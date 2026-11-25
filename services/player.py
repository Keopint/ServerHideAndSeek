import uuid
import math

from sqlalchemy.orm import selectinload

from database.models import (Game, Player, GameStatus, PlayerEffect,
                             EffectType, AbilityType, PlayerAbility, Ability, ZoneType, Role, PlayerDeathCauses,
                             GameZone, role_abilities, game_roles, VictoryConditionType, UsedAbility)
from sqlalchemy import select, delete
from datetime import datetime, timezone, timedelta
from services.zone import ZoneService
from services.ability_rules import forbidden_abilities_for
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

    async def get_ability_state(self, player_id: uuid.UUID):
        """Персональный остаток и откат для восстановления интерфейса."""
        rows = (await self.db.execute(select(PlayerAbility, Ability)
                .join(Ability, PlayerAbility.ability_id == Ability.id)
                .where(PlayerAbility.player_id == player_id))).all()
        now = datetime.now(timezone.utc)
        abilities = []
        for owned, ability in rows:
            last = (await self.db.execute(select(UsedAbility.used_at)
                    .where(UsedAbility.player_id == player_id, UsedAbility.ability_id == ability.id)
                    .order_by(UsedAbility.used_at.desc()).limit(1))).scalar_one_or_none()
            remaining_ms = max(0, int(((last + timedelta(seconds=ability.recharge_time)) - now).total_seconds() * 1000)) if last else 0
            abilities.append({
                'ability_type': ability.ability_type.value, 'number_uses_left': owned.number_uses_left,
                'recharge_time': ability.recharge_time, 'duration_seconds': ability.duration_seconds,
                'data': ability.data or {}, 'cooldown_remaining_ms': remaining_ms,
            })
        return abilities

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

    async def get_passive_effect_state(self, player_id: uuid.UUID):
        now = datetime.now(timezone.utc)
        return [
            {'id': str(effect.id), 'type': effect.type.value,
             'remaining_ms': max(0, int((effect.ends_at - now).total_seconds() * 1000))}
            for effect in await self.get_active_player_effects(player_id)
            if effect.type in (EffectType.SHIELD, EffectType.INTEL, EffectType.SCAN)
        ]

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

        # Другая сессия могла изменить фазу игры или радиус безопасной зоны.
        game = await self.db.get(Game, game_id, populate_existing=True)
        if not game:
            raise ValueError(f"Game {game_id} not found")
        if game.status not in (GameStatus.ACTIVE, GameStatus.WAITING, GameStatus.HIDE_TIME):
            raise ValueError("Game is not active")
        if not player.is_alive:
            raise ValueError("Player is no longer active")

        # Реальное положение сохраняется даже при действии ловушки.
        player.location_lat = lat
        player.location_lng = lng
        player.last_location_update = datetime.now(timezone.utc)
        self.db.add(player)
        await self.db.flush()
        # Урон за границу применяется только в активной фазе, не в лобби.
        if game.status == GameStatus.ACTIVE:
            effects = await self.get_active_player_effects(player_id)
            for effect in effects:
                trap = effect.data or {}
                if (effect.type == EffectType.TRAPPED and
                    all(key in trap for key in ('center_lat', 'center_lng', 'radius')) and
                    calculate_distance(lat, lng, trap['center_lat'], trap['center_lng']) > trap['radius']):
                    player.health = 0
                    await self.player_died(game_id, player_id, PlayerDeathCauses.HP_ARE_OVER)
                    return player
            if game.safe_zone_center_lat is not None and game.safe_zone_center_lng is not None and game.safe_zone_radius is not None:
                distance = calculate_distance(lat, lng, game.safe_zone_center_lat, game.safe_zone_center_lng)
                if distance > game.safe_zone_radius:
                    await self.apply_damage(game_id, player_id, game.zone_boundary_damage, ignore_shield=True)
            if player.is_alive:
                zone_service = ZoneService(self.db)
                await zone_service.check_player_in_zones(game_id, player_id)
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
        # WebSocket-сессия живёт долго: статус мог измениться в другом соединении.
        game = await self.db.get(Game, game_id, populate_existing=True)
        if not game or game.status != GameStatus.WAITING:
            raise ValueError("Роль можно изменить только в лобби")
        role_in_game = await self.db.execute(select(game_roles.c.role_id).where(
            game_roles.c.game_id == game_id, game_roles.c.role_id == new_role_id))
        if role_in_game.scalar_one_or_none() is None:
            raise ValueError("Роль не принадлежит этой игре")
        # Проверяем существование новой роли
        role = await self.db.get(Role, new_role_id)
        if not role:
            raise ValueError("Role not found")
        # Обновляем роль
        player.role_id = new_role_id
        # Дополнительно можно обновить здоровье в соответствии с ролью
        player.health = role.health
        await self.set_role_abilities(player)
        await self.db.commit()

    async def set_role_abilities(self, player):
        """В лобби заменяем способности игрока набором выбранной роли."""
        await self.db.execute(delete(PlayerAbility).where(PlayerAbility.player_id == player.id))
        result = await self.db.execute(
            select(Ability).join(role_abilities, Ability.id == role_abilities.c.ability_id)
            .where(role_abilities.c.role_id == player.role_id)
        )
        for ability in result.scalars().all():
            self.db.add(PlayerAbility(player_id=player.id, ability_id=ability.id,
                                      number_uses_left=ability.number_uses))

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
            game = await self.db.get(Game, game_id)
            if not game or game.status != GameStatus.WAITING:
                raise ValueError("Готовность можно изменить только в лобби")
            if not isinstance(new_ready_status, bool):
                raise ValueError("Неверное значение готовности")
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
        return result.scalars().first() is not None

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

    async def player_died(self, game_id: uuid.UUID, player_id: uuid.UUID, death_cause: PlayerDeathCauses, hunter_player_id: uuid.UUID = None, voluntary_quit: bool = False):
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
                "type": "quit_confirmed" if voluntary_quit else "you_died",
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

        await self._finish_game_if_side_eliminated(game_id)

    async def _finish_game_if_side_eliminated(self, game_id: uuid.UUID) -> None:
        """Завершает игру, если не осталось живых игроков одной из сторон."""
        game = await self.db.get(Game, game_id)
        if not game or game.status not in (GameStatus.HIDE_TIME, GameStatus.ACTIVE):
            return

        players = await self.get_players_in_game(game_id)
        alive_hiders = 0
        alive_seekers = 0
        for player in players:
            if not player.is_alive:
                continue
            role = await self.db.get(Role, player.role_id)
            if not role:
                continue
            if role.victory_condition == VictoryConditionType.HIDER:
                alive_hiders += 1
            elif role.victory_condition == VictoryConditionType.SEEKER:
                alive_seekers += 1

        if alive_hiders == 0 or alive_seekers == 0:
            from services.game_management import GameService
            await GameService(self.db).finish_game(game_id=game_id)

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
        return result.scalars().first() is not None

    async def consume_shield(self, player_id: uuid.UUID):
        """Снимает активный щит с игрока."""
        now = datetime.now(timezone.utc)
        stmt = select(PlayerEffect).where(
            PlayerEffect.player_id == player_id,
            PlayerEffect.type == EffectType.SHIELD,
            PlayerEffect.is_active == True
        )
        result = await self.db.execute(stmt)
        shield = result.scalars().first()
        if shield:
            shield.is_active = False
            self.db.add(shield)
            await connection_manager.send_personal({'type': 'passive_effect_ended',
                                                     'data': {'id': str(shield.id)}}, player_id)

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
            game = await self.db.get(Game, game_id, populate_existing=True)
            if not game or game.status != GameStatus.ACTIVE or not player.is_alive:
                raise ValueError("Способность доступна только активному игроку")

            # 2. Преобразуем строку в enum AbilityType
            try:
                ability_enum = AbilityType(ability_type)
            except ValueError:
                raise ValueError(f"Unknown ability type: {ability_type}")

            role = getattr(player, 'role_ref', None)
            if role is not None and ability_enum in forbidden_abilities_for(role.victory_condition):
                raise ValueError("Эта способность недоступна вашей роли")

            # 3. Ищем PlayerAbility, связанную с Ability, и фильтруем по игроку и типу способности
            stmt = (
                select(PlayerAbility)
                .join(Ability, PlayerAbility.ability_id == Ability.id)
                .where(PlayerAbility.player_id == player_id)
                .where(Ability.ability_type == ability_enum)
                .with_for_update(of=PlayerAbility)
            )
            result = await self.db.execute(stmt)
            player_ability = result.scalar_one_or_none()
            if not player_ability:
                raise ValueError("У игрока нет этой способности")

            if player_ability.number_uses_left <= 0:
                raise ValueError(f"Player {player_id} has no more uses of ability {ability_type}")

            ability = await self.db.get(Ability, player_ability.ability_id)
            if not ability or not ability.duration_seconds or ability.duration_seconds <= 0 or ability.recharge_time < 0:
                raise ValueError("Некорректная длительность способности")
            last_use = (await self.db.execute(select(UsedAbility.used_at)
                        .where(UsedAbility.player_id == player_id, UsedAbility.ability_id == ability.id)
                        .order_by(UsedAbility.used_at.desc()).limit(1))).scalar_one_or_none()
            if last_use and datetime.now(timezone.utc) < last_use + timedelta(seconds=ability.recharge_time):
                raise ValueError("Способность ещё перезаряжается")
            if ability_enum in (AbilityType.INTEL, AbilityType.SCAN):
                active = await self.get_active_player_effects(player_id)
                if any(effect.type in (EffectType.INTEL, EffectType.SCAN) for effect in active):
                    raise ValueError("Другая способность обнаружения уже действует")
            if ability_enum == AbilityType.SHIELD and await self.has_active_shield(player_id):
                raise ValueError("Щит уже активен")
            if ability_enum in (AbilityType.SAFE_HOUSE, AbilityType.SAFE_MANSION):
                lat, lng = player.location_lat, player.location_lng
            if ability_enum in (AbilityType.TRAP, AbilityType.SNARE, AbilityType.PERSONAL_BOMB, AbilityType.SAFE_HOUSE, AbilityType.SAFE_MANSION):
                if (isinstance(lat, bool) or isinstance(lng, bool) or
                    not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)) or
                    not math.isfinite(lat) or not math.isfinite(lng) or not validate_coordinates(lat, lng)):
                    raise ValueError("Укажите корректную точку на карте")
                radius = (ability.data or {}).get('radius')
                if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
                    raise ValueError("Некорректный радиус способности")
                if ability_enum == AbilityType.PERSONAL_BOMB:
                    damage = ability.data.get('damage')
                    if (isinstance(damage, bool) or not isinstance(damage, (int, float)) or
                        not math.isfinite(damage) or damage <= 0 or int(damage) != damage):
                        raise ValueError("Некорректный урон бомбы")
                if calculate_distance(lat, lng, game.safe_zone_center_lat, game.safe_zone_center_lng) > game.safe_zone_radius:
                    raise ValueError("Точка способности находится за пределами игровой зоны")
            player_ability.number_uses_left -= 1
            self.db.add(UsedAbility(player_id=player_id, ability=ability_enum, ability_id=ability.id,
                                    used_at=datetime.now(timezone.utc), location_lat=player.location_lat,
                                    location_lng=player.location_lng))

            if AbilityType(ability_type) == AbilityType.SHIELD:
                await self.apply_effect(
                    game_id,
                    player_id,
                    EffectType.SHIELD,
                    ability.duration_seconds
                )
            elif AbilityType(ability_type) == AbilityType.INTEL:
                await self.apply_effect(
                    game_id,
                    player_id,
                    EffectType.INTEL,
                    ability.duration_seconds
                )
            elif AbilityType(ability_type) in (AbilityType.TRAP, AbilityType.SNARE):
                zone_service = ZoneService(self.db)
                zone = await zone_service.create_zone(
                    game_id,
                    ZoneType.TRAP if ability_enum == AbilityType.TRAP else ZoneType.SNARE,
                    lat,
                    lng,
                    ability.duration_seconds,
                    ability.data.get("radius"),
                    ability.data.get("damage"),
                    player_id
                )
                zone.zone_data = {'trap_duration_seconds': int(ability.data.get('trap_duration_seconds', 60))}
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
                    int(ability.data["damage"]),
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
            elif AbilityType(ability_type) == AbilityType.SCAN:
                await self.apply_effect(game_id, player_id, EffectType.SCAN, ability.duration_seconds)
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

            if effect_type in (EffectType.INTEL, EffectType.SCAN):
                from services.visibility import visibility_tick
                await timer_manager.schedule(
                    game_id, TimerType.EFFECT, new_player_effect.id,
                    now + timedelta(milliseconds=100),
                    lambda: visibility_tick(game_id, player_id, new_player_effect.id)
                )
            else:
                await timer_manager.schedule(
                    game_id=game_id,
                    entity_type=TimerType.EFFECT,
                    entity_id=new_player_effect.id,
                    end_time=new_player_effect.ends_at,
                    callback=lambda: self._on_effect_expired_callback(game_id, new_player_effect.id)
                )

            return new_player_effect

        except Exception:
            await self.db.rollback()
            raise

    async def _on_effect_expired_callback(self, game_id: uuid.UUID, effect_id: uuid.UUID):
        """Callback для таймера; создаёт новую сессию и вызывает обработчик."""
        # Импортируем здесь, чтобы избежать циклических зависимостей
        from database.db import get_db
        async for db in get_db():
            await PlayerService(db).handle_effect_expired(game_id, effect_id)
            break


    async def handle_effect_expired(self, game_id: uuid.UUID, effect_id: uuid.UUID):
        """Обрабатывает истечение эффекта"""
        effect = await self.db.get(PlayerEffect, effect_id)
        if not effect:
            return
        effect.is_active = False
        self.db.add(effect)

        await self.db.commit()
        if effect.type == EffectType.TRAPPED:
            await connection_manager.broadcast_to_game(game_id, {'type': 'delete_zone', 'data': {'zone_id': str(effect.id)}})
