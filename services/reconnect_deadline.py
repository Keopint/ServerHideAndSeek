"""Проверка срока повторного входа по сохранённому времени отключения.

Функции не меняют состояние игры: вызывающий код должен сохранять время
отключения и синхронизировать повторный вход с исключением игрока.
"""
from datetime import datetime, timedelta, timezone

RECONNECT_GRACE = timedelta(minutes=1)


def reconnect_deadline(disconnected_at: datetime) -> datetime:
    if disconnected_at.tzinfo is None or disconnected_at.utcoffset() is None:
        raise ValueError("Время отключения должно содержать часовой пояс")
    return disconnected_at.astimezone(timezone.utc) + RECONNECT_GRACE


def can_reconnect(disconnected_at: datetime, now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Текущее время должно содержать часовой пояс")
    return now.astimezone(timezone.utc) < reconnect_deadline(disconnected_at)
