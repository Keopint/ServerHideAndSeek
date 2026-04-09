# websocket_manager.py
import asyncio
from typing import Dict, Set, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
import json
import uuid
from datetime import datetime, timezone

class ConnectionManager:
    """
    Управляет WebSocket-соединениями игроков в разрезе игр.
    """
    def __init__(self):
        # game_id -> {player_id: WebSocket}
        self._game_connections: Dict[str, Dict[str, WebSocket]] = {}
        # player_id -> game_id (для быстрого поиска)
        self._player_game: Dict[str, str] = {}
        # game_id -> Set[player_id] для быстрой проверки состава игры
        self._game_players: Dict[str, Set[str]] = {}

    async def connect(self, game_id: uuid.UUID, player_id: uuid.UUID, websocket: WebSocket) -> None:
        """Принять соединение от игрока и добавить в комнату игры."""
        await websocket.accept()
        game_key = str(game_id)
        player_key = str(player_id)

        if game_key not in self._game_connections:
            self._game_connections[game_key] = {}
            self._game_players[game_key] = set()

        self._game_connections[game_key][player_key] = websocket
        self._player_game[player_key] = game_key
        self._game_players[game_key].add(player_key)

        print(f"[WS] Player {player_key} connected to game {game_key}")

    def disconnect(self, player_id: uuid.UUID) -> None:
        """Удалить соединение игрока."""
        player_key = str(player_id)
        game_key = self._player_game.pop(player_key, None)
        if game_key and game_key in self._game_connections:
            self._game_connections[game_key].pop(player_key, None)
            self._game_players[game_key].discard(player_key)
            # Если в игре не осталось соединений, можно удалить пустой словарь
            if not self._game_connections[game_key]:
                del self._game_connections[game_key]
                del self._game_players[game_key]
        print(f"[WS] Player {player_key} disconnected")

    async def send_personal(self, message: Any, player_id: uuid.UUID) -> bool:
        """Отправить сообщение конкретному игроку."""
        player_key = str(player_id)
        game_key = self._player_game.get(player_key)
        if not game_key:
            return False
        websocket = self._game_connections.get(game_key, {}).get(player_key)
        if not websocket:
            return False
        try:
            if isinstance(message, dict):
                await websocket.send_json(message)
            else:
                await websocket.send_text(str(message))
            return True
        except Exception as e:
            print(f"[WS] Failed to send to {player_key}: {e}")
            self.disconnect(player_id)
            return False

    async def broadcast_to_game(self, game_id: uuid.UUID, message: Any, exclude_player: Optional[uuid.UUID] = None) -> int:
        """Отправить сообщение всем игрокам в игре."""
        game_key = str(game_id)
        connections = self._game_connections.get(game_key, {})
        if not connections:
            return 0

        exclude_key = str(exclude_player) if exclude_player else None
        tasks = []
        for player_key, ws in connections.items():
            if player_key == exclude_key:
                continue
            try:
                if isinstance(message, dict):
                    tasks.append(ws.send_json(message))
                else:
                    tasks.append(ws.send_text(str(message)))
            except Exception as e:
                print(f"[WS] Error queuing for {player_key}: {e}")

        if not tasks:
            return 0

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        return success_count

    def get_connected_players(self, game_id: uuid.UUID) -> Set[str]:
        """Вернуть список ID игроков, у которых открыт WebSocket."""
        return self._game_players.get(str(game_id), set()).copy()

    def is_online(self, player_id: uuid.UUID) -> bool:
        """Проверить, подключен ли игрок в данный момент."""
        return str(player_id) in self._player_game


# Глобальный экземпляр менеджера (синглтон для приложения)
connection_manager = ConnectionManager()