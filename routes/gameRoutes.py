import asyncio
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from typing import Any, Dict
from models import (Game, Player, PlayerRole, Ability, AbilityType, EventType, Zone, ZoneType,
                    role_abilities, Role, game_roles, role_events, Event, GameStatus)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func
from GameService import GameService
from db import init_db, get_db
from fastapi import APIRouter

game_router = APIRouter()

@game_router.api_route("/api/games/create", methods=["POST"])
async def create_game(
        data: Dict[str, Any],
        db: AsyncSession = Depends(get_db)
):
    """Создать новую игру"""
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

    try:
        service = GameService(db)
        await service.create_game(data)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при создании игры: {str(e)}")


@game_router.api_route("/api/games/{game_id}/connect_player", methods=["POST"])
async def create_game(
        game_id: uuid.UUID,
        data: Dict[str, Any],
        db: AsyncSession = Depends(get_db)
):
    """Добавить игрока в игру"""
    # Валидация обязательных полей

    required_fields = ["name", "center_lat", "center_lng"]
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

    try:
        service = GameService(db)
        new_player = await service.add_player(game_id, data)
        return new_player
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при добавлении игрока: {str(e)}")




