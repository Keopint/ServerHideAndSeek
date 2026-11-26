"""Проверки конфигурации без подключения к пользовательской БД."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

import pytest
from fastapi.testclient import TestClient

from database.db import get_database_url
from main import app


def test_database_url_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        get_database_url()
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/example")
    with pytest.raises(RuntimeError, match="asyncpg"):
        get_database_url()


@pytest.mark.parametrize("public,expected", [
    (None, "ws://testserver/ws/"),
    ("https://example.test/prefix", "wss://example.test/prefix/ws/"),
])
def test_join_websocket_address(monkeypatch, public, expected):
    from database.db import get_db
    import routes.gameRoutes as routes

    game_id, player_id = uuid4(), uuid4()

    class FakeService:
        def __init__(self, db):
            pass

        async def add_player(self, code, data):
            return SimpleNamespace(id=player_id, game_id=game_id, name=data["name"])

        async def get_game_with_relations(self, game_id):
            return SimpleNamespace(id=game_id, status=SimpleNamespace(value='WAITING'))

    async def fake_db():
        yield None

    if public is None:
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("PUBLIC_BASE_URL", public)
    monkeypatch.setattr(routes, "GameService", FakeService)
    app.dependency_overrides[get_db] = fake_db
    try:
        # Без контекстного менеджера: тест не запускает startup и не подключается к БД.
        client = TestClient(app)
        response = client.post("/api/connect_player/ABC123", json={
            "name": "Tester", "player_location_lat": 55.0, "player_location_lng": 37.0,
        })
        assert response.status_code == 200
        assert response.json()["ws_url"] == f"{expected}{game_id}/{player_id}"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_init_db_ensures_phase_column(monkeypatch):
    import database.db as db_module

    executed = []

    class Connection:
        async def execute(self, statement):
            executed.append(str(statement))

    class Begin:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *args):
            return None

    class Engine:
        def begin(self):
            return Begin()

    monkeypatch.setattr(db_module, 'get_database_url', lambda: 'postgresql+asyncpg://test')
    monkeypatch.setattr(db_module, 'create_async_engine', lambda *args, **kwargs: Engine())
    monkeypatch.setattr(db_module, 'sessionmaker', lambda *args, **kwargs: object())

    old_engine, old_session = db_module.engine, db_module.AsyncSessionLocal
    db_module.engine, db_module.AsyncSessionLocal = None, None
    try:
        await db_module.init_db()
    finally:
        db_module.engine, db_module.AsyncSessionLocal = old_engine, old_session

    assert any('ADD COLUMN IF NOT EXISTS phase_ends_at' in statement for statement in executed)


@pytest.mark.asyncio
async def test_lifespan_restores_timers(monkeypatch):
    import main

    calls = []

    async def fake_init_db():
        calls.append('init_db')

    def fake_register(app):
        calls.append('register_ws')

    async def fake_recover_phase():
        calls.append('phase')

    async def fake_recover_zone_effects():
        calls.append('zone_effects')

    monkeypatch.setattr(main, 'init_db', fake_init_db)
    monkeypatch.setattr(main, 'register_websocket_endpoint', fake_register)
    monkeypatch.setattr('services.timer_recovery.recover_phase_timers', fake_recover_phase)
    monkeypatch.setattr('services.timer_recovery.recover_zone_and_effect_timers', fake_recover_zone_effects)

    async with main.lifespan(main.app):
        pass

    assert calls == ['init_db', 'register_ws', 'phase', 'zone_effects']
