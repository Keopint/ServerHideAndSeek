from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from database.models import Game, GameStatus, GameZone
from services import timers
from services.player import PlayerService
from utils.geo import calculate_distance, choose_next_zone_center


@pytest.mark.parametrize('lat,lng', [(55.75, 37.61), (0.0, 179.999), (80.0, -45.0)])
def test_next_zone_is_contained(lat, lng):
    for _ in range(100):
        next_lat, next_lng = choose_next_zone_center(lat, lng, 500, 150)
        assert calculate_distance(lat, lng, next_lat, next_lng) + 150 <= 500.00001


@pytest.mark.asyncio
async def test_shrink_announces_updates_center_and_eliminates_all_outside(monkeypatch):
    game_id, zone_id = uuid4(), uuid4()
    game = SimpleNamespace(status=GameStatus.ACTIVE, min_zone_radius=20.0, zone_shrink_interval=10,
                           safe_zone_center_lat=0.0, safe_zone_center_lng=0.0, safe_zone_radius=100.0)
    zone = SimpleNamespace(id=zone_id, radius=100.0, center_lat=0.0, center_lng=0.0, zone_data={})
    outside = [SimpleNamespace(id=uuid4(), is_alive=True, health=100, location_lat=0.01, location_lng=0.0) for _ in range(2)]
    inside = SimpleNamespace(id=uuid4(), is_alive=True, health=100, location_lat=0.0, location_lng=0.0)
    async def get(model, key):
        return game if model is Game else zone if model is GameZone else None
    db = SimpleNamespace(get=AsyncMock(side_effect=get), commit=AsyncMock(), refresh=AsyncMock(),
                         execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: outside + [inside]))))
    async def fake_db():
        yield db
    manager = timers.TimerManager()
    manager.schedule = AsyncMock()
    broadcasts = []
    async def broadcast(game_id, message, **kwargs):
        broadcasts.append(message)
    monkeypatch.setattr(timers, 'get_db', fake_db)
    monkeypatch.setattr(timers.connection_manager, 'broadcast_to_game', broadcast)
    monkeypatch.setattr(timers.connection_manager, 'send_personal', AsyncMock())
    monkeypatch.setattr(timers.connection_manager, 'disconnect', lambda *_: None)
    finish = AsyncMock()
    monkeypatch.setattr(PlayerService, '_finish_game_if_side_eliminated', finish)

    await manager.safe_zone_schedule(game_id, zone, datetime.now(timezone.utc) + timedelta(seconds=100), db)
    planned = zone.zone_data['next_safe_zone']
    assert broadcasts[0]['type'] == 'next_safe_zone'
    assert calculate_distance(0, 0, planned['center_lat'], planned['center_lng']) + planned['radius'] <= 100.00001
    callback = manager.schedule.await_args.args[4]
    await callback()
    assert game.safe_zone_center_lat == planned['center_lat']
    assert game.safe_zone_center_lng == planned['center_lng']
    assert game.safe_zone_radius == planned['radius']
    assert all(not p.is_alive and p.health == 0 for p in outside)
    assert inside.is_alive and inside.health == 100
    finish.assert_awaited_once_with(game_id)
    assert any(message['type'] == 'update_safe_zone' for message in broadcasts)


@pytest.mark.asyncio
async def test_no_preview_when_next_interval_is_after_round(monkeypatch):
    game_id = uuid4()
    game = SimpleNamespace(status=GameStatus.ACTIVE, min_zone_radius=20.0, zone_shrink_interval=60)
    zone = SimpleNamespace(id=uuid4(), radius=100.0, center_lat=0.0, center_lng=0.0, zone_data={})
    async def get(model, key):
        return game if model is Game else zone
    db = SimpleNamespace(get=AsyncMock(side_effect=get), commit=AsyncMock())
    async def fake_db():
        yield db
    manager = timers.TimerManager()
    manager.schedule = AsyncMock()
    broadcast = AsyncMock()
    monkeypatch.setattr(timers, 'get_db', fake_db)
    monkeypatch.setattr(timers.connection_manager, 'broadcast_to_game', broadcast)
    await manager.safe_zone_schedule(game_id, zone, datetime.now(timezone.utc) + timedelta(seconds=30), db)
    assert 'next_safe_zone' not in zone.zone_data
    manager.schedule.assert_not_awaited()
    assert broadcast.await_args.args[1]['data']['available'] is False
