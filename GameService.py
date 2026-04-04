import uuid

from db import get_db
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from models import Game, Player
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func



class GameService:
    def __init__(self, db: AsyncSession):
        self.db = db

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
            select(Player).where(Player.game_id == game_id)
        ).scalars().all()
        return players

    async def get_game_player(self, game_id: uuid.UUID, player_id: uuid.UUID) -> Player:
        """
        Получить список всех игроков в игре
        """

        player = await self.db.execute(
            select(Player).where(Player.game_id == game_id)
        ).scalars()

        #return players

    async def get_alive_players(self, game_id: uuid.UUID) -> list[Player]:
        """
        Получить список живых игроков в игре
        """
        alive_players = await self.db.execute(
            select(Player)
            .where(Player.game_id == game_id)
            .where(Player.is_alive == True)
        ).scalars().all()

        return alive_players


