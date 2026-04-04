import asyncio
from datetime import datetime
from models import Game, Player, PlayerRole, Ability, AbilityType, EventType, role_abilities
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from db import init_db, get_db
from main import app
from typing import Any, Dict
from models import Zone, ZoneType


# @app.api_route("/api/games/create", methods=["GET", "POST"])
# async def create_game(data: Dict[str, Any]):
#     """Создать новую игру"""
#     db = get_db()
#
#     try:
#         # 1. Создаем точку для центра безопасной зоны
#         safe_zone_center = Point(data["center_lng"], data["center_lat"])  # (longitude, latitude)
#
#         # 2. Создаем игру
#         game = Game(
#             name=data["name"],
#             created_at=datetime.utcnow(),
#             safe_zone_center=from_shape(safe_zone_center, srid=4326),
#             safe_zone_radius=data.get("safe_zone_radius", 500.0),
#             min_zone_radius=data.get("min_zone_radius", 50.0),
#             zone_shrink_interval=data.get("zone_shrink_interval", 120),
#             game_duration=data.get("game_duration", 1800),
#             last_shrink_at=datetime.utcnow()  # время последнего сужения = время создания
#         )
#
#         db.add(game)
#         await db.flush()  # получаем game.id
#
#         # 3. Создаем игрока-хоста (вода)
#         host_player = Player(
#             game_id=game.id,
#             name=data["host_name"],
#             role=PlayerRole.seeker,  # создатель игры - вода
#             health=100,
#             is_alive=True,
#             lat=data["center_lat"],
#             lng=data["center_lng"],
#             abilities=[]  # начальные способности можно добавить позже
#         )
#
#         db.add(host_player)
#
#         # 4. Создаем начальную безопасную зону (опционально, если хотим хранить в отдельной таблице)
#
#         initial_zone = Zone(
#             game_id=game.id,
#             type=ZoneType.safe,
#             center=from_shape(safe_zone_center, srid=4326),
#             radius=data.get("safe_zone_radius", 500.0),
#             created_at=datetime.utcnow(),
#             expires_at=None,  # безопасная зона не истекает
#             is_active=True
#         )
#
#         db.add(initial_zone)
#
#         # 5. Связываем игру с текущей безопасной зоной
#         game.current_safe_zone_id = initial_zone.id
#
#         abilities = data["abilities"]
#
#         for ability in abilities:
#             role_ability = role_abilities(
#                 role_id=host_player.id,
#                 ability_id=ability.id,
#                 data={"number_uses": data.get("number_uses", 1), "is_strong": data.get("is_strong", False)}
#             )
#             db.add(role_ability)
#
#         # 6. Сохраняем все изменения
#         await db.commit()
#         await db.refresh(game)
#         await db.refresh(host_player)
#
#         return game
#
#     except Exception as e:
#         await db.rollback()
#         raise ValueError(f"Ошибка при создании игры: {str(e)}")

