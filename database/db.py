"""Подключение к PostgreSQL.

Схема управляется Alembic. При запуске дополнительно выполняется
идемпотентная проверка колонок, добавленных последними миграциями:
это позволяет поднять сервер на БД, где миграция ещё не применялась.
"""
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

engine = None
AsyncSessionLocal = None


def get_database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("Укажите DATABASE_URL (postgresql+asyncpg://...) в переменных окружения")
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("DATABASE_URL должен использовать драйвер postgresql+asyncpg")
    return url


async def init_db():
    global engine, AsyncSessionLocal
    if engine is not None:
        return
    engine = create_async_engine(get_database_url(), pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(text(
            'ALTER TABLE games ADD COLUMN IF NOT EXISTS phase_ends_at TIMESTAMPTZ'
        ))
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    if AsyncSessionLocal is None:
        await init_db()
    async with AsyncSessionLocal() as session:
        yield session
