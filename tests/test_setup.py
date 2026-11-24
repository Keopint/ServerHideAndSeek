"""Проверки конфигурации без подключения к пользовательской БД."""
from types import SimpleNamespace
from uuid import uuid4

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
            return SimpleNamespace(id=game_id)

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
