"""Ограниченная история текстового чата по игре и игровой стороне.

История находится в памяти процесса и исчезает при его перезапуске.
"""
import asyncio
from collections import deque
from datetime import datetime, timezone
from uuid import uuid4

from database.models import Game, GameStatus, Player, Role
from sqlalchemy import select

MAX_MESSAGE_LENGTH = 500
HISTORY_LIMIT = 100


class FactionChat:
    def __init__(self):
        self._messages = {}
        self._lock = asyncio.Lock()

    async def history(self, game_id, side):
        async with self._lock:
            return list(self._messages.get((game_id, side), ()))

    async def append(self, game_id, side, sender, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Введите сообщение')
        text = text.strip()
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ValueError(f'Сообщение не должно превышать {MAX_MESSAGE_LENGTH} символов')
        message = {'id': str(uuid4()), 'sender_id': str(sender.id), 'sender_name': sender.name,
                   'text': text, 'sent_at': datetime.now(timezone.utc).isoformat()}
        async with self._lock:
            self._messages.setdefault((game_id, side), deque(maxlen=HISTORY_LIMIT)).append(message)
        return message

    def clear_game(self, game_id):
        for key in list(self._messages):
            if key[0] == game_id:
                del self._messages[key]


faction_chat = FactionChat()


async def handle_faction_chat(db, manager, game_id, player_id, message_type, data):
    game = await db.get(Game, game_id, populate_existing=True)
    rows = (await db.execute(select(Player, Role.victory_condition)
            .join(Role, Player.role_id == Role.id).where(Player.game_id == game_id))).all()
    own = next(((player, side) for player, side in rows if player.id == player_id), None)
    if not game or game.status not in (GameStatus.HIDE_TIME, GameStatus.ACTIVE) or not own or not own[0].is_alive:
        raise ValueError('Чат доступен только активным участникам игры')
    player, side = own
    if message_type == 'chat_history_request':
        await manager.send_personal({'type': 'chat_history', 'data': {'messages': await faction_chat.history(game_id, side)}}, player_id)
        return
    message = await faction_chat.append(game_id, side, player, data.get('text'))
    for recipient, recipient_side in rows:
        if recipient_side == side and recipient.is_alive:
            await manager.send_personal({'type': 'chat_message', 'data': message}, recipient.id)
