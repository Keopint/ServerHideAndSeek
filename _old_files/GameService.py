import uuid
from database.models import Game, Player, Role, game_roles, PlayerRole, ZoneType, Zone, Ability, role_abilities, role_events, \
    Event, GameStatus, GameZone, PlayerEffect, EffectType, AbilityType
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Any, Dict, Optional
from datetime import datetime, timezone, timedelta
from timers import timer_manager, TimerType
from websocket_manager import connection_manager

class GameService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.timer_manager = timer_manager

    async def create_game(self, data: Dict[str, Any]):
        game = Game(
            name=data["name"],
            safe_zone_center_lat=data["center_lat"],
            safe_zone_center_lng=data["center_lng"],
            safe_zone_radius=data.get("safe_zone_radius", 500.0),
            min_zone_radius=data.get("min_zone_radius", 50.0),
            zone_shrink_interval=data.get("zone_shrink_interval", 120),
            game_duration=data.get("game_duration", 1800),
            last_shrink_at=datetime.now(timezone.utc)
        )

        self.db.add(game)
        await self.db.flush()

        # 2. Создаем игрока-хоста (вода)
        host_player = Player(
            game_id=game.id,
            name=data["host_name"],
            role=PlayerRole.seeker,
            health=100,
            is_alive=True,
            location_lat=data["host_player_location_lat"],
            location_lng=data["host_player_location_lng"],
            last_location_update=datetime.now(timezone.utc)
        )

        self.db.add(host_player)
        await self.db.flush()

        # 3. Создаем начальную безопасную зону
        initial_zone = Zone(
            game_id=game.id,
            type=ZoneType.safe,
            center_lat=data["center_lat"],
            center_lng=data["center_lng"],
            radius=data.get("safe_zone_radius", 500.0),
            created_at=datetime.now(timezone.utc),
            expires_at=None,
            is_active=True
        )

        self.db.add(initial_zone)
        await self.db.flush()

        # 4. Связываем игру с текущей безопасной зоной
        game.current_safe_zone_id = initial_zone.id

        # 5. Добавляем способности (если есть)

        roles_data = data.get("roles", [])
        abilities_data = data.get("abilities", {})
        events_data = data.get("events", {})

        for role_name in roles_data:
            # Создаём роль
            new_role = Role(name=role_name)
            self.db.add(new_role)
            await self.db.flush()

            # Связываем роль с игрой
            await self.db.execute(
                game_roles.insert().values(
                    game_id=game.id,
                    role_id=new_role.id
                )
            )

            # Добавляем способности для роли
            for ability_type in abilities_data.get(role_name, []):
                result = await self.db.execute(
                    select(Ability.id).where(Ability.ability_type == ability_type)
                )
                ability_id = result.scalar_one_or_none()
                if ability_id:
                    await self.db.execute(
                        role_abilities.insert().values(
                            role_id=new_role.id,
                            ability_id=ability_id
                        )
                    )

            # Добавляем события для роли
            for event_type in events_data.get(role_name, []):
                result = await self.db.execute(
                    select(Event.id).where(Event.type == event_type)
                )
                event_id = result.scalar_one_or_none()
                if event_id:
                    await self.db.execute(
                        role_events.insert().values(
                            role_id=new_role.id,
                            event_id=event_id
                        )
                    )

        # 6. Сохраняем все изменения
        await self.db.commit()
        await self.db.refresh(game)

        return {
            "game_id": str(game.id),
            "player_id": str(host_player.id),
            "game_name": game.name,
            "status": game.status.value,
            "center_lat": data["center_lat"],
            "center_lng": data["center_lng"],
            "safe_zone_radius": game.safe_zone_radius,
            "created_at": game.created_at.isoformat()
        }

    async def get_game(self, game_id: uuid.UUID) -> Game:
        """Получить игру или выбросить исключение"""

        game = await self.db.get(Game, game_id)
        if not game:
            raise ValueError(f"Game with id {game_id} not found")
        return game

    async def get_game_players(self, game_id: uuid.UUID) -> list[Player]:
        """
        Получить список всех игроков в игре
        """
        players = await self.db.execute(
            select(Player).where(
                Player.game_id == game_id,
            )
        ).scalars().all()

        return players

    async def get_game_player(self, game_id: uuid.UUID, player_id: uuid.UUID) -> Player | None:
        """
        Получить конкретного игрока по ID игры и ID игрока.
        """
        player = await self.db.execute(
            select(Player).where(
                Player.game_id == game_id,
                Player.id == player_id
            )
        ).scalar_one_or_none()

        if not player:
            raise ValueError(f"Player {player_id} not found in game {game_id}")
        return player

    async def get_alive_players(self, game_id: uuid.UUID) -> list[Player]:
        """
        Получить список живых игроков в игре
        """
        alive_players = await self.db.execute(
            select(Player).where(
                Player.game_id == game_id,
                Player.is_alive == True
            )
        ).scalars().all()

        if not alive_players:
            raise ValueError(f"No alive players with game_id {game_id}")

        return alive_players

    async def add_player(self, game_id: uuid.UUID, data: Dict[str, Any]):
        game = await self.db.get(Game, game_id)
        if not game:
            raise ValueError("Game not found")

        if game.status != GameStatus.WAITING:
            raise ValueError("Game is already active")

        first_role = game.roles[0]

        new_player = Player(
            game_id=game_id,
            name=data["name"],
            role_id=first_role.id,  # теперь это FK
            health=first_role.health,
            is_alive=True,
            location_lat=data["player_location_lat"],
            location_lng=data["player_location_lng"],
            last_location_update=datetime.now(timezone.utc)
        )
        self.db.add(new_player)
        await self.db.commit()
        await self.db.refresh(new_player)
        return new_player

    async def create_zone(self, game_id, data: Dict[str, Any]):
        zone_info = await self.db.execute(
            select(Zone).where(
                Zone.game_id == game_id,
                Zone.type == data.get("zone_type")
            )
        ).scalar_one_or_none()

        if not zone_info:
            raise ValueError("ZoneType not found")

        if zone_info.type == ZoneType.DANGER:
            return await self.create_danger_zone(game_id, zone_info, data)

    async def create_danger_zone(self, game_id, zone_info, data: Dict[str, Any]):
        now = datetime.now(timezone.utc)

        danger_zone = GameZone(
            game_id=game_id,
            type=data["zone_type"],
            center_lat=data["center_lat"],
            center_lng=data["center_lng"],
            radius=zone_info.radius,
            starts_at=now,
            ends_at=now + timedelta(seconds=zone_info.duration_seconds),
            created_by=data.get("creator_player_id", None)
        )
        self.db.add(danger_zone)
        await self.db.commit()
        await self.db.refresh(danger_zone)

        # Планируем завершение зоны
        await timer_manager.schedule(
            game_id=game_id,
            entity_type=TimerType.ZONE,
            entity_id=danger_zone.id,
            end_time=danger_zone.ends_at,
            callback=lambda: self._on_zone_expired(game_id, danger_zone.id)
        )

        # Уведомляем игроков
        await self._notify_game_players(game_id, {
            "type": "zone_created",
            "zone": danger_zone.to_dict()
        })

        return danger_zone

    async def _on_zone_expired(self, game_id: uuid.UUID, zone_id: uuid.UUID):
        """Вызывается таймером по истечении зоны."""
        # Загружаем актуальные данные зоны
        stmt = select(GameZone).where(GameZone.id == zone_id)
        result = await self.db.execute(stmt)
        zone = result.scalar_one_or_none()
        if not zone or not zone.is_active:
            return

        # Помечаем неактивной
        zone.is_active = False
        self.db.add(zone)

        # Находим игроков внутри зоны (можно вызвать метод проверки)
        affected_players = await self._get_players_in_zone(game_id, zone)
        for player in affected_players:
            # Применяем урон или выбывание (в зависимости от типа зоны)
            await self._apply_zone_damage(player, zone)

        await self.db.commit()

        # Уведомляем об окончании
        await self._notify_game_players(game_id, {
            "type": "zone_expired",
            "zone_id": str(zone_id),
            "affected_players": [str(p.id) for p in affected_players]
        })

    async def _notify_game_players(self, game_id: uuid.UUID, message: dict, exclude_player: Optional[uuid.UUID] = None):
        """Отправляет WebSocket-сообщение всем онлайн-игрокам игры."""
        await connection_manager.broadcast_to_game(game_id, message, exclude_player)

    async def update_player_location(
        self,
        game_id: uuid.UUID,
        player_id: uuid.UUID,
        lat: float,
        lng: float
    ) -> Player:
        """
        Обновляет геолокацию игрока, проверяет попадание в зоны и применяет эффекты.
        """
        # 1. Валидация координат
        if not (-90 <= lat <= 90):
            raise ValueError("Invalid latitude (must be between -90 and 90)")
        if not (-180 <= lng <= 180):
            raise ValueError("Invalid longitude (must be between -180 and 180)")

        # 2. Получаем игрока и связанную игру
        player = await self.db.get(Player, player_id)
        if not player:
            raise ValueError(f"Player {player_id} not found")
        if player.game_id != game_id:
            raise ValueError(f"Player {player_id} does not belong to game {game_id}")

        game = await self.db.get(Game, game_id)
        if not game:
            raise ValueError(f"Game {game_id} not found")
        if game.status not in (GameStatus.ACTIVE, GameStatus.WAITING):
            raise ValueError(f"Game {game_id} is not active")

        # 3. Обновляем координаты и время
        player.lat = lat
        player.lng = lng
        player.last_location_update = datetime.now(timezone.utc)
        self.db.add(player)

        # 4. Проверка выхода за границы безопасной зоны (если игра ограничена радиусом)
        if game.center_lat is not None and game.center_lng is not None and game.radius is not None:
            distance = self._calculate_distance(
                lat, lng, game.center_lat, game.center_lng
            )
            if distance > game.radius:
                # Игрок вышел за пределы игровой зоны — наносим урон
                await self._apply_boundary_damage(player, game)

        # 5. Проверка попадания в активные зоны (красные, оранжевые, капканы)
        await self._check_zone_effects(game_id, player)

        # 6. Проверка активных эффектов (например, находится ли игрок в капкане и может ли двигаться)
        await self._check_movement_restrictions(game_id, player)

        # 7. Сохраняем изменения
        await self.db.commit()
        await self.db.refresh(player)

    def _calculate_distance(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """
        Вычисляет расстояние между двумя точками в метрах (формула гаверсинусов для
        точного вычисления расстояния, учитывая изогнутость поверхности Земли).
        """
        from math import radians, sin, cos, sqrt, atan2

        R = 6371000  # радиус Земли в метрах
        lat1_rad = radians(lat1)
        lat2_rad = radians(lat2)
        delta_lat = radians(lat2 - lat1)
        delta_lng = radians(lng2 - lng1)

        a = sin(delta_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lng / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    async def _apply_boundary_damage(self, player: Player, game: Game):
        """Наносит урон игроку за выход за границы игровой зоны."""
        # Предположим, что damage_per_second хранится в конфигурации игры
        damage = game.zone_boundary_damage
        player.health -= damage
        if player.health <= 0:
            player.is_alive = False
            player.health = 0
        self.db.add(player)
        # Можно отправить уведомление через WebSocket

    async def _check_zone_effects(self, game_id: uuid.UUID, player: Player):
        """Проверяет, находится ли игрок в какой-либо активной зоне, и применяет эффекты."""
        now = datetime.now(timezone.utc)
        # Получаем все активные зоны игры
        stmt = select(GameZone).where(
            GameZone.game_id == game_id,
            GameZone.is_active == True,
            GameZone.starts_at <= now,
            GameZone.ends_at > now
        )
        result = await self.db.execute(stmt)
        zones = result.scalars().all()

        for zone in zones:
            distance = self._calculate_distance(
                player.lat, player.lng, zone.center_lat, zone.center_lng
            )
            if distance <= zone.radius:
                # Игрок внутри зоны
                if zone.type == ZoneType.DANGER:
                    # Мгновенный урон или отметка о попадании
                    await self._handle_red_zone_entry(player, zone)
                elif zone.type == ZoneType.WARNING:
                    # Оранжевая зона действует только на конкретного игрока (если это индивидуальное событие)
                    if zone.target_player_id == player.id:
                        await self._handle_orange_zone_entry(player, zone)
                elif zone.type == ZoneType.TRAP:  # Капкан
                    await self._handle_trap_entry(player, zone, trap_duration=60)
                elif zone.type == ZoneType.SNARE:  # Ловушка
                    await self._handle_trap_entry(player, zone, trap_duration=600)

    async def _check_movement_restrictions(self, game_id: uuid.UUID, player: Player):
        """Проверяет, не находится ли игрок под действием эффекта, запрещающего движение."""
        now = datetime.now(timezone.utc)
        stmt = select(PlayerEffect).where(
            PlayerEffect.player_id == player.id,
            PlayerEffect.is_active == True,
            PlayerEffect.starts_at <= now,
            PlayerEffect.ends_at > now,
            PlayerEffect.type.in_([EffectType.TRAPPED, EffectType.ROOTED])
        )
        result = await self.db.execute(stmt)
        active_trap = result.scalar_one_or_none()
        if active_trap:
            # Игрок не может двигаться — откатываем координаты
            # Для этого нужно либо сохранить старые координаты перед обновлением, либо выбросить исключение
            raise ValueError("Player is trapped and cannot move")

    async def _handle_red_zone_entry(self, player: Player, zone: GameZone):
        """Обрабатывает попадание в красную зону (например, смерть)."""
        # Если у игрока есть активный щит, он выживает, щит снимается
        if await self._has_active_shield(player.id):
            await self._consume_shield(player.id)
            return
        player.is_alive = False
        player.health = 0
        self.db.add(player)

    async def _handle_orange_zone_entry(self, player: Player, zone: GameZone):
        """Обрабатывает индивидуальную оранжевую зону."""
        # Аналогично красной, но только для конкретного игрока
        if await self._has_active_shield(player.id):
            await self._consume_shield(player.id)
            return
        player.is_alive = False
        player.health = 0
        self.db.add(player)

    async def _handle_trap_entry(self, player: Player, zone: GameZone, trap_duration: int):
        """Обрабатывает попадание в капкан/ловушку."""
        # Проверяем, не попал ли уже игрок в этот капкан ранее
        stmt = select(PlayerEffect).where(
            PlayerEffect.player_id == player.id,
            PlayerEffect.zone_id == zone.id,
            PlayerEffect.is_active == True
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return  # Уже в ловушке

        # Создаём эффект обездвиживания
        now = datetime.now(timezone.utc)
        effect = PlayerEffect(
            player_id=player.id,
            type=EffectType.TRAPPED,
            starts_at=now,
            ends_at=now + timedelta(seconds=trap_duration),
            zone_id=zone.id,
            is_active=True
        )
        self.db.add(effect)
        # Можно пометить зону как использованную, если она одноразовая
        if zone.single_use:
            zone.is_active = False
            self.db.add(zone)

        # Планируем окончание эффекта через TimerManager
        from timers import timer_manager, TimerType
        await timer_manager.schedule(
            game_id=player.game_id,
            entity_type=TimerType.EFFECT,
            entity_id=effect.id,
            end_time=effect.ends_at,
            callback=lambda: self._on_effect_expired(effect.id)
        )

    async def _has_active_shield(self, player_id: uuid.UUID) -> bool:
        """Проверяет, есть ли у игрока активный щит."""
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

    async def _consume_shield(self, player_id: uuid.UUID):
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
            target: Player = None) -> int:

        return 0



