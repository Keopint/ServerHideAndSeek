import asyncio
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import random
from typing import Any, Dict
from models import (Game, Player, PlayerRole, Ability, AbilityType, EventType, Zone, ZoneType,
                    role_abilities, Role, game_roles, role_events, Event)
from game_logic import GameLogic
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func
from websocket_manager import manager
from GameService import GameService

from db import init_db, get_db

# from_shape(Point(target_lat, target_lng), srid=4326)

app = FastAPI(title="GeoGame Server", version="1.0.0")


# CORS для Android приложения
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Хранилище игр (в памяти, для продакшена используйте Redis/PostgreSQL)
# games: dict[str, Game] = {}


# ---------- REST API ----------
@app.get("/")
async def root():
    return {"message": "GeoGame Server", "status": "running"}


@app.api_route("/api/games/create", methods=["POST"])
async def create_game(
        data: Dict[str, Any],
        db: AsyncSession = Depends(get_db)
):
    """Создать новую игру"""
    try:
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

        # 1. Создаем игру
        game = Game(
            name=data["name"],
            safe_zone_center_lat=center_lat,
            safe_zone_center_lng=center_lng,
            safe_zone_radius=safe_zone_radius,
            min_zone_radius=data.get("min_zone_radius", 50.0),
            zone_shrink_interval=data.get("zone_shrink_interval", 120),
            game_duration=data.get("game_duration", 1800),
            last_shrink_at=datetime.now(timezone.utc)
        )

        db.add(game)
        await db.flush()

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

        db.add(host_player)
        await db.flush()

        # 3. Создаем начальную безопасную зону
        initial_zone = Zone(
            game_id=game.id,
            type=ZoneType.safe,
            center_lat=center_lat,
            center_lng=center_lng,
            radius=safe_zone_radius,
            created_at=datetime.now(timezone.utc) ,
            expires_at=None,
            is_active=True
        )

        db.add(initial_zone)
        await db.flush()

        # 4. Связываем игру с текущей безопасной зоной
        game.current_safe_zone_id = initial_zone.id

        # 5. Добавляем способности (если есть)

        roles_data = data.get("roles", [])
        abilities_data = data.get("abilities", {})
        events_data = data.get("events", {})

        for role_name in roles_data:
            # Создаём роль
            new_role = Role(name=role_name)
            db.add(new_role)
            await db.flush()

            # Связываем роль с игрой
            await db.execute(
                game_roles.insert().values(
                    game_id=game.id,
                    role_id=new_role.id
                )
            )

            # Добавляем способности для роли
            for ability_type in abilities_data.get(role_name, []):
                result = await db.execute(
                    select(Ability.id).where(Ability.ability_type == ability_type)
                )
                ability_id = result.scalar_one_or_none()
                if ability_id:
                    await db.execute(
                        role_abilities.insert().values(
                            role_id=new_role.id,
                            ability_id=ability_id
                        )
                    )

            # Добавляем события для роли
            for event_type in events_data.get(role_name, []):
                result = await db.execute(
                    select(Event.id).where(Event.type == event_type)
                )
                event_id = result.scalar_one_or_none()
                if event_id:
                    await db.execute(
                        role_events.insert().values(
                            role_id=new_role.id,
                            event_id=event_id
                        )
                    )

        # 6. Сохраняем все изменения
        await db.commit()
        await db.refresh(game)

        return {
            "game_id": str(game.id),
            "player_id": str(host_player.id),
            "game_name": game.name,
            "status": game.status.value,
            "center_lat": center_lat,
            "center_lng": center_lng,
            "safe_zone_radius": game.safe_zone_radius,
            "created_at": game.created_at.isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при создании игры: {str(e)}")


@app.api_route("/api/games/{game_id}/info", methods=["GET"])
async def get_game_endpoint(
    game_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    service = GameService(db)
    try:
        game = await service.get_game(game_id)
        return game
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.api_route("/api/games/{game_id}/players_info", methods=["GET"])
async def get_game_players_info(
        game_id: uuid.UUID,
        db: AsyncSession = Depends(get_db)
):
    service = GameService(db)
    try:
        # Получаем список игроков
        players = await service.get_game_players(game_id)

        # JSON ответ
        return {
            "game_id": str(game_id),
            "players_count": len(players),
            "players": [
                {
                    "id": str(player.id),
                    "name": player.name,
                    "role": player.role.value if player.role else None,
                    "health": player.health,
                    "is_alive": player.is_alive,
                    "location": {
                        "lat": player.lat,
                        "lng": player.lng
                    } if player.lat and player.lng else None,
                    "last_location_update": player.last_location_update.isoformat() if player.last_location_update else None,
                    "is_trapped": player.is_trapped,
                    "shield_active": player.shield_active
                }
                for player in players
            ]
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.api_route("/api/games/{game_id}/player_info/{player_id}", methods=["GET"])
async def get_game_players_info(
        game_id: uuid.UUID,
        player_id: uuid.UUID,
        db: AsyncSession = Depends(get_db)
):
    service = GameService(db)
    try:
        # Получаем список игроков
        players = await service.get_game_players(game_id)

        # JSON ответ
        return {
            "game_id": str(game_id),
            "players_count": len(players),
            "players": [
                {
                    "id": str(player.id),
                    "name": player.name,
                    "role": player.role.value if player.role else None,
                    "health": player.health,
                    "is_alive": player.is_alive,
                    "location": {
                        "lat": player.lat,
                        "lng": player.lng
                    } if player.lat and player.lng else None,
                    "last_location_update": player.last_location_update.isoformat() if player.last_location_update else None,
                    "is_trapped": player.is_trapped,
                    "shield_active": player.shield_active
                }
                for player in players
            ]
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))



# ---------- Запуск ----------
@app.on_event("startup")
async def startup():
    await init_db()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)