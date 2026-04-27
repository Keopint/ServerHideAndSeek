import uuid
from fastapi import HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from services.game_management import GameService
from services.player import PlayerService
from database.db import get_db
from fastapi import APIRouter

info_router = APIRouter()

@info_router.api_route("/api/games/{game_id}/info", methods=["GET"])
async def get_game_endpoint(
    game_id: str,
    db: AsyncSession = Depends(get_db)
):
    game_id = uuid.UUID(game_id)
    service = GameService(db)
    try:
        game = await service.get_game(game_id)
        return game
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@info_router.api_route("/api/games/{game_id}/players_info", methods=["GET"])
async def get_game_players_info(
        game_id: str,
        db: AsyncSession = Depends(get_db)
):
    game_id = uuid.UUID(game_id)
    service = PlayerService(db)
    try:
        # Получаем список игроков
        players = await service.get_players_in_game(game_id)

        # JSON ответ
        return {
            "game_id": str(game_id),
            "players_count": len(players),
            "players": [
                {
                    "id": str(player.id),
                    "name": player.name,
                    "role_id": player.role_id,
                    "health": player.health,
                    "is_alive": player.is_alive,
                    "lat": player.location_lat,
                    "lng": player.location_lng,
                    "last_location_update": player.last_location_update.isoformat() if player.last_location_update else None,
                    "is_player_ready": player.is_player_ready
                }
                for player in players
            ]
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@info_router.api_route("/api/games/{game_id}/player_info/{player_id}", methods=["GET"])
async def get_game_player_info(
        game_id: str,
        player_id: str,
        db: AsyncSession = Depends(get_db)
):
    game_id = uuid.UUID(game_id)
    player_id = uuid.UUID(player_id)
    service = PlayerService(db)
    try:
        # Получаем список игроков
        player = await service.get_player_in_game(game_id, player_id)

        # JSON ответ
        return player

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
