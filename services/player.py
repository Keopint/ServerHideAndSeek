import uuid

from sqlalchemy.orm import selectinload

from database.models import (Game, Player, GameStatus, PlayerEffect,
                             EffectType, AbilityType, PlayerAbility, Ability, ZoneType, Role, PlayerDeathCauses,
                             GameZone, role_abilities, game_roles, VictoryConditionType)
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
from services.zone import ZoneService
from services.base import BaseService
from services.timers import TimerType, timer_manager
from utils.conversions import to_dict
from utils.geo import calculate_distance, validate_coordinates
from services.websocket_manager import connection_manager


class PlayerService(BaseService):
    """Сервис для операций с игроком: обновление локации, получение информации, проверка состояния."""

    async def get_player(self, player_id: uuid.UUID) -> Player | None:
        return await self.db.get(Player, player_id)

    async def get_player_in_game(self, game_id: uuid.UUID, player_id: uuid.UUID) -> Player | None:
        """Возвращает игрока, только если он принадлежит указанной игре."""
        # stmt = select(Game).where(Game.id == game_id).options(
        #     selectinload(Game.roles).selectinload(Role.abilities),
        #     selectinload(Game.events),
        #     selectinload(Game.players).selectinload(Player.role_ref)
        # ).execution_options(populate_existing=True)
        # result = await self.db.execute(stmt)
        # game_with_relations = result.scalar_one()

        stmt = select(Player).where(
            Player.id == player_id,
            Player.game_id == game_id
        ).options(selectinload(Player.role_ref).selectinload(Role.abilities)
        ).execution_options(populate_existing=True)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_players_in_game(self, game_id: uuid.UUID):
        stmt = select(Player).where(
            Player.game_id == game_id
        )
        players = (await self.db.execute(stmt)).scalars().all()
        return players

    async def get_active_player_effects(self, player_id: uuid.UUID) -> list[PlayerEffect]:
        """
        Возвращает список активных эффектов для указанного игрока.
        Активными считаются эффекты с is_active=True, starts_at <= now <= ends_at.
        """
        now = datetime.now(timezone.utc)
        stmt = select(PlayerEffect).where(
            PlayerEffect.player_id == player_id,
            PlayerEffect.is_active == True,
            PlayerEffect.starts_at <= now,
            PlayerEffect.ends_at > now
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

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

        if game.safe_zone_center_lat is not None and game.safe_zone_center_lng is not None and game.safe_zone_radius is not None:
            distance = calculate_distance(lat, lng, game.safe_zone_center_lat, game.safe_zone_center_lng)
            if distance > game.safe_zone_radius:
                # Наносим урон за выход
                await self._apply_boundary_damage(player, game)
        if game.safe_zone_center_lat is not None and game.safe_zone_center_lng is not None and game.safe_zone_radius is not None:
            player_active_effects = await self.get_active_player_effects(player_id)
            has_trap_effect = any(
                effect.type in (EffectType.TRAPPED, EffectType.ROOTED) for effect in player_active_effects)

            # 2. Активные зоны-ловушки (TRAP или SNARE)
            now = datetime.now(timezone.utc)
            trap_zones_query = select(GameZone).where(
                GameZone.game_id == game_id,
                GameZone.type.in_([ZoneType.TRAP, ZoneType.SNARE]),
                GameZone.is_active == True,
                GameZone.starts_at <= now,
                (GameZone.ends_at == None) | (GameZone.ends_at > now)
            )
            trap_zones = (await self.db.execute(trap_zones_query)).scalars().all()

            # Проверяем, находится ли игрок в любой из этих зон
            is_in_trap_zone = False
            for zone in trap_zones:
                distance = calculate_distance(player.location_lat, player.location_lng,
                                              zone.center_lat, zone.center_lng)
                if distance <= zone.radius:
                    is_in_trap_zone = True
                    break

            # 3. Если игрок не защищён ни эффектом, ни нахождением в зоне-ловушке
            if not has_trap_effect and not is_in_trap_zone:
                # Проверка выхода за границу безопасной зоны
                distance_to_safe_center = calculate_distance(
                    player.location_lat, player.location_lng,
                    game.safe_zone_center_lat, game.safe_zone_center_lng
                )
                if distance_to_safe_center > game.safe_zone_radius:
                    # Игрок выбывает
                    player.is_alive = False
                    player_service = PlayerService(self.db)
                    await player_service.player_died(game_id, player_id, death_cause=PlayerDeathCauses.LEAVE_TRAP)

        zone_service = ZoneService(self.db)
        await zone_service.check_player_in_zones(game_id, player_id)

        self.db.add(player)
        return player

    async def change_player_role(
            self,
            game_id: uuid.UUID,
            player_id: uuid.UUID,
            new_role_id: uuid.UUID
    ):
        # Получаем игрока с предзагрузкой (опционально)
        player = await self.get_player_in_game(game_id, player_id)
        if not player:
            raise ValueError("Player not found in game")
        # Проверяем существование новой роли
        role = await self.db.get(Role, new_role_id)
        if not role:
            raise ValueError("Role not found")
        # Обновляем роль
        player.role_id = new_role_id
        # Дополнительно можно обновить здоровье в соответствии с ролью
        player.health = role.health
        await self.db.commit()

    async def add_ability(
            self,
            game_id: uuid.UUID,
            player_id: uuid.UUID,
            ability_id: uuid.UUID,
            number_uses: int = 1
    ) -> PlayerAbility:
        """
            Добавляет способность игроку или увеличивает количество использований, если способность уже есть.
            Возвращает существующий или созданный объект PlayerAbility.
            """
        # Проверяем существование игрока
        try:
            player = await self.get_player_in_game(game_id, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found in game {game_id}")

            # Проверяем существование способности
            ability = await self.db.get(Ability, ability_id)
            if not ability:
                raise ValueError(f"Ability {ability_id} not found")

            # Ищем существующую запись PlayerAbility для этого игрока и способности
            from sqlalchemy import select
            stmt = select(PlayerAbility).where(
                PlayerAbility.player_id == player_id,
                PlayerAbility.ability_id == ability_id
            )
            result = await self.db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                # Если уже есть, увеличиваем количество использований
                existing.number_uses_left += number_uses
                self.db.add(existing)
                await self.db.flush()
                return existing
            else:
                # Создаём новую запись
                player_ability = PlayerAbility(
                    player_id=player_id,
                    ability_id=ability_id,
                    number_uses_left=number_uses
                )
                self.db.add(player_ability)
                await self.db.flush()
                return player_ability
        except Exception:
            await self.db.rollback()
            raise

    async def change_ready_status(
            self,
            game_id: uuid.UUID,
            player_id: uuid.UUID,
            new_ready_status: bool
    ):
        try:
            player = await self.get_player_in_game(game_id, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found in game {game_id}")
            player.is_player_ready = new_ready_status
            self.db.add(player)
            return player
        except Exception:
            await self.db.rollback()
            raise

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
        try:
            damage = game.zone_boundary_damage
            player.health -= damage
            if player.health <= 0:
                player.is_alive = False
                player.health = 0
            self.db.add(player)
        except Exception:
            await self.db.rollback()
            raise

    async def apply_damage(self, game_id: uuid.UUID, player_id: uuid.UUID, damage: int, ignore_shield: bool = False) -> Player:
        """Наносит урон игроку, учитывая возможный щит."""
        try:
            player = await self.get_player(player_id)
            if not player or not player.is_alive:
                return player

            if not ignore_shield and await self.has_active_shield(player_id):
                # Щит поглощает урон и снимается
                await self.consume_shield(player_id)
                return player

            player.health -= damage
            if player.health <= 0:
                player.health = 0
                await self.player_died(game_id, player_id, PlayerDeathCauses.HP_ARE_OVER)
            self.db.add(player)
            await connection_manager.send_personal(
                player_id=player_id,
                message={
                    "type": "apply_damage",
                    "data": {
                        "damage": damage
                    }
                }
            )
            return player
        except Exception:
            await self.db.rollback()
            raise

    async def player_died(self, game_id: uuid.UUID, player_id: uuid.UUID, death_cause: PlayerDeathCauses, hunter_player_id: uuid.UUID = None):
        # 1. Получаем игрока из БД
        player = await self.db.get(Player, player_id)
        if not player or not player.is_alive:
            return

        # 2. Обновляем состояние
        player.is_alive = False
        await self.db.commit()

        # 3. Отправляем личное сообщение умершему игроку (он закроет сокет)
        await connection_manager.send_personal(
            {
                "type": "you_died",
                "data": {
                    "reason": str(death_cause.value),
                    "hunter_player_id": str(hunter_player_id) if hunter_player_id else None,
                }
            },
            player_id
        )

        # 4. Оповещаем остальных игроков
        message = {
            "type": "player_died",
            "data": {
                "reason": str(death_cause.value),
                "player_id": str(player_id),
            }
        }
        if death_cause == PlayerDeathCauses.HUNTER_FOUND_PLAYER:
            message["data"]["hunter_player_id"] = str(hunter_player_id)

        await connection_manager.broadcast_to_game(
            game_id,
            message,
            exclude_player=player_id
        )

        # 5. Удаляем соединение из менеджера (чтобы сервер не слал ему новые сообщения)

        connection_manager.disconnect(game_id, player_id)

        player_service = PlayerService(self.db)
        players = await player_service.get_players_in_game(game_id)

        cnt_hiders = 0
        for player in players:
            role = await self.db.get(Role, player.role_id)
            if player.is_alive and role.victory_condition == VictoryConditionType.HIDER:
                cnt_hiders += 1
        if cnt_hiders == 0:
            from services.game_management import GameService
            game_service = GameService(self.db)
            await game_service.finish_game(game_id=game_id)
            return

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
        try:
            player = await self.get_player_in_game(game_id, player_id)
            if not player:
                raise ValueError("Player not found in game")

            # 2. Преобразуем строку в enum AbilityType
            try:
                ability_enum = AbilityType(ability_type)
            except ValueError:
                raise ValueError(f"Unknown ability type: {ability_type}")

            # 3. Ищем PlayerAbility, связанную с Ability, и фильтруем по игроку и типу способности
            stmt = (
                select(PlayerAbility)
                .join(Ability, PlayerAbility.ability_id == Ability.id)
                .where(PlayerAbility.player_id == player_id)
                .where(Ability.ability_type == ability_enum)
            )
            result = await self.db.execute(stmt)
            player_ability = result.scalar_one_or_none()
            if not player_ability:
                raise ValueError(f"Player {player_id} does not have ability {player_ability.ability_id}")

            if player_ability.number_uses_left <= 0:
                raise ValueError(f"Player {player_id} has no more uses of ability {ability_type}")

            player_ability.number_uses_left -= 1

            ability = await self.db.get(Ability, player_ability.ability_id)

            print(ability_type)

            if AbilityType(ability_type) == AbilityType.SHIELD:
                await self.apply_effect(
                    game_id,
                    player_id,
                    EffectType.SHIELD,
                    ability.duration_seconds
                )
            elif AbilityType(ability_type) == AbilityType.SCAN:
                await self.apply_effect(
                    game_id,
                    player_id,
                    EffectType.INTEL,
                    ability.duration_seconds
                )
            elif AbilityType(ability_type) == AbilityType.TRAP:
                zone_service = ZoneService(self.db)
                await zone_service.create_zone(
                    game_id,
                    ZoneType.TRAP,
                    lat,
                    lng,
                    ability.duration_seconds,
                    ability.data.get("radius"),
                    ability.data.get("damage"),
                    player_id
                )
            elif AbilityType(ability_type) == AbilityType.PERSONAL_BOMB:
                print("[DEBUG ABILITY TYPE ACTIVATION]")
                zone_service = ZoneService(self.db)
                await zone_service.create_zone(
                    game_id,
                    ZoneType.DANGER,
                    lat,
                    lng,
                    ability.duration_seconds,
                    ability.data.get("radius"),
                    ability.data.get("damage"),
                    player_id
                )
            elif AbilityType(ability_type) == AbilityType.SAFE_HOUSE:
                zone_service = ZoneService(self.db)
                await zone_service.create_zone(
                    game_id,
                    ZoneType.SAFE_HOUSE,
                    lat,
                    lng,
                    ability.duration_seconds,
                    ability.data.get("radius"),
                    ability.data.get("damage"),
                    player_id
                )
            elif AbilityType(ability_type) == AbilityType.SAFE_MANSION:
                zone_service = ZoneService(self.db)
                await zone_service.create_zone(
                    game_id,
                    ZoneType.SAFE_MANSION,
                    lat,
                    lng,
                    ability.duration_seconds,
                    ability.data.get("radius"),
                    ability.data.get("damage"),
                    player_id
                )
            elif AbilityType(ability_type) == AbilityType.INTEL:
                await self.handle_scan_effect(
                    game_id,
                    player_id,
                    ability.duration_seconds
                )
            elif AbilityType(ability_type) == AbilityType.SAFE_HOUSE:
                zone_service = ZoneService(self.db)
                await zone_service.create_zone(
                    game_id,
                    ZoneType.SAFE_HOUSE,
                    lat,
                    lng,
                    ability.duration_seconds,
                    ability.data.get("radius"),
                    ability.data.get("damage"),
                    player_id
                )
            return 0
        except Exception:
            await self.db.rollback()
            raise

    async def handle_scan_effect(self, game_id: uuid.UUID, player_id: uuid.UUID, duration: int):
        await self.apply_effect(game_id, player_id, EffectType.SCAN, duration)
        await connection_manager.send_personal({
                "type": "scan_activated",
                "duration": duration,
                "ends_at": (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat()
            },
            player_id
        )

    async def apply_effect(self, game_id: uuid.UUID, player_id, effect_type: EffectType, duration_seconds: int | None):
        try:
            now = datetime.now(timezone.utc)

            new_player_effect = PlayerEffect(
                player_id=player_id,
                type=effect_type,
                starts_at=now,
                ends_at=now + timedelta(seconds=duration_seconds) if duration_seconds is not None else None,
                is_active=True
            )

            self.db.add(new_player_effect)
            await self.db.flush()

            if effect_type == EffectType.INTEL:
                await timer_manager.intel_ability_shedule(
                    game_id=game_id,
                    player_id=player_id,
                    effect=new_player_effect,
                    end_time=new_player_effect.ends_at,
                    callback=lambda: self._on_effect_expired_callback(game_id, new_player_effect.id)
                )
            elif effect_type == EffectType.SCAN:
                await timer_manager.scan_ability_shedule(
                    game_id=game_id,
                    player_id=player_id,
                    effect=new_player_effect,
                    end_time=new_player_effect.ends_at,
                    callback=lambda: self._on_effect_expired_callback(game_id, new_player_effect.id)
                )
            else:
                await timer_manager.schedule(
                    game_id=game_id,
                    entity_type=TimerType.EFFECT,
                    entity_id=new_player_effect.id,
                    end_time=new_player_effect.ends_at,
                    callback=lambda: self._on_effect_expired_callback(game_id, new_player_effect.id)
                )

        except Exception:
            await self.db.rollback()
            raise

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