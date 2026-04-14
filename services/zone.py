import uuid
from database.models import Game, Player, ZoneType, GameZone, Zone
from sqlalchemy import select
from datetime import datetime, timezone, timedelta

from services.base import BaseService
from timers import timer_manager, TimerType
from utils.geo import is_point_in_circle
from services.player import PlayerService


class ZoneService(BaseService):
    """Сервис для создания, проверки и завершения игровых зон."""

    async def create_zone(
        self,
        game_id: uuid.UUID,
        zone_type: ZoneType,
        center_lat: float,
        center_lng: float,
        creator_id: uuid.UUID | None = None,
    ) -> GameZone:
        """Создаёт новую зону и планирует её завершение."""
        now = datetime.now(timezone.utc)

        zone = select(Zone).where(
            Zone.game_id == game_id,
            Zone.type == zone_type
        ).scalar_one_or_none()

        zone = GameZone(
            game_id=game_id,
            type=zone_type,
            center_lat=center_lat,
            center_lng=center_lng,
            radius=zone.radius,
            starts_at=now,
            ends_at=now + timedelta(seconds=zone.duration_seconds),
            created_by=creator_id,
            is_active=True
        )
        self.db.add(zone)
        await self.db.flush()  # получаем zone.id

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
        player_service = PlayerService(self.db)

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
            await player_service.apply_damage(player.id, damage=1000, ignore_shield=False)
        elif zone.type == ZoneType.WARNING:
            if zone.target_player_id == player.id:
                await player_service.apply_damage(player.id, damage=1000, ignore_shield=False)
        elif zone.type in (ZoneType.TRAP, ZoneType.SNARE):
            # Капкан или ловушка — накладываем эффект обездвиживания
            trap_duration = 60 if zone.type == ZoneType.TRAP else 600
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
        """Проверяет, в каких зонах находится игрок, и применяет эффекты (для мгновенных зон)."""
        # Можно вызывать при обновлении локации
        player_service = PlayerService(self.db)
        player = await player_service.get_player_in_game(game_id, player_id)
        if not player or not player.is_alive:
            return

        zones = await self.get_active_zones(game_id)
        for zone in zones:
            if player.lat is None or player.lng is None:
                continue
            if is_point_in_circle((player.lat, player.lng), (zone.center_lat, zone.center_lng), zone.radius):
                # Для красных зон может быть мгновенный эффект, но в нашей механике эффект наступает по истечении.
                # Однако можно добавить логику "входа" (например, мгновенный капкан).
                if zone.type in (ZoneType.TRAP, ZoneType.SNARE):
                    # При входе в капкан эффект применяется сразу
                    await self._apply_zone_effect_to_player(player, zone, player_service)

