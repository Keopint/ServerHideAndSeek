"""Временные запросы поимки для одного процесса сервера.

После перезапуска сервера ожидающие запросы исчезают, игрок может отправить новый.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class CatchRequest:
    id: uuid.UUID
    game_id: uuid.UUID
    hunter_id: uuid.UUID
    target_id: uuid.UUID
    expires_at: float


class CatchRequests:
    def __init__(self):
        self._requests: dict[uuid.UUID, CatchRequest] = {}
        self.lock = asyncio.Lock()

    def create(self, game_id, hunter_id, target_id):
        now = time.monotonic()
        self._requests = {key: value for key, value in self._requests.items() if value.expires_at > now}
        if any(r.game_id == game_id and r.target_id == target_id for r in self._requests.values()):
            raise ValueError('Игрок уже получил запрос на подтверждение')
        request = CatchRequest(uuid.uuid4(), game_id, hunter_id, target_id, now + 120)
        self._requests[request.id] = request
        return request

    def consume(self, request_id, game_id, target_id):
        request = self._requests.get(request_id)
        if not request or request.game_id != game_id or request.target_id != target_id:
            raise ValueError('Запрос не найден или адресован другому игроку')
        del self._requests[request_id]
        if request.expires_at <= time.monotonic():
            raise ValueError('Время подтверждения истекло')
        return request

    def discard(self, request_id):
        self._requests.pop(request_id, None)


catch_requests = CatchRequests()
