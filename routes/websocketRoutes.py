# websocketRoutes.py
import json
import uuid
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from database.db import get_db
from services.player import PlayerService
from services.game_management import GameService
from websocket_manager import connection_manager

# Типы сообщений, которые клиент может отправлять
CLIENT_MESSAGE_TYPES = {
    "ping",                 # проверка связи
    "update_location",      # обновление геопозиции
    "use_ability",          # использование способности
    "get_game_state",       # запрос полного состояния игры
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
            "server_time": datetime.now(timezone.utc).isoformat()
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
            # Оповестить всех игроков в игре об изменении локации (кроме отправителя)
            await connection_manager.broadcast_to_game(
                game_id,
                {
                    "type": "player_moved",
                    "player_id": str(player_id),
                    "location": {"lat": lat, "lng": lng},
                    "timestamp": datetime.now(timezone.utc).isoformat()
                },
                exclude_player=player_id
            )
        except ValueError as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": str(e)
            }, player_id)

    elif msg_type == "use_ability":
        ability_type = data.get("ability_type")
        if not ability_type:
            await connection_manager.send_personal({
                "type": "error",
                "message": "Missing ability_type"
            }, player_id)
            return

        try:
            result = await player_service.use_ability(game_id, player_id, ability_type)
            # О результате использования способности сервис сам разошлет уведомления через TimerManager
            # Можно также сразу подтвердить игроку
            await connection_manager.send_personal({
                "type": "ability_used",
                "ability": ability_type,
                "result": result
            }, player_id)
        except ValueError as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": str(e)
            }, player_id)

    elif msg_type == "get_game_state":
        # Отправить игроку актуальное состояние игры (зоны, игроки, эффекты)
        try:
            state = await player_service.get_player_in_game(game_id, player_id)
            await connection_manager.send_personal({
                "type": "game_state",
                "data": state
            }, player_id)
        except Exception as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": f"Failed to get state: {e}"
            }, player_id)

    else:
        await connection_manager.send_personal({
            "type": "error",
            "message": f"Unknown message type: {msg_type}"
        }, player_id)


# Функция для добавления WebSocket эндпоинта в приложение
def register_websocket_endpoint(app):
    @app.websocket("/ws/{game_id}/{player_id}")
    async def websocket_endpoint(websocket: WebSocket, game_id: uuid.UUID, player_id: uuid.UUID):
        # Получаем сессию БД
        db_gen = get_db()
        db = await anext(db_gen)  # получаем сессию из генератора

        try:
            # Проверяем, что игра существует и игрок в ней участвует
            game_service = GameService(db)
            game = await game_service.get_game(game_id)
            if game is None:
                await websocket.close(code=4004, reason="Game not found")
                return

            player = await game_service.get_player_in_game(game_id, player_id)
            if player is None:
                await websocket.close(code=4001, reason="Player not in game")
                return

            # Принимаем соединение и регистрируем в менеджере
            await connection_manager.connect(game_id, player_id, websocket)

            # Оповестить других игроков, что игрок вошел в сеть
            await connection_manager.broadcast_to_game(
                game_id,
                {
                    "type": "player_online",
                    "player_id": str(player_id),
                    "player_name": player.name,
                    "role": player.role.value if player.role else None
                },
                exclude_player=player_id
            )

            # Отправить подключившемуся игроку начальное состояние игры
            initial_state = await game_service.get_player_in_game(game_id, player_id)
            await connection_manager.send_personal({
                "type": "game_state",
                "data": initial_state
            }, player_id)

            # Основной цикл приема сообщений
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
                    break
                except Exception as e:
                    print(f"[WS] Error processing message: {e}")
                    await connection_manager.send_personal({
                        "type": "error",
                        "message": "Internal server error"
                    }, player_id)

        except WebSocketDisconnect:
            # Клиент отключился
            pass
        except Exception as e:
            print(f"[WS] Unexpected error: {e}")
            try:
                await websocket.close(code=1011, reason="Internal error")
            except:
                pass
        finally:
            # Убираем соединение из менеджера
            connection_manager.disconnect(player_id)
            # Оповещаем других игроков, что игрок офлайн
            try:
                await connection_manager.broadcast_to_game(
                    game_id,
                    {
                        "type": "player_offline",
                        "player_id": str(player_id)
                    },
                    exclude_player=player_id
                )
            except:
                pass
            # Не забываем закрыть сессию БД, если она была получена через get_db
            # (в текущей реализации get_db возвращает асинхронный генератор, его нужно аккуратно завершить)
            await db.close()