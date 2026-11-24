import uuid
from fastapi import HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from services.game_management import GameService
from services.player import PlayerService
from database.db import get_db
from fastapi import APIRouter
from utils.conversions import to_dict

info_router = APIRouter()

@info_router.api_route("/api/games/{game_id}/info", methods=["GET"])
async def get_game_endpoint(
    game_id: str,
    db: AsyncSession = Depends(get_db)
):
    game_id = uuid.UUID(game_id)
    try:
        service = GameService(db)
        game_with_relations = await service.get_game_with_relations(game_id)
        from utils.conversions import game_to_dict
        return game_to_dict(game_with_relations)
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
        result = to_dict(player)
        if result:
            result.pop("location_lat", None)
            result.pop("location_lng", None)
            result.pop("last_location_update", None)
        return result

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
