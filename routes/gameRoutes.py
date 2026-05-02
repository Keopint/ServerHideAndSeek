import uuid
from fastapi import HTTPException, Depends
from typing import Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select
from database.models import Game, Role
from services.game_management import GameService
from database.db import get_db
from fastapi import APIRouter
from websocket_manager import connection_manager
from sqlalchemy.orm import selectinload

from services.player import PlayerService

game_router = APIRouter()

@game_router.api_route("/api/games/create", methods=["POST"])
async def create_game(
        data: Dict[str, Any],
        db: AsyncSession = Depends(get_db)
):
    """Создать новую игру"""
    try:
        service = GameService(db)
        game = await service.create_game(data)
        game_with_relations = await service.get_game_with_relations(game.id)
        return game_with_relations
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при создании игры: {str(e)}")


@game_router.api_route("/api/games/{game_id}/start", methods=["POST"])
async def start_game(
        game_id: str,
        db: AsyncSession = Depends(get_db)
):
    game_id = uuid.UUID(game_id)
    try:
        service = GameService(db)
        game = await service.start_game(game_id)
        return game
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при запуске игры: {str(e)}")


@game_router.api_route("/api/connect_player/{game_code}", methods=["POST"])
async def connect_player(
        game_code: str,
        data: Dict[str, Any],
        db: AsyncSession = Depends(get_db)
):
    """Добавить игрока в игру"""
    # Валидация обязательных полей

    required_fields = ["name", "player_location_lat", "player_location_lng"]
    for field in required_fields:
        if field not in data:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required field: {field}"
            )

    # Валидация координат
    center_lat = data["player_location_lat"]
    center_lng = data["player_location_lng"]
    if not (-90 <= center_lat <= 90):
        raise HTTPException(status_code=422, detail="Invalid latitude")
    if not (-180 <= center_lng <= 180):
        raise HTTPException(status_code=422, detail="Invalid longitude")

    try:
        service = GameService(db)
        new_player = await service.add_player(game_code, data)
        game = await service.get_game_with_relations(new_player.game_id)

        await connection_manager.broadcast_to_game({

        },
            game_id=game.id
        )

        return {
            "game_id": "uuid-игры",
            "player_id": new_player.id,
            "player_name": new_player.name,
            "game_status": "WAITING",
            "ws_url": "ws://your-server.com/ws/{game_id}/{player_id}"
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при добавлении игрока: {str(e)}")




