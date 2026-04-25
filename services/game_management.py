import uuid
from fastapi import HTTPException
from database.models import Game, Player, Role, game_roles, ZoneType, Zone, Ability, role_abilities, \
    role_events, \
    Event, GameStatus, AbilityType, PlayerAbility, EventType, ActivationFrequencyType, GameZone
from sqlalchemy import select
from typing import Any, Dict, Type, Coroutine
from datetime import datetime, timezone, timedelta

from services.base import BaseService


class GameService(BaseService):

    async def create_game(self, data: Dict[str, Any]):
        try:
            # 1. Базовые параметры игры
            name = data["name"]
            center_lat = data["center_lat"]
            center_lng = data["center_lng"]
            safe_zone_radius = data.get("safe_zone_radius", 500.0)
            min_zone_radius = data.get("min_zone_radius", 50.0)
            zone_shrink_interval = data.get("zone_shrink_interval", 120)
            game_duration = data.get("game_duration", 1800)
            zone_boundary_damage = data.get("zone_boundary_damage", 1)

            # 2. Информация о хосте
            host_data = data["host_player"]
            host_name = host_data["host_name"]
            host_lat = host_data.get("host_player_location_lat", center_lat)
            host_lng = host_data.get("host_player_location_lng", center_lng)
            host_role_name = host_data.get("host_role")  # может отсутствовать

            # 3. Списки ролей, способностей, событий
            game_roles_list = data.get("game_roles", [])
            roles_abilities = data.get("roles_abilities", {})
            roles_events = data.get("roles_events", {})
            events_configurations = data.get("events_configurations", {})

            if not game_roles_list:
                raise ValueError("Не указаны роли для игры")

            # Определяем роль хоста
            if host_role_name is None:
                host_role_name = game_roles_list[0]  # берём первую роль

            if host_role_name not in game_roles_list:
                raise ValueError(f"Роль хоста '{host_role_name}' не найдена в списке ролей игры")

            # 4. Создаём игру
            game = Game(
                name=name,
                status=GameStatus.WAITING,
                safe_zone_center_lat=center_lat,
                safe_zone_center_lng=center_lng,
                safe_zone_radius=safe_zone_radius,
                min_zone_radius=min_zone_radius,
                zone_shrink_interval=zone_shrink_interval,
                game_duration=game_duration,
                last_shrink_at=datetime.now(timezone.utc),
                zone_boundary_damage=zone_boundary_damage
            )
            self.db.add(game)
            await self.db.flush()  # чтобы получить game.id

            # 5. Создаём начальную безопасную зону
            now = datetime.now(timezone.utc)
            safe_zone = GameZone(
                game_id=game.id,
                type=ZoneType.SAFE,
                center_lat=center_lat,
                center_lng=center_lng,
                radius=safe_zone_radius,
                starts_at=now,
                ends_at=now + timedelta(seconds=game_duration),
                is_active=True
            )
            self.db.add(safe_zone)
            await self.db.flush()
            game.current_safe_zone_id = safe_zone.id

            # 7. Обрабатываем роли
            role_objects = {}  # name -> Role
            for role_name in game_roles_list:
                role = Role(name=role_name)
                self.db.add(role)
                await self.db.flush()
                role_objects[role_name] = role

                # Связываем роль с игрой
                await self.db.execute(
                    game_roles.insert().values(
                        game_id=game.id,
                        role_id=role.id
                    )
                )

            for event_type, event_data in events_configurations.items():
                event_enum = EventType(event_type)
                activation_frequency_type = ActivationFrequencyType(event_data["activation_frequency"])
                new_event = Event(
                    type = event_enum,
                    activation_frequency = activation_frequency_type,
                    event_data = event_data.get("addition_data", {})
                )
                self.db.add(new_event)
                await self.db.flush()

                for role_name, events_list in roles_events.items():
                    stmt = select(Role).join(game_roles, Role.id == game_roles.c.role_id).where(
                        game_roles.c.game_id == game.id,
                        Role.name == role_name
                    )
                    role = (await self.db.execute(stmt)).scalar_one_or_none()
                    await self.db.execute(
                        role_events.insert().values(
                            role_id=role.id,
                            event_id=new_event.id
                        )
                    )
            stmt = (select(Role.id).join(game_roles, Role.id == game_roles.c.role_id)
                    .where(game_roles.c.game_id == game.id)
                    .where(Role.name == host_role_name))
            role_id = (await self.db.execute(stmt)).scalar_one_or_none()

            host_player = Player(
                game_id=game.id,
                name=host_name,
                role_id = role_id,
                health=100,
                is_alive=True,
                location_lat=host_lat,
                location_lng=host_lng,
                last_location_update=datetime.now(timezone.utc)
            )

            self.db.add(host_player)
            await self.db.flush()

            # 8. Обрабатываем способности для каждой роли
            for role_name, abilities_dict in roles_abilities.items():
                role = role_objects.get(role_name)
                if not role:
                    continue

                for ability_type_str, ability_params in abilities_dict.items():
                    # Приводим к нижнему регистру для enum
                    ability_type_clean = ability_type_str.upper()
                    try:
                        ability_enum = AbilityType(ability_type_clean)
                    except ValueError:
                        raise ValueError(f"Неизвестный тип способности: {ability_type_str}")

                    ability = Ability(
                        ability_type=ability_enum,
                        number_uses=ability_params["number_uses"],
                        recharge_time=ability_params["recharge_time"],
                        data=ability_params["addition_data"]
                    )

                    self.db.add(ability)
                    await self.db.flush()

                    # Связываем роль со способностью
                    await self.db.execute(
                        role_abilities.insert().values(
                            role_id=role.id,
                            ability_id=ability.id
                        )
                    )

                    # Если эта роль принадлежит хосту, даём ему эту способность
                    if role_name == host_role_name:
                        player_ability = PlayerAbility(
                            player_id=host_player.id,
                            ability_id=ability.id,
                            number_uses_left=ability_params.get("number_uses", 1),
                        )
                        self.db.add(player_ability)

            # 10. Фиксируем все изменения
            await self.db.commit()
            await self.db.refresh(game)
            return game

        except Exception as e:
            await self.db.rollback()
            raise ValueError(f"Ошибка при создании игры: {str(e)}")

    async def get_game(self, game_id: uuid.UUID) -> Type[Game]:
        """Получить игру или выбросить исключение"""

        game = await self.db.get(Game, game_id)
        if not game:
            raise ValueError(f"Game with id {game_id} not found")
        return game

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
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

