"""Подключение к PostgreSQL; схема управляется Alembic, а не запуском сервера."""
import os

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
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    if AsyncSessionLocal is None:
        await init_db()
    async with AsyncSessionLocal() as session:
        yield session
