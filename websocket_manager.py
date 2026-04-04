import asyncio
import json
from typing import Dict, Set
from fastapi import WebSocket


class ConnectionManager:
    """Управление WebSocket подключениями"""

    def __init__(self):
        # active_connections[game_id][player_id] = websocket
        self.active_connections: Dict[str, Dict[str, WebSocket]] = {}

    async def connect(self, game_id: str, player_id: str, websocket: WebSocket):
        await websocket.accept()

        if game_id not in self.active_connections:
            self.active_connections[game_id] = {}

        self.active_connections[game_id][player_id] = websocket

    def disconnect(self, game_id: str, player_id: str):
        if game_id in self.active_connections:
            if player_id in self.active_connections[game_id]:
                del self.active_connections[game_id][player_id]

            # Если в игре не осталось игроков, удаляем игру
            if len(self.active_connections[game_id]) == 0:
                del self.active_connections[game_id]

    async def send_to_player(self, game_id: str, player_id: str, message: dict):
        """Отправить сообщение конкретному игроку"""
        if game_id in self.active_connections:
            if player_id in self.active_connections[game_id]:
                try:
                    await self.active_connections[game_id][player_id].send_json(message)
                except:
                    pass

    async def broadcast_to_game(self, game_id: str, message: dict, exclude_player: str = None):
        """Отправить сообщение всем игрокам в игре"""
        if game_id in self.active_connections:
            for player_id, connection in self.active_connections[game_id].items():
                if player_id != exclude_player:
                    try:
                        await connection.send_json(message)
                    except:
                        pass

    async def broadcast_to_role(self, game_id: str, role: str, message: dict):
        """Отправить сообщение игрокам с определенной ролью"""
        if game_id in self.active_connections:
            # Нужно передать game объект извне, упростим для примера
            pass


manager = ConnectionManager()