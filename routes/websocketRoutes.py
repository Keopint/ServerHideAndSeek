# websocketRoutes.py
import json
import uuid
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from database.models import GameStatus, PlayerDeathCauses
from database.db import get_db
from services.game_management import GameService
from services.websocket_manager import connection_manager
from services.player import PlayerService
from utils.conversions import to_dict

# Типы сообщений, которые клиент может отправлять
CLIENT_MESSAGE_TYPES = {
    "ping",                 # проверка связи
    "update_location",      # обновление геопозиции
    "use_ability",          # использование способности
    "change_role",          # запрос на изменение роли
    "change_ready_status",  # запрос на изменение статуса готовности
    "get_out_of_the_game",  # запрос на выбывание из игры
    "get_game_state",       # запрос полного состояния игры
    "hunter_found_player"
}

async def handle_client_message(
    game_id: uuid.UUID,
    player_id: uuid.UUID,
    message: dict,
    db: AsyncSession
):
    """Обработчик входящих сообщений от клиента."""

    msg_type = message.get("type")
    data = message.get("data", {})
    player_service = PlayerService(db)

    if msg_type == "ping":
        # Ответить pong с серверным временем
        await connection_manager.send_personal({
            "type": "pong",
            "data": {
                "server_time": datetime.now(timezone.utc).isoformat()
            }
        }, player_id)

    elif msg_type == "change_role":
        new_role_id = data.get("role_id")
        if new_role_id is None:
            await connection_manager.send_personal({
                "type": "role_changed",
                "data": {
                    "role_id": str(new_role_id)
                }
            }, player_id)
            return
        try:
            new_role_id = uuid.UUID(new_role_id)
            await player_service.change_player_role(game_id, player_id, new_role_id)
            await db.commit()

            await connection_manager.send_personal({
                "type": "role_changed",
                "data": {
                    "role_id": str(new_role_id)
                }
            }, player_id)
            return
        except ValueError as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": str(e)
            }, player_id)

    elif msg_type == "change_ready_status":
        new_status = data.get("status", False)
        if new_status is None:
            await connection_manager.send_personal({
                "type": "error",
                "message": "Missing new_status"
            }, player_id)
            return
        try:
            await player_service.change_ready_status(game_id, player_id, new_status)
            await db.commit()
            await connection_manager.send_personal({
                "type": "ready_status_changed",
                "data": {
                    "status": new_status
                }
            }, player_id)
            players = await player_service.get_players_in_game(game_id=game_id)
            all_is_ready = True
            for player in players:
                if not player.is_player_ready:
                    all_is_ready = False
                    break
            if all_is_ready:
                game_service = GameService(db)
                await game_service.start_game(game_id=game_id)
        except ValueError as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": str(e)
            }, player_id)

    elif msg_type == "update_location":
        lat = data.get("lat")
        lng = data.get("lng")
        if lat is None or lng is None:
            await connection_manager.send_personal({
                "type": "error",
                "message": "Missing lat/lng"
            }, player_id)
            return

        # Обновить позицию игрока через сервис
        try:
            await player_service.update_player_location(game_id, player_id, lat, lng)
            await db.commit()
            # Оповестить всех игроков в игре об изменении локации (кроме отправителя)
            await connection_manager.broadcast_to_game(
                game_id,
                {
                    "type": "player_moved",
                    "data": {
                        "player_id": str(player_id),
                        "location_lat": lat,
                        "location_lng": lng,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                },
                exclude_player=None
            )
        except ValueError as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": str(e)
            }, player_id)

    elif msg_type == "use_ability":
        game_service = GameService(db)
        game_status = await game_service.get_status(game_id)

        # Проверяем, активна ли игра
        if game_status != GameStatus.ACTIVE:
            await connection_manager.send_personal({
                "type": "error",
                "message": "Game is not active!"
            }, player_id)
            return

        ability_type = data.get("ability_type")
        if not ability_type:
            await connection_manager.send_personal({
                "type": "error",
                "message": "Missing ability_type"
            }, player_id)
            return

        try:
            result = await player_service.use_ability(game_id, player_id, ability_type)
            await db.commit()
            # О результате использования способности сервис сам разошлет уведомления через TimerManager
            # Можно также сразу подтвердить игроку
            await connection_manager.send_personal({
                "type": "ability_used",
                "data": {
                    "ability": ability_type,
                    "result": result
                }
            }, player_id)
        except ValueError as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": str(e)
            }, player_id)

    elif msg_type == "get_game_state":
        # Отправить игроку актуальное состояние игры (зоны, игроки, эффекты)
        try:
            game_service = GameService(db)
            game_with_relation = await game_service.get_game_with_relations(game_id)
            players_state = await player_service.get_player_in_game(game_id, player_id)
            await connection_manager.send_personal({
                "type": "game_state",
                "data": {
                    "game_info": to_dict(game_with_relation),
                    "player_info":to_dict(players_state)
                }
            }, player_id)
        except Exception as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": f"Failed to get state: {e}"
            }, player_id)

    elif msg_type == "hunter_found_player":
        try:
            hunter_player_id = player_id
            founded_player_id = uuid.UUID(data.get("founded_player_id"))
            await player_service.player_died(game_id, founded_player_id, PlayerDeathCauses.HUNTER_FOUND_PLAYER, hunter_player_id)

        except Exception as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": f"Failed to found player: {e}"
            }, player_id)
    else:
        await connection_manager.send_personal({
            "type": "error",
            "message": f"Unknown message type: {msg_type}"
        }, player_id)


# регистрация websocket_endpoint
def register_websocket_endpoint(app):
    print("=== Registering WebSocket endpoint ===")
    @app.websocket("/ws/{game_id}/{player_id}")
    async def websocket_endpoint(websocket: WebSocket, game_id: str, player_id: str):
        try:
            game_id = uuid.UUID(game_id)
            player_id = uuid.UUID(player_id)

            db_gen = get_db()
            db = await anext(db_gen)

            try:
                game_service = GameService(db)
                game = await game_service.get_game(game_id)
                if game is None:
                    await websocket.close(code=4004, reason="Game not found")
                    return

                player_service = PlayerService(db)

                player = await player_service.get_player_in_game(game_id, player_id)
                if player is None:
                    await websocket.close(code=4001, reason="Player not in game")
                    return

                await connection_manager.connect(game_id, player_id, websocket)

                # Оповестить других
                await connection_manager.broadcast_to_game(
                    game_id,
                    {
                        "type": "player_online",
                        "player_id": str(player_id),
                        "player_name": str(player.name),
                        "role": str(player.role_ref.name) if player.role_ref else None  # исправлено
                    },
                    exclude_player=player_id
                )

                # Отправить начальное состояние (player_service уже создан)
                initial_state = await player_service.get_player_in_game(game_id, player_id)
                game_data = await game_service.get_game_with_relations(game_id)

                await connection_manager.send_personal({
                    "type": "websocket_connected_player",
                    "data": {
                        "player_data": to_dict(initial_state),
                        "game_data": to_dict(game_data)
                    }
                }, player_id)

                # Цикл сообщений
                while True:
                    try:
                        raw_message = await websocket.receive_text()
                        message = json.loads(raw_message)
                        await handle_client_message(game_id, player_id, message, db)
                    except json.JSONDecodeError:
                        await connection_manager.send_personal({
                            "type": "error",
                            "message": "Invalid JSON"
                        }, player_id)
                    except WebSocketDisconnect:
                        break  # просто выходим
                    except Exception as e:
                        print(f"[WS] Error processing message: {e}")
                        await connection_manager.send_personal({
                            "type": "error",
                            "message": "Internal server error"
                        }, player_id)

            except Exception as e:
                print(f"[WS] Unexpected error: {e}")
                try:
                    await websocket.close(code=1011, reason="Internal error")
                except:
                    pass
            finally:
                # Единоразовая очистка
                connection_manager.disconnect(player_id=player_id)
                try:
                    player_service = PlayerService(db)
                    player = await player_service.get_player_in_game(game_id, player_id)
                    player.is_online = False
                    await connection_manager.broadcast_to_game(
                        game_id,
                        {"type": "player_offline", "player_id": str(player_id)},
                        exclude_player=player_id
                    )
                except Exception as e:
                    print(f"[WS] Failed to send offline: {e}")
                await db.close()
        except Exception as e:
            print(f"!!! WebSocket fatal error: {e}")
            import traceback
            traceback.print_exc()
            await websocket.close(code=1011, reason=str(e))