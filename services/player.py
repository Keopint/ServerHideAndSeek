import uuid

from starlette.websockets import WebSocket

from database.models import (Game, Player, GameStatus, PlayerEffect,
                             EffectType, AbilityType, PlayerAbility, Ability, ZoneType)
from sqlalchemy import select
from datetime import datetime, timezone, timedelta

from services.base import BaseService
from timers import TimerType, timer_manager
from utils.geo import calculate_distance, validate_coordinates
from websocket_manager import connection_manager
from zone import ZoneService


class PlayerService(BaseService):
    """Сервис для операций с игроком: обновление локации, получение информации, проверка состояния."""

    async def get_player(self, player_id: uuid.UUID) -> Player | None:
        return await self.db.get(Player, player_id)

    async def get_player_in_game(self, game_id: uuid.UUID, player_id: uuid.UUID) -> Player | None:
        """Возвращает игрока, только если он принадлежит указанной игре."""
        stmt = select(Player).where(
            Player.id == player_id,
            Player.game_id == game_id
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_player_location(
        self,
        game_id: uuid.UUID,
        player_id: uuid.UUID,
        lat: float,
        lng: float
    ) -> Player:
        """Обновляет геолокацию игрока с проверками."""
        # Валидация координат
        if not validate_coordinates(lat, lng):
            raise ValueError("Invalid latitude or longitude")

        # Получаем игрока и игру
        player = await self.get_player_in_game(game_id, player_id)
        if not player:
            raise ValueError(f"Player {player_id} not found in game {game_id}")

        game = await self.db.get(Game, game_id)
        if not game:
            raise ValueError(f"Game {game_id} not found")
        if game.status not in (GameStatus.ACTIVE, GameStatus.WAITING):
            raise ValueError("Game is not active")

        # Проверяем, не находится ли игрок под действием эффекта, запрещающего движение
        if await self.is_movement_restricted(player_id):
            raise ValueError("Player is trapped and cannot move")

        # Сохраняем старые координаты (для возможного отката, но проще просто обновить)
        player.lat = lat
        player.lng = lng
        player.last_location_update = datetime.now(timezone.utc)

        # Проверка выхода за границы игровой зоны (если заданы)
        if game.center_lat is not None and game.center_lng is not None and game.radius is not None:
            distance = calculate_distance(lat, lng, game.center_lat, game.center_lng)
            if distance > game.radius:
                # Наносим урон за выход
                await self._apply_boundary_damage(player, game)

        zone_service = ZoneService(self.db)
        await zone_service.check_player_in_zones()

        self.db.add(player)
        return player

    async def is_movement_restricted(self, player_id: uuid.UUID) -> bool:
        """Проверяет, есть ли у игрока активный эффект, запрещающий движение."""
        now = datetime.now(timezone.utc)
        stmt = select(PlayerEffect).where(
            PlayerEffect.player_id == player_id,
            PlayerEffect.is_active == True,
            PlayerEffect.starts_at <= now,
            PlayerEffect.ends_at > now,
            PlayerEffect.type.in_([EffectType.TRAPPED, EffectType.ROOTED])
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _apply_boundary_damage(self, player: Player, game: Game):
        """Наносит урон игроку за выход за границы игровой зоны."""
        damage = game.zone_boundary_damage
        player.health -= damage
        if player.health <= 0:
            player.is_alive = False
            player.health = 0
        self.db.add(player)

    async def apply_damage(self, player_id: uuid.UUID, damage: int, ignore_shield: bool = False) -> Player:
        """Наносит урон игроку, учитывая возможный щит."""
        player = await self.get_player(player_id)
        if not player or not player.is_alive:
            return player

        if not ignore_shield and await self.has_active_shield(player_id):
            # Щит поглощает урон и снимается
            await self.consume_shield(player_id)
            return player

        player.health -= damage
        if player.health <= 0:
            player.is_alive = False
            player.health = 0
        self.db.add(player)
        return player

    async def has_active_shield(self, player_id: uuid.UUID) -> bool:
        now = datetime.now(timezone.utc)
        stmt = select(PlayerEffect).where(
            PlayerEffect.player_id == player_id,
            PlayerEffect.type == EffectType.SHIELD,
            PlayerEffect.is_active == True,
            PlayerEffect.starts_at <= now,
            PlayerEffect.ends_at > now
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def consume_shield(self, player_id: uuid.UUID):
        """Снимает активный щит с игрока."""
        now = datetime.now(timezone.utc)
        stmt = select(PlayerEffect).where(
            PlayerEffect.player_id == player_id,
            PlayerEffect.type == EffectType.SHIELD,
            PlayerEffect.is_active == True
        )
        result = await self.db.execute(stmt)
        shield = result.scalar_one_or_none()
        if shield:
            shield.is_active = False
            self.db.add(shield)

    async def use_ability(
            self,
            game_id: uuid.UUID,
            player_id: uuid.UUID,
            ability_type: AbilityType,
            lat: float = None,
            lng: float = None) -> int:
        player = self.get_player_in_game(game_id, player_id)
        stmt = select(Ability).join(PlayerAbility).where(
            PlayerAbility.player_id == player_id,
            Ability.type == ability_type
        )
        result = await self.db.execute(stmt)
        ability = result.scalar_one_or_none()

        if ability.number_uses_left <= 0:
            raise ValueError(f"Player {player_id} has no more abilities {ability_type} left")

        ability.number_uses_left -= 1

        if ability_type == AbilityType.SHIELD:
            await self.apply_effect(game_id, player_id, EffectType.SHIELD, ability.duration_seconds)
        elif ability_type == AbilityType.SCAN:
            await self.apply_effect(game_id, player_id, EffectType.INTEL, ability.duration_seconds)
        elif ability_type == AbilityType.TRAP:
            zone_service = ZoneService(self.db)
            await zone_service.create_zone(game_id, ZoneType.TRAP, lat, lng, player_id)
        elif ability_type == AbilityType.PERSONAL_BOMB:
            zone_service = ZoneService(self.db)
            await zone_service.create_zone(game_id, ZoneType.PERSONAL_BOMB, lat, lng, player_id)
        elif ability_type == AbilityType.SAFE_HOUSE:
            zone_service = ZoneService(self.db)
            await zone_service.create_zone(game_id, ZoneType.SAFE_HOUSE, lat, lng, player_id)
        elif ability_type == AbilityType.SAFE_MANSION:
            zone_service = ZoneService(self.db)
            await zone_service.create_zone(game_id, ZoneType.SAFE_MANSION, lat, lng, player_id)
        elif ability_type == AbilityType.INTEL:
            await self.handle_scan_effect(game_id, player_id, ability.duration_seconds, connection_manager)
        elif ability_type == AbilityType.SAFE_HOUSE:
            zone_service = ZoneService(self.db)
            await zone_service.create_zone(game_id, ZoneType.SAFE_HOUSE, lat, lng, player_id)
        return 0

    async def handle_scan_effect(self, game_id: uuid.UUID, player_id: uuid.UUID, duration: int, websocket: WebSocket):
        await self.apply_effect(game_id, player_id, EffectType.SCAN, duration)
        await websocket.send_json({
            "type": "scan_activated",
            "duration": duration,
            "ends_at": (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat()
        })

    async def apply_effect(self, game_id: uuid.UUID, player_id, effect_type: EffectType, duration_seconds: int):
        now = datetime.now(timezone.utc)

        new_player_effect = PlayerEffect(
            player_id=player_id,
            type=effect_type,
            starts_at=now,
            ends_at=now + timedelta(seconds=duration_seconds),
            is_active=True
        )

        self.db.add(new_player_effect)
        await self.db.flush()

        timer_manager.schedule(
            game_id=game_id,
            entity_type=TimerType.EFFECT,
            entity_id=new_player_effect.id,
            end_time=new_player_effect.ends_at,
            callback=lambda: self._on_effect_expired_callback(game_id, new_player_effect.id)
        )

    async def _on_effect_expired_callback(self, game_id: uuid.UUID, effect_id: uuid.UUID):
        """Callback для таймера; создаёт новую сессию и вызывает обработчик."""
        # Импортируем здесь, чтобы избежать циклических зависимостей
        from database.db import get_db
        async for db in get_db():
            await self.handle_effect_expired(game_id, effect_id)
            break


    async def handle_effect_expired(self, game_id: uuid.UUID, effect_id: uuid.UUID):
        """Обрабатывает истечение эффекта"""
        effect = await self.db.get(PlayerEffect, effect_id)
        if not effect:
            return
        effect.is_active = False
        self.db.add(effect)

        await self.db.commit()