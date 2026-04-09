import asyncio
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from typing import Any, Dict
from models import (Game, Player, PlayerRole, Ability, AbilityType, EventType, Zone, ZoneType,
                    role_abilities, Role, game_roles, role_events, Event)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func
from GameService import GameService
from db import init_db, get_db
from fastapi import APIRouter

info_router = APIRouter()

@info_router.api_route("/api/games/{game_id}/info", methods=["GET"])
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


@info_router.api_route("/api/games/{game_id}/players_info", methods=["GET"])
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


@info_router.api_route("/api/games/{game_id}/player_info/{player_id}", methods=["GET"])
async def get_game_players_info(
        game_id: uuid.UUID,
        player_id: uuid.UUID,
        db: AsyncSession = Depends(get_db)
):
    service = GameService(db)
    try:
        # Получаем список игроков
        player = await service.get_game_player(game_id, player_id)

        # JSON ответ
        return {
            "game_id": str(game_id),
            "player":
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
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
