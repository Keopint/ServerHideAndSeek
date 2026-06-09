# # database.py
# import os
# from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
# from sqlalchemy.orm import sessionmaker
# from database.models import Base
# from sqlalchemy import text
#
# # old version: DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./database/geogame.db?timeout=30")
# DATABASE_URL = "postgresql+asyncpg://postgres:284016358@localhost:5432/geogame"
#
# engine = None
# AsyncSessionLocal = None
#
# async def init_db():
#     global engine, AsyncSessionLocal
#     global engine, AsyncSessionLocal
#
#     # **Шаг 1: Увеличиваем таймаут и подготавливаем подключение**
#     # ? Добавьте timeout=30 в строку подключения
#     database_url = DATABASE_URL
#     engine = create_async_engine(database_url, echo=True)
#
#     # **Шаг 2: Включаем WAL и IMMEDIATE ТОЛЬКО один раз**
#     async with engine.begin() as conn:
#         # Включаем WAL (достаточно выполнить один раз)
#         await conn.execute(text("PRAGMA journal_mode=WAL;"))
#         # Устанавливаем режим IMMEDIATE для всех транзакций
#         # (выполняется при каждом старте приложения)
#         await conn.execute(text("PRAGMA synchronous=NORMAL;"))
#
#     AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
#
#     async with engine.begin() as conn:
#         await conn.run_sync(Base.metadata.create_all)
#         print("База данных инициализирована.")
#
# async def get_db():
#     """Возвращает сессию базы данных. Использовать: async for db in get_db()"""
#     if AsyncSessionLocal is None:
#         await init_db()
#     async with AsyncSessionLocal() as session:
#         yield session

# database.py
import os

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from database.models import Base

DATABASE_URL = "postgresql+asyncpg://postgres:284016358@localhost:5432/geogame"

engine = None
AsyncSessionLocal = None

async def init_db():
    global engine, AsyncSessionLocal
    # Создаём асинхронный движок для PostgreSQL
    engine = create_async_engine(DATABASE_URL, echo=True, pool_pre_ping=True)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Создаём таблицы (если их нет)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("База данных PostgreSQL инициализирована.")

async def get_db():
    """Возвращает сессию базы данных. Использовать: async for db in get_db()"""
    if AsyncSessionLocal is None:
        await init_db()
    async with AsyncSessionLocal() as session:
        yield session