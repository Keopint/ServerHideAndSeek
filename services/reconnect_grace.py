"""Отложенное исключение при обрыве соединения одного процесса сервера."""
import asyncio
from datetime import datetime, timezone

from services.reconnect_deadline import reconnect_deadline


class ReconnectGrace:
    def __init__(self):
        self._tasks = {}
        self._deadlines = {}

    def allows(self, game_id, player_id):
        deadline = self._deadlines.get((game_id, player_id))
        return deadline is not None and datetime.now(timezone.utc) < deadline

    def has_deadline(self, game_id, player_id):
        return (game_id, player_id) in self._deadlines

    def cancel(self, game_id, player_id):
        task = self._tasks.pop((game_id, player_id), None)
        self._deadlines.pop((game_id, player_id), None)
        if task is not None:
            task.cancel()

    def start(self, game_id, player_id, disconnected_at, expire):
        key = (game_id, player_id)
        self.cancel(game_id, player_id)
        self._deadlines[key] = reconnect_deadline(disconnected_at)

        async def wait():
            try:
                delay = max(0, (reconnect_deadline(disconnected_at) - datetime.now(timezone.utc)).total_seconds())
                await asyncio.sleep(delay)
                # Не прерываем транзакцию исключения при позднем подключении.
                if self._tasks.get(key) is asyncio.current_task():
                    self._tasks.pop(key, None)
                await expire(game_id, player_id)
            finally:
                if self._tasks.get(key) is asyncio.current_task():
                    self._tasks.pop(key, None)

        self._tasks[key] = asyncio.create_task(wait())


reconnect_grace = ReconnectGrace()


async def expire_disconnected_player(game_id, player_id):
    from database.db import get_db
    from database.models import Game, GameStatus, Player, PlayerDeathCauses
    from services.player import PlayerService
    from services.websocket_manager import connection_manager

    async for db in get_db():
        if connection_manager.is_online(player_id):
            return
        game = await db.get(Game, game_id)
        player = await db.get(Player, player_id)
        if (game and game.status in (GameStatus.HIDE_TIME, GameStatus.ACTIVE)
                and player and player.game_id == game_id and player.is_alive
                and not connection_manager.is_online(player_id)):
            await PlayerService(db).player_died(game_id, player_id, PlayerDeathCauses.HP_ARE_OVER)
        return
