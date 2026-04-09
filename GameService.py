import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from models import Game, Player, Role, game_roles, PlayerRole, ZoneType, Zone, Ability, role_abilities, role_events, \
    Event, GameStatus, GameZone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func
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

        game = await self.get_by_id(game_id)
        if not game:
            raise ValueError(f"Game with id {game_id} not found")
        return game


    async def get_game_or_none(self, game_id: uuid.UUID) -> Game | None:
        """Получить игру или None"""

        return await self.get_by_id(game_id)


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

        if game.status != GameStatus.waiting:
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

    async def notify_game_players(self, game_id, param):
        await connection_manager.broadcast_to_game(game_id, param)

    async def create_zone(self, game_id, data: Dict[str, Any]):
        zone_info = await self.db.execute(
            select(Zone).where(
                Zone.game_id == game_id,
                Zone.type == data.get("zone_type")
            )
        ).scalar_one_or_none()

        if not zone_info:
            raise ValueError("ZoneType not found")

        if zone_info.type == ZoneType.danger:
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