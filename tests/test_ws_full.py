import json
import time
import uuid

import requests
import websocket

API_URL = "http://localhost:8000/api/games/create"
WS_BASE = "ws://localhost:8000/ws"

def create_game():
    payload = {
        "name": "Тестовая игра",
        "center_lat": 55.751244,
        "center_lng": 37.618423,
        "safe_zone_radius": 500.0,
        "min_zone_radius": 50.0,
        "zone_shrink_interval": 120,
        "game_duration": 1800,
        "time_to_hide": 300,
        "host_player": {
            "host_name": "Keopint",
            "host_player_location_lat": 20.0,
            "host_player_location_lng": 20.0
        },
        "game_roles": {
            "Amogus": {
                "health": 200,
                "victory_condition": "SEEKER"
            },
            "Hunter": {
                "health": 100,
                "victory_condition": "SEEKER"
            },
            "Hider": {
                "health": 100,
                "victory_condition": "HIDER"
            }
        },
        "roles_abilities": {
            "Amogus": {
                "TRAP": {
                    "duration_seconds": 600,
                    "number_uses": 2,
                    "recharge_time": 60,
                    "addition_data": {
                        "radius": 10.0,
                        "trap_duration_seconds": 120
                    }
                },
                "SCAN": {
                    "duration_seconds": 600,
                    "number_uses": 2,
                    "recharge_time": 60,
                    "addition_data": {}
                }
            },
            "Hunter": {
                "PERSONAL_BOMB": {
                    "duration_seconds": 600,
                    "number_uses": 2,
                    "recharge_time": 60,
                    "addition_data": {
                        "radius": 10.0,
                        "damage": 100
                    }
                }
            },
            "Hider": {
                "SNARE": {
                    "duration_seconds": 600,
                    "number_uses": 2,
                    "recharge_time": 60,
                    "addition_data": {
                        "radius": 10.0,
                        "trap_duration_seconds": 600
                    }
                }
            }
        },
        "events": ["BOMB", "AIRDROP", "BOMBARDMENT"],
        "roles_events": {
            "Amogus": ["BOMB", "AIRDROP", "BOMBARDMENT"],
            "Hunter": ["BOMB", "BOMBARDMENT"],
            "Hider": ["BOMB", "AIRDROP", "BOMBARDMENT"]
        },
        "events_configurations": {
            "BOMB": {
                "activation_frequency": "FREQUENT",
                "addition_data": {
                    "duration_seconds": 600,
                    "radius": 10.0,
                    "damage": 100
                }
            },
            "AIRDROP": {
                "activation_frequency": "RARE",
                "addition_data": {
                    "radius": 10.0
                }
            },
            "BOMBARDMENT": {
                "activation_frequency": "COMMON",
                "addition_data": {
                    "duration_seconds": 600,
                    "radius": 5.0,
                    "damage": 50
                }
            }
        }
    }
    resp = requests.post(API_URL, json=payload)
    resp.raise_for_status()
    data = resp.json()
    print(data)
    # Структура ответа зависит от вашего эндпоинта. Пример:
    game_id = uuid.UUID(data["game"]["id"])
    # Извлеките player_id из ответа (может быть в data["player_id"] или в списке players)
    player_id = uuid.UUID(data["host_player_id"])  # подстройте под свой API
    print(f"Игра создана: game_id={game_id}, player_id={player_id}")
    return game_id, player_id


import asyncio
import websockets

async def websocket_client():
    game_id, player_id = create_game()
    uri = f"ws://localhost:8000/ws/{game_id}/{player_id}"
    try:
        async with websockets.connect(uri) as websocket:

            init_msg = await websocket.recv()
            print("INIT:", init_msg)

            request = {
                "type": "update_location",
                "data": {
                    "lat": 20.2443,
                    "lng": 33.2453
                }
            }

            await websocket.send(json.dumps(request))

            response = await websocket.recv()
            print("RESPONSE:", response)
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"Соединение закрыто с ошибкой: {e}")
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(websocket_client())


