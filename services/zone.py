import random
import uuid
from typing import Dict, Set, Type

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Game, GameStatus, Player, PlayerAbility, ZoneType, GameZone, AbilityType, Ability, Role, VictoryConditionType, EffectType
from sqlalchemy import select, or_
from datetime import datetime, timezone, timedelta

from services.base import BaseService
from services.ability_rules import forbidden_abilities_for
from services.timers import timer_manager, TimerType
from utils.conversions import to_dict
from utils.geo import is_point_in_circle
from services.websocket_manager import connection_manager


async def _on_zone_expired_callback(game_id: uuid.UUID, zone_id: uuid.UUID):
    """Callback для таймера; создаёт новую сессию и вызывает обработчик."""
    # Импортируем здесь, чтобы избежать циклических зависимостей
    print("[DEBUG] END ZONE TIMER")
    from database.db import get_db
    async for db in get_db():
        service = ZoneService(db)
        await service.handle_zone_expired(game_id, zone_id)
        break


class ZoneService(BaseService):
    """Сервис для создания, проверки и завершения игровых зон."""

    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self._player_zones_cache: Dict[uuid.UUID, Set[uuid.UUID]] = {}

    async def activate_safe_zone(
        self,
        game_id: uuid.UUID
    ):
        stmt = select(GameZone).where(
            GameZone.game_id == game_id,
            GameZone.type == ZoneType.SAFE
        )
        game = await self.db.get(Game, game_id)
        result = await self.db.execute(stmt)
        safe_zone = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)

        duration_seconds = game.game_duration

        """активация safe зоны"""
        await timer_manager.safe_zone_schedule(
            game_id=game_id,
            safe_zone=safe_zone,
            end_time=now + timedelta(seconds=duration_seconds),
            db=self.db
        )


    async def create_zone(
        self,
        game_id: uuid.UUID,
        zone_type: ZoneType,
        center_lat: float,
        center_lng: float,
        duration_seconds: int | None,
        radius: float,
        damage: int | None = None,
        creator_id: uuid.UUID | None = None,
    ) -> GameZone:
        """Создаёт новую зону и планирует её завершение."""

        print("[DEBUG CREATE ZONE]: ")
        now = datetime.now(timezone.utc)

        zone = GameZone(
            game_id=game_id,
            type=zone_type,
            center_lat=center_lat,
            center_lng=center_lng,
            radius=radius,
            starts_at=now,
            ends_at=now + timedelta(seconds=duration_seconds) if duration_seconds is not None else None,
            damage=int(damage) if damage is not None else None,
            created_by=creator_id,
            is_active=True
        )
        self.db.add(zone)
        await self.db.flush()  # получаем zone.id

        # Ловушки видны только участникам стороны установившего их игрока.
        players = (await self.db.execute(select(Player, Role.victory_condition)
                   .join(Role, Player.role_id == Role.id).where(Player.game_id == game_id))).all()
        creator_side = next((side for player, side in players if player.id == creator_id), None)
        for recipient, side in players:
            if zone_type in (ZoneType.TRAP, ZoneType.SNARE) and (creator_side is None or side != creator_side):
                continue
            visible_type = (ZoneType.DANGER if zone_type in (ZoneType.SAFE_HOUSE, ZoneType.SAFE_MANSION)
                            and side == VictoryConditionType.SEEKER else zone_type)
            remaining_seconds = (int((zone.ends_at - now).total_seconds())
                                 if zone.ends_at is not None else None)
            await connection_manager.send_personal({'type': 'create_zone', 'data': {
                'zone_id': str(zone.id), 'zone_type': visible_type.value,
                'center_lat': center_lat, 'center_lng': center_lng, 'radius': radius,
                # None означает, что зона не привязана ко времени и таймер не нужен.
                'remaining_seconds': remaining_seconds
            }}, recipient.id)
        print(f"[DEBUG] zone.ends_at: {zone.ends_at}")
        if zone.ends_at is not None:
            # Планируем завершение
            print("[DEBUG] START ZONE TIMER")
            await timer_manager.schedule(
                game_id=game_id,
                entity_type=TimerType.ZONE,
                entity_id=zone.id,
                end_time=zone.ends_at,
                callback=lambda: _on_zone_expired_callback(game_id, zone.id)
            )

        return zone

    async def handle_zone_expired(self, game_id: uuid.UUID, zone_id: uuid.UUID):
        """Обрабатывает истечение зоны: деактивирует, применяет эффекты к игрокам внутри."""
        zone = await self.db.get(GameZone, zone_id)
        if not zone or not zone.is_active:
            return

        zone.is_active = False
        self.db.add(zone)

        # Находим всех игроков в игре (можно оптимизировать запрос)
        game = await self.db.get(Game, game_id)
        if not game:
            return
        if game.status != GameStatus.ACTIVE:
            await self.db.commit()
            return

        stmt = select(Player).where(Player.game_id == game_id, Player.is_alive == True)
        result = await self.db.execute(stmt)
        players = result.scalars().all()

        from services.player import PlayerService
        player_service = PlayerService(self.db)
        await connection_manager.broadcast_to_game(game_id=game_id, message={
            "type": "delete_zone", "data": {"zone_id": str(zone.id)}
        })
        for player in players:
            if player.location_lat is None or player.location_lng is None:
                continue
            if is_point_in_circle((player.location_lat, player.location_lng), (zone.center_lat, zone.center_lng), zone.radius):
                await self._apply_zone_effect_to_player(player, zone, player_service)

        await self.db.commit()

    async def _apply_zone_effect_to_player(self, player, zone: Type[GameZone] | None, player_service):
        """Применяет эффект зоны к конкретному игроку."""
        if zone.type == ZoneType.DANGER:
            # Красная зона убивает, если нет щита
            await player_service.apply_damage(player.game_id, player.id, zone.damage, ignore_shield=False)
        elif zone.type == ZoneType.WARNING:
            await player_service.apply_damage(player.game_id, player.id, zone.damage, ignore_shield=False)
        elif zone.type == ZoneType.AIRDROP:
            # Проверяем, не был ли уже аирдроп забран
            if zone.zone_data.get("is_claimed", False):
                return
            # Отмечаем зону как забранную, чтобы другие игроки не получили способность
            zone.zone_data = {**zone.zone_data, "is_claimed": True}
            zone.is_active = False
            # Выбираем случайную сильную способность, доступную стороне игрока.
            strong_ability_types = [AbilityType.SAFE_MANSION, AbilityType.SCAN, AbilityType.TRAP]
            role = await self.db.get(Role, player.role_id)
            forbidden = forbidden_abilities_for(role.victory_condition) if role else set()
            allowed_types = [ability for ability in strong_ability_types if ability not in forbidden]
            chosen_type = random.choice(allowed_types or [AbilityType.SCAN])
            # Ищем соответствующую способность в БД (по ability_type)
            # Если такая способность уже есть у игрока, увеличиваем её запас,
            # а не создаём вторую запись того же типа.
            stmt = (select(Ability).join(PlayerAbility, PlayerAbility.ability_id == Ability.id)
                    .where(PlayerAbility.player_id == player.id, Ability.ability_type == chosen_type))
            result = await self.db.execute(stmt)
            ability = result.scalars().first()
            if not ability:
                # Если такой способности нет в БД – создаём (можно с параметрами по умолчанию)
                ability = Ability(
                    ability_type=chosen_type,
                    recharge_time=60,
                    number_uses=1,
                    duration_seconds=120,
                    data={"radius": 10, "trap_duration_seconds": 60}
                )
                self.db.add(ability)
                await self.db.flush()
            # Добавляем способность игроку
            player_ability = await player_service.add_ability(player.game_id, player.id, ability.id, number_uses=1)
            # Обновляем запись зоны (чтобы сохранить флаг is_claimed)
            self.db.add(zone)
            await self.db.commit()
            # Уведомить игрока о получении способности (отдельным сообщением)
            ability_data = to_dict(ability)
            ability_data['number_uses'] = player_ability.number_uses_left
            await connection_manager.send_personal({
                "type": "airdrop_collected",
                "data": {"ability": ability_data}
            }, player.id)
            await connection_manager.broadcast_to_game(player.game_id, {'type': 'delete_zone', 'data': {'zone_id': str(zone.id)}})

        elif zone.type in (ZoneType.TRAP, ZoneType.SNARE):
            # Капкан или ловушка — накладываем эффект обездвиживания
            if zone.created_by == player.id:
                return
            role = await self.db.get(Role, player.role_id)
            if not role or role.victory_condition != VictoryConditionType.HIDER:
                return
            trap_duration = max(1, int((zone.zone_data or {}).get("trap_duration_seconds", 60)))
            effect = await player_service.apply_effect(player.game_id, player.id, EffectType.TRAPPED, trap_duration)
            effect.data = {'zone_id': str(zone.id), 'center_lat': zone.center_lat,
                           'center_lng': zone.center_lng, 'radius': zone.radius,
                           'zone_type': zone.type.value}
            zone.is_active = False
            self.db.add(zone)
            await connection_manager.broadcast_to_game(player.game_id, {'type': 'delete_zone', 'data': {'zone_id': str(zone.id)}})
            trap_data = {'effect_id': str(effect.id), 'zone_type': zone.type.value,
                         'center_lat': zone.center_lat, 'center_lng': zone.center_lng,
                         'radius': zone.radius, 'remaining_seconds': trap_duration,
                         'player_name': player.name}
            await connection_manager.send_personal({'type': 'trap_activated',
                'data': {**trap_data, 'is_victim': True}}, player.id)
            seekers = (await self.db.execute(select(Player).join(Role, Player.role_id == Role.id)
                        .where(Player.game_id == player.game_id, Role.victory_condition == VictoryConditionType.SEEKER))).scalars().all()
            for seeker in seekers:
                await connection_manager.send_personal({'type': 'trap_activated',
                    'data': {**trap_data, 'is_victim': False}}, seeker.id)

        # Защитные зоны не должны наносить урон при исчезновении.

    async def get_active_zones(self, game_id: uuid.UUID) -> list[GameZone]:
        """Возвращает все активные зоны игры."""
        now = datetime.now(timezone.utc)
        stmt = select(GameZone).where(
            GameZone.game_id == game_id,
            GameZone.is_active == True,
            GameZone.starts_at <= now,
            or_(GameZone.ends_at.is_(None), GameZone.ends_at > now)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def check_player_in_zones(self, game_id: uuid.UUID, player_id: uuid.UUID):
        """
            Проверяет, в каких зонах находится игрок, и отправляет уведомления о входе/выходе.
            Эффекты зон (ловушки, аирдроп) применяются при входе (один раз).
            """
        from services.player import PlayerService
        # Используем ту же транзакцию, где только что обновились координаты.
        player_service = PlayerService(self.db)
        player = await player_service.get_player_in_game(game_id, player_id)
        if not player or not player.is_alive:
            return
        zones = await self.get_active_zones(game_id)
        for zone in zones:
            if zone.type not in (ZoneType.TRAP, ZoneType.SNARE, ZoneType.AIRDROP):
                continue
            if is_point_in_circle((player.location_lat, player.location_lng),
                                  (zone.center_lat, zone.center_lng), zone.radius):
                await self._apply_zone_effect_to_player(player, zone, player_service)

    def clear_player_cache(self, player_id: uuid.UUID):
        self._player_zones_cache.pop(player_id, None)

