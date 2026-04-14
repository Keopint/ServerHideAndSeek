import uuid
from fastapi import HTTPException
from database.models import Game, Player, Role, game_roles, PlayerRole, ZoneType, Zone, Ability, role_abilities, role_events, \
    Event, GameStatus
from sqlalchemy import select
from typing import Any, Dict
from datetime import datetime, timezone

from services.base import BaseService


class GameService(BaseService):

    async def create_game(self, data: Dict[str, Any]):
        # Валидация обязательных полей
        required_fields = ["name", "host_name", "center_lat", "center_lng"]
        for field in required_fields:
            if field not in data:
                raise HTTPException(
                    status_code=422,
                    detail=f"Missing required field: {field}"
                )

        # Валидация координат
        center_lat = data["center_lat"]
        center_lng = data["center_lng"]
        if not (-90 <= center_lat <= 90):
            raise HTTPException(status_code=422, detail="Invalid latitude")
        if not (-180 <= center_lng <= 180):
            raise HTTPException(status_code=422, detail="Invalid longitude")

        # Валидация радиуса
        safe_zone_radius = data.get("safe_zone_radius", 500.0)
        if safe_zone_radius < 100:
            raise HTTPException(status_code=422, detail="Radius too small (min 100m)")

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

        host_player_data = data.get("host_player", {})

        # 2. Создаем игрока-хоста (вода)
        host_player = Player(
            game_id=game.id,
            name=host_player_data["host_name"],
            role=PlayerRole.seeker,
            health=100,
            is_alive=True,
            location_lat=host_player_data["host_player_location_lat"],
            location_lng=host_player_data["host_player_location_lng"],
            last_location_update=datetime.now(timezone.utc)
        )

        self.db.add(host_player)
        await self.db.flush()

        # 3. Создаем начальную безопасную зону
        initial_zone = Zone(
            game_id=game.id,
            type=ZoneType.SAFE,
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

    async def get_game(self, game_id: uuid.UUID) -> Game:
        """Получить игру или выбросить исключение"""

        game = await self.db.get(Game, game_id)
        if not game:
            raise ValueError(f"Game with id {game_id} not found")
        return game

    async def get_players_in_game(self, game_id: uuid.UUID) -> list[Player]:
        """
        Получить список всех игроков в игре
        """
        players = await self.db.execute(
            select(Player).where(
                Player.game_id == game_id,
            )
        ).scalars().all()

        return players

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

    async def get_player_in_game(self, game_id: uuid.UUID, player_id: uuid.UUID) -> Player | None:
        """Возвращает игрока, только если он принадлежит указанной игре."""
        stmt = select(Player).where(
            Player.id == player_id,
            Player.game_id == game_id
        )
        result = await self.db.execute(stmt).scalar_one_or_none()
        return result.scalar_one_or_none()

