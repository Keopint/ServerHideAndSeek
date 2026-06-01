import random
import uuid
from typing import Dict, Set

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Game, Player, ZoneType, GameZone, AbilityType, Ability, Role, VictoryConditionType
from sqlalchemy import select
from datetime import datetime, timezone, timedelta

from services.base import BaseService
from services.timers import timer_manager, TimerType
from utils.conversions import to_dict
from utils.geo import is_point_in_circle
from services.websocket_manager import connection_manager


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
        duration_seconds: int,
        radius: float,
        damage: int | None = None,
        creator_id: uuid.UUID | None = None,
    ) -> GameZone:
        """Создаёт новую зону и планирует её завершение."""
        now = datetime.now(timezone.utc)

        zone = GameZone(
            game_id=game_id,
            type=zone_type,
            center_lat=center_lat,
            center_lng=center_lng,
            radius=radius,
            starts_at=now,
            ends_at=now + timedelta(seconds=duration_seconds),
            damage=damage,
            created_by=creator_id,
            is_active=True
        )
        self.db.add(zone)
        await self.db.flush()  # получаем zone.id

        connection_manager.broadcast_to_game(
            game_id=game_id,
            message={
                "type": "create_zone",
                "data": {
                    "zone_id": zone.id,
                    "zone_type": str(zone_type),
                    "center_lat": center_lat,
                    "center_lng": center_lng,
                    "radius": radius
                }
            }
        )

        # Планируем завершение
        await timer_manager.schedule(
            game_id=game_id,
            entity_type=TimerType.ZONE,
            entity_id=zone.id,
            end_time=zone.ends_at,
            callback=lambda: self._on_zone_expired_callback(game_id, zone.id)
        )

        return zone

    async def _on_zone_expired_callback(self, game_id: uuid.UUID, zone_id: uuid.UUID):
        """Callback для таймера; создаёт новую сессию и вызывает обработчик."""
        # Импортируем здесь, чтобы избежать циклических зависимостей
        from database.db import get_db
        async for db in get_db():
            service = ZoneService(db)
            await service.handle_zone_expired(game_id, zone_id)
            break

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

        stmt = select(Player).where(Player.game_id == game_id, Player.is_alive == True)
        result = await self.db.execute(stmt)
        players = result.scalars().all()

        from services.player import PlayerService
        from database.db import get_db
        async for db in get_db():
            player_service = PlayerService(db)

            connection_manager.broadcast_to_game(
                game_id=game_id,
                message={
                    "type": "delete_zone",
                    "data": {
                        "zone_id": zone.id,
                    }
                }
            )

            for player in players:
                if player.lat is None or player.lng is None:
                    continue
                if is_point_in_circle((player.lat, player.lng), (zone.center_lat, zone.center_lng), zone.radius):
                    # Игрок внутри зоны
                    await self._apply_zone_effect_to_player(player, zone, player_service)

        await self.db.commit()

    async def _apply_zone_effect_to_player(self, player, zone: GameZone, player_service):
        """Применяет эффект зоны к конкретному игроку."""
        if zone.type == ZoneType.DANGER:
            # Красная зона убивает, если нет щита
            await player_service.apply_damage(player.game_id, player.id, zone.get("damage", 100), ignore_shield=False)
        elif zone.type == ZoneType.WARNING:
            await player_service.apply_damage(player.game_id, player.id, zone.get("damage", 50), ignore_shield=False)
        elif zone.type == ZoneType.AIRDROP:
            # Проверяем, не был ли уже аирдроп забран
            if zone.zone_data.get("is_claimed", False):
                return
            # Отмечаем зону как забранную, чтобы другие игроки не получили способность
            zone.zone_data["is_claimed"] = True
            # Выбираем случайную сильную способность
            strong_ability_types = [AbilityType.SAFE_MANSION, AbilityType.SCAN, AbilityType.TRAP]
            chosen_type = random.choice(strong_ability_types)
            # Ищем соответствующую способность в БД (по ability_type)
            stmt = select(Ability).where(Ability.ability_type == chosen_type)
            result = await self.db.execute(stmt)
            ability = result.scalar_one_or_none()
            if not ability:
                # Если такой способности нет в БД – создаём (можно с параметрами по умолчанию)
                ability = Ability(
                    ability_type=chosen_type,
                    recharge_time=60,
                    number_uses=1,
                    duration_seconds=None,
                    data={}
                )
                self.db.add(ability)
                await self.db.flush()
            # Добавляем способность игроку
            await player_service.add_ability(player.game_id, player.id, ability.id, number_uses=1)
            # Обновляем запись зоны (чтобы сохранить флаг is_claimed)
            self.db.add(zone)
            await self.db.commit()
            # Уведомить игрока о получении способности (отдельным сообщением)
            await connection_manager.send_personal({
                "type": "airdrop_collected",
                "data": {"ability": to_dict(ability)}
            }, player.id)
            await player_service.add_ability(player.game_id, player.id, ability.id)

        elif zone.type in (ZoneType.TRAP, ZoneType.SNARE):
            # Капкан или ловушка — накладываем эффект обездвиживания
            trap_duration = zone.zone_data.get("trap_duration_seconds")
            from services.effect import EffectService
            effect_service = EffectService(self.db)
            await effect_service.apply_trapped_effect(
                player_id=player.id,
                game_id=player.game_id,
                zone_id=zone.id,
                duration_seconds=trap_duration,
                single_use=zone.single_use
            )
            if zone.single_use:
                zone.is_active = False
                self.db.add(zone)

        elif zone.type in (ZoneType.SAFE_MANSION, ZoneType.SAFE_HOUSE):
            player_role = await self.db.get(Role, player.role_id)
            if player_role.victory_condition == VictoryConditionType.HIDER:
                await player_service.apply_damage(player.game_id, player.id, zone.get("damage", 10), ignore_shield=False)

    async def get_active_zones(self, game_id: uuid.UUID) -> list[GameZone]:
        """Возвращает все активные зоны игры."""
        now = datetime.now(timezone.utc)
        stmt = select(GameZone).where(
            GameZone.game_id == game_id,
            GameZone.is_active == True,
            GameZone.starts_at <= now,
            GameZone.ends_at > now
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def check_player_in_zones(self, game_id: uuid.UUID, player_id: uuid.UUID):
        """
            Проверяет, в каких зонах находится игрок, и отправляет уведомления о входе/выходе.
            Эффекты зон (ловушки, аирдроп) применяются при входе (один раз).
            """
        from database.db import get_db
        from services.player import PlayerService

        async for db in get_db():
            player_service = PlayerService(db)

            player = await player_service.get_player_in_game(game_id, player_id)
            if not player or not player.is_alive:
                return

            # Получаем все активные зоны игры
            zones = await self.get_active_zones(game_id)
            current_zone_ids = set()

            for zone in zones:
                if is_point_in_circle(
                        (player.location_lat, player.location_lng),
                        (zone.center_lat, zone.center_lng),
                        zone.radius
                ):
                    current_zone_ids.add(zone.id)

            # Предыдущие зоны игрока
            prev_zone_ids = self._player_zones_cache.get(player_id, set())

            # Определяем вошедшие и вышедшие зоны
            entered_zones = current_zone_ids - prev_zone_ids
            exited_zones = prev_zone_ids - current_zone_ids

            # Уведомления о входе и применение эффектов (один раз)
            for zone_id in entered_zones:
                zone = next(z for z in zones if z.id == zone_id)
                # Личное сообщение игроку
                await connection_manager.send_personal({
                    "type": "player_entered_zone",
                    "data": {
                        "zone_id": str(zone.id),
                        "zone_type": zone.type.value,
                        "center_lat": zone.center_lat,
                        "center_lng": zone.center_lng,
                        "radius": zone.radius
                    }
                }, player_id)

                # Мгновенные эффекты при входе
                if zone.type in (ZoneType.TRAP, ZoneType.SNARE, ZoneType.AIRDROP):
                    await self._apply_zone_effect_to_player(player, zone, player_service)

            # Уведомления о выходе
            for zone_id in exited_zones:
                zone = next(z for z in zones if z.id == zone_id)
                await connection_manager.send_personal({
                    "type": "player_exited_zone",
                    "data": {
                        "zone_id": str(zone.id),
                        "zone_type": zone.type.value
                    }
                }, player_id)

            # Обновляем кэш
            self._player_zones_cache[player_id] = current_zone_ids

    def clear_player_cache(self, player_id: uuid.UUID):
        self._player_zones_cache.pop(player_id, None)

