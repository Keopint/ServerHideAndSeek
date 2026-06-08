# database.py
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from database.models import Base
from sqlalchemy import text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./database/geogame.db?timeout=30")

engine = None
AsyncSessionLocal = None

async def init_db():
    global engine, AsyncSessionLocal
    global engine, AsyncSessionLocal

    # **Шаг 1: Увеличиваем таймаут и подготавливаем подключение**
    # ? Добавьте timeout=30 в строку подключения
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./geogame.db?timeout=30")
    engine = create_async_engine(database_url, echo=True)

    # **Шаг 2: Включаем WAL и IMMEDIATE ТОЛЬКО один раз**
    async with engine.begin() as conn:
        # Включаем WAL (достаточно выполнить один раз)
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        # Устанавливаем режим IMMEDIATE для всех транзакций
        # (выполняется при каждом старте приложения)
        await conn.execute(text("PRAGMA synchronous=NORMAL;"))

    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("База данных инициализирована.")

async def get_db():
    """Возвращает сессию базы данных. Использовать: async for db in get_db()"""
    if AsyncSessionLocal is None:
        await init_db()
    async with AsyncSessionLocal() as session:
        yield session