import random
import string
import uuid
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Game
from sqlalchemy import select


async def generate_game_join_code(db: AsyncSession, length: int = 6) -> str:
    """
    Генерирует уникальный 6-символьный код для присоединения к игре.
    Код состоит из заглавных букв и цифр (исключены похожие символы: 0, O, I, 1).
    """
    # Набор символов: исключаем путаницу (0, O, I, 1)
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    max_attempts = 10

    for _ in range(max_attempts):
        code = ''.join(random.choices(chars, k=length))
        # Проверяем уникальность кода в таблице игр
        stmt = select(Game).where(
            Game.game_code == code
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if not existing:
            return code

    # Если после нескольких попыток не удалось найти уникальный код – возможно, мало свободных.
    # Можно увеличить длину или использовать UUID с обрезанием.
    raise RuntimeError("Не удалось сгенерировать уникальный код для игры")