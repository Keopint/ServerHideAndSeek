import json
import uuid
import pytest
import asyncio

import pytest_asyncio
import websockets
import requests
from typing import Tuple

API_URL = "http://localhost:8000/api/games/create"
WS_BASE = "ws://localhost:8000/ws"

def create_game_payload() -> dict:
    return {
        "name": "TestGame",
        "center_lat": 55.751244,
        "center_lng": 37.618423,
        "safe_zone_radius": 500.0,
        "min_zone_radius": 50.0,
        "zone_shrink_interval": 120,
        "game_duration": 1800,
        "time_to_hide": 300,
        "host_player": {
            "host_name": "Host",
            "host_player_location_lat": 20.0,
            "host_player_location_lng": 20.0,
        },
        "game_roles": {
            "Seeker": {"health": 100, "victory_condition": "SEEKER"},
            "Hider": {"health": 100, "victory_condition": "HIDER"},
        },
        "roles_abilities": {},
        "events": [],
        "roles_events": {},
        "events_configurations": {},
    }

def create_game() -> Tuple[uuid.UUID, uuid.UUID]:
    resp = requests.post(API_URL, json=create_game_payload())
    resp.raise_for_status()
    data = resp.json()
    game_id = uuid.UUID(data["game"]["id"])
    host_id = uuid.UUID(data["host_player_id"])
    return game_id, host_id

@pytest_asyncio.fixture
async def game_and_host():
    game_id, host_id = create_game()
    uri = f"{WS_BASE}/{game_id}/{host_id}"
    async with websockets.connect(uri) as ws:
        # Дожидаемся приветственного сообщения
        init_msg = await ws.recv()
        init_data = json.loads(init_msg)
        assert init_data["type"] == "websocket_connected_player"
        yield game_id, host_id, ws

@pytest_asyncio.fixture
async def second_player(game_and_host):
    game_id, host_id, host_ws = game_and_host
    # Получаем game_code через API
    game_info = requests.get(f"http://localhost:8000/api/games/{game_id}/info").json()
    game_code = game_info["game_code"]
    join_payload = {
        "name": "Player2",
        "player_location_lat": 20.0,
        "player_location_lng": 20.0,
    }
    resp = requests.post(f"http://localhost:8000/api/connect_player/{game_code}", json=join_payload)
    resp.raise_for_status()
    join_data = resp.json()
    player2_id = uuid.UUID(join_data["player_id"])
    uri2 = f"{WS_BASE}/{game_id}/{player2_id}"
    async with websockets.connect(uri2) as ws2:
        # Пропускаем приветственное сообщение
        init_msg2 = await ws2.recv()
        assert json.loads(init_msg2)["type"] == "websocket_connected_player"
        yield player2_id, ws2

@pytest.mark.asyncio
async def test_ping(game_and_host):
    _, _, ws = game_and_host
    ping_msg = {"type": "ping", "data": {}}
    await ws.send(json.dumps(ping_msg))
    resp = await ws.recv()
    resp_data = json.loads(resp)
    assert resp_data["type"] == "pong"
    assert "server_time" in resp_data["data"]

@pytest.mark.asyncio
async def test_update_location(game_and_host, second_player):
    game_id, host_id, ws = game_and_host
    _, ws2 = second_player
    loc_msg = {
        "type": "update_location",
        "data": {"lat": 55.123, "lng": 37.456}
    }
    await ws.send(json.dumps(loc_msg))
    # Проверяем, что второй игрок получил player_moved
    resp2 = await ws2.recv()
    resp2_data = json.loads(resp2)
    assert resp2_data["type"] == "player_moved"
    assert resp2_data["data"]["player_id"] == str(host_id)
    assert resp2_data["data"]["location_lat"] == 55.123
    assert resp2_data["data"]["location_lng"] == 37.456

@pytest.mark.asyncio
async def test_change_role(game_and_host):
    game_id, host_id, ws = game_and_host
    game_info = requests.get(f"http://localhost:8000/api/games/{game_id}/info").json()
    roles = game_info["roles"]
    role_id = roles[0]["id"]
    change_role_msg = {
        "type": "change_role",
        "data": {"role_id": role_id}
    }
    await ws.send(json.dumps(change_role_msg))
    resp = await ws.recv()
    resp_data = json.loads(resp)
    assert resp_data["type"] == "role_changed"
    assert resp_data["data"]["role_id"] == role_id

@pytest.mark.asyncio
async def test_change_ready_status(game_and_host):
    _, _, ws = game_and_host
    ready_msg = {
        "type": "change_ready_status",
        "data": {"status": True}
    }
    await ws.send(json.dumps(ready_msg))
    resp = await ws.recv()
    resp_data = json.loads(resp)
    assert resp_data["type"] == "ready_status_changed"
    assert resp_data["data"]["status"] is True

@pytest.mark.asyncio
async def test_use_ability(game_and_host):
    # Для теста способности требуется активная игра, поэтому временно пропускаем
    pytest.skip("Требуется активная игра")

@pytest.mark.asyncio
async def test_get_game_state(game_and_host):
    _, _, ws = game_and_host
    state_msg = {"type": "get_game_state", "data": {}}
    await ws.send(json.dumps(state_msg))
    resp = await ws.recv()
    resp_data = json.loads(resp)
    print(resp_data)
    # Исправлено: ожидаем тип "game_state" (предполагаем, что сервер исправлен)
    assert resp_data["type"] == "game_state"
    assert "game_code" in resp_data["data"]["game_info"]

@pytest.mark.asyncio
async def test_hunter_found_player(game_and_host, second_player):
    game_id, host_id, ws = game_and_host
    player2_id, ws2 = second_player

    # Даём время на доставку приветственных сообщений
    await asyncio.sleep(0.2)

    # Очищаем буфер сообщений у первого игрока
    while True:
        try:
            # Пытаемся прочитать сообщение с таймаутом 0.1 сек
            msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
            # Если пришло – оно нам не нужно, просто игнорируем (логируем при желании)
            # print(f"Cleared message: {msg}")
        except asyncio.TimeoutError:
            # Больше сообщений нет
            break

    # Отправляем hunter_found_player
    found_msg = {
        "type": "hunter_found_player",
        "data": {"founded_player_id": str(player2_id)}
    }
    await ws.send(json.dumps(found_msg))

    # Второй игрок получает you_died
    resp2 = await ws2.recv()
    resp2_data = json.loads(resp2)
    assert resp2_data["type"] == "you_died"
    assert resp2_data["data"]["reason"] == "HUNTER_FOUND_PLAYER"
    assert resp2_data["data"]["hunter_player_id"] == str(host_id)

    # Охотник получает broadcast player_died
    broadcast = await ws.recv()
    broadcast_data = json.loads(broadcast)
    assert broadcast_data["type"] == "player_died"
    assert broadcast_data["data"]["player_id"] == str(player2_id)

@pytest.mark.asyncio
async def test_unknown_message_type(game_and_host):
    _, _, ws = game_and_host
    bad_msg = {"type": "unknown_type", "data": {}}
    await ws.send(json.dumps(bad_msg))
    resp = await ws.recv()
    resp_data = json.loads(resp)
    assert resp_data["type"] == "error"
    assert "Unknown message type" in resp_data["message"]