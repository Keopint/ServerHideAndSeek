# websocketRoutes.py
import json
import math
import uuid
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from database.models import GameStatus, PlayerDeathCauses, Game, VictoryConditionType, Player, PlayerEffect, EffectType, ZoneType, Role, GameEvent, EventType, GameZone
from sqlalchemy import select
from database.db import get_db
from services.game_management import GameService
from services.websocket_manager import connection_manager
from services.player import PlayerService
from services.zone import ZoneService
from utils.conversions import to_dict, game_to_dict
from services.catch_requests import catch_requests
from services.faction_chat import handle_faction_chat
from services.timers import timer_manager
from services.reconnect_grace import reconnect_grace, expire_disconnected_player

# Типы сообщений, которые клиент может отправлять
CLIENT_MESSAGE_TYPES = {
    'chat_send', 'chat_history_request',
    "ping",                 # проверка связи
    "update_location",      # обновление геопозиции
    "use_ability",          # использование способности
    "change_role",          # запрос на изменение роли
    "change_ready_status",  # запрос на изменение статуса готовности
    "get_out_of_the_game",  # запрос на выбывание из игры
    "get_game_state",       # запрос полного состояния игры
    "hunter_found_player", "respond_to_catch"
}

async def handle_client_message(
    game_id: uuid.UUID,
    player_id: uuid.UUID,
    message: dict,
    db: AsyncSession
):
    """Обработчик входящих сообщений от клиента."""

    if isinstance(message, str):
        print(f"Received string instead of dict: {message}")
        try:
            message = json.loads(message)
        except:
            return

    msg_type = message.get("type")
    data = message.get("data", {})
    player_service = PlayerService(db)

    if msg_type in ('chat_send', 'chat_history_request'):
        try:
            await handle_faction_chat(db, connection_manager, game_id, player_id, msg_type, data)
        except ValueError as e:
            await connection_manager.send_personal({'type': 'error', 'message': str(e)}, player_id)

    elif msg_type == "ping":
        # Ответить pong с серверным временем
        await connection_manager.send_personal({
            "type": "pong",
            "data": {
                "server_time": datetime.now(timezone.utc).isoformat()
            }
        }, player_id)


    elif msg_type == "change_role":
        new_role_id = data.get("role_id")
        print(f"[DEBUG] change_role: role_id={new_role_id}, game_id={game_id}, player_id={player_id}")
        if new_role_id is None:
            await connection_manager.send_personal({
                "type": "error",
                "message": "Missing role_id"
            }, player_id)
            return
        try:
            new_role_id = uuid.UUID(new_role_id)
            print("[DEBUG] About to call change_player_role")
            await player_service.change_player_role(game_id, player_id, new_role_id)
            await db.commit()
            print("[DEBUG] change_player_role succeeded")

            # Оповестить самого игрока
            await connection_manager.send_personal({
                "type": "role_changed",
                "data": {"role_id": str(new_role_id)}
            }, player_id)

            # Оповестить всех остальных
            await connection_manager.broadcast_to_game(
                game_id,
                {
                    "type": "player_role_changed",
                    "data": {
                        "player_id": str(player_id),
                        "role_id": str(new_role_id)
                    }
                }
            )
        except Exception as e:
            print(f"[ERROR] change_role exception: {e}")
            import traceback
            traceback.print_exc()
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
                    "ready_status": new_status
                }
            }, player_id)
            await connection_manager.broadcast_to_game(
                game_id,
                {
                    "type": "player_ready_status_changed",
                    "data": {
                        "player_id": str(player_id),
                        "ready_status": new_status
                    }
                },
                exclude_player=player_id
            )
            players = await player_service.get_players_in_game(game_id=game_id)
            all_is_ready = True
            for player in players:
                await db.refresh(player)
                if not player.is_player_ready:
                    all_is_ready = False
                    break
            if all_is_ready:
                game_service = GameService(db)
                print("[DEBUG GAME_ID]: ", str(game_id))
                await game_service.start_game(game_id=game_id)
        except ValueError as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": str(e)
            }, player_id)

    elif msg_type == "get_out_of_the_game":
        player = await player_service.get_player_in_game(game_id, player_id)
        game = await db.get(Game, game_id, populate_existing=True)
        if not player or not player.is_alive or not game or game.status not in (GameStatus.HIDE_TIME, GameStatus.ACTIVE):
            await connection_manager.send_personal({'type': 'error', 'message': 'Не удалось выйти: игрок или игра уже не активны'}, player_id)
            return
        await player_service.player_died(game_id, player_id, PlayerDeathCauses.HP_ARE_OVER, voluntary_quit=True)

    elif msg_type == "update_location":
        lat = data.get("lat")
        lng = data.get("lng")
        if lat is None or lng is None:
            await connection_manager.send_personal({
                "type": "error",
                "message": "Missing lat/lng"
            }, player_id)
            return
        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            await connection_manager.send_personal({
                "type": "error",
                "message": "Invalid lat/lng"
            }, player_id)
            return

        # Обновить позицию игрока через сервис
        try:
            player = await player_service.update_player_location(game_id, player_id, lat, lng)
            await db.commit()
            # Союзники по роли всегда видят друг друга. Противникам координаты не уходят.
            if player.is_alive:
                rows = (await db.execute(
                    select(Player.id, Role.victory_condition)
                    .join(Role, Player.role_id == Role.id)
                    .where(Player.game_id == game_id, Player.is_alive.is_(True))
                )).all()
                viewer_side = next((side for pid, side in rows if pid == player_id), None)
                moved_message = {
                    "type": "player_moved",
                    "data": {
                        "player_id": str(player_id),
                        "location_lat": lat,
                        "location_lng": lng,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                }
                for ally_id, side in rows:
                    if ally_id == player_id or side != viewer_side:
                        continue
                    await connection_manager.send_personal(moved_message, ally_id)
        except ValueError as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": str(e)
            }, player_id)

    elif msg_type == "use_ability":

        game = await db.get(Game, game_id, populate_existing=True)

        if game.status != GameStatus.ACTIVE:
            await connection_manager.send_personal({
                "type": "error",
                "message": "Game is not Active!"
            }, player_id)
            return

        ability_type = data.get("ability_type")
        if not ability_type:
            await connection_manager.send_personal({
                "type": "error",
                "message": "Missing ability_type"
            }, player_id)
            return
        center_lat = data.get("center_lat", None)
        center_lng = data.get("center_lng", None)
        try:
            result = await player_service.use_ability(game_id, player_id, ability_type, center_lat, center_lng)
            await db.commit()
            # О результате использования способности сервис сам разошлет уведомления через TimerManager
            # Можно также сразу подтвердить игроку
            await connection_manager.send_personal({
                "type": "ability_used",
                "data": {
                    "ability": ability_type,
                    "result": result,
                    "passive_effects": await player_service.get_passive_effect_state(player_id)
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
            player_state = await player_service.get_player_in_game(game_id, player_id)
            player_data = to_dict(player_state)
            player_data['ability_state'] = await player_service.get_ability_state(player_id)
            player_data['passive_effects'] = await player_service.get_passive_effect_state(player_id)
            game_data = game_to_dict(game_with_relation, player_id)
            game_data['phase_remaining_seconds'] = (max(0, math.ceil((game_with_relation.phase_ends_at - datetime.now(timezone.utc)).total_seconds()))
                                                     if game_with_relation.phase_ends_at else timer_manager.phase_remaining_seconds(game_id))
            await connection_manager.send_personal({
                "type": "game_state",
                "data": {
                    "player_data": player_data,
                    "game_data": game_data
                }
            }, player_id)
            now = datetime.now(timezone.utc)
            safe_zone = await db.get(GameZone, game_with_relation.current_safe_zone_id, populate_existing=True) if game_with_relation.current_safe_zone_id else None
            next_zone = (safe_zone.zone_data or {}).get('next_safe_zone') if safe_zone else None
            if next_zone:
                deadline = datetime.fromisoformat(next_zone['ends_at'])
                await connection_manager.send_personal({'type': 'next_safe_zone', 'data': {
                    **next_zone, 'remaining_seconds': max(0, math.ceil((deadline - now).total_seconds()))
                }}, player_id)
            reveal_end = (await db.execute(select(GameEvent.ends_at).where(
                GameEvent.game_id == game_id, GameEvent.event_type == EventType.REVEAL,
                GameEvent.ends_at > now).order_by(GameEvent.ends_at.desc()).limit(1))).scalar_one_or_none()
            if reveal_end:
                await connection_manager.send_personal({'type': 'event_started', 'data': {
                    'event_type': 'REVEAL', 'duration_seconds': max(0, math.ceil((reveal_end - now).total_seconds())),
                    'notify': False,
                }}, player_id)
            viewer_side = player_state.role_ref.victory_condition if player_state.role_ref else None
            for zone in await ZoneService(db).get_active_zones(game_id):
                if zone.type == ZoneType.SAFE:
                    continue
                if zone.type in (ZoneType.TRAP, ZoneType.SNARE):
                    creator = await db.get(Player, zone.created_by) if zone.created_by else None
                    creator_role = await db.get(Role, creator.role_id) if creator else None
                    if not creator_role or creator_role.victory_condition != viewer_side:
                        continue
                visible_type = (ZoneType.DANGER if zone.type in (ZoneType.SAFE_HOUSE, ZoneType.SAFE_MANSION)
                                and viewer_side == VictoryConditionType.SEEKER else zone.type)
                await connection_manager.send_personal({'type': 'create_zone', 'data': {
                    'zone_id': str(zone.id), 'zone_type': visible_type.value,
                    'center_lat': zone.center_lat, 'center_lng': zone.center_lng, 'radius': zone.radius,
                    'remaining_seconds': max(0, math.ceil((zone.ends_at - now).total_seconds())) if zone.ends_at else None,
                }}, player_id)
            traps = (await db.execute(select(PlayerEffect, Player)
                     .join(Player, PlayerEffect.player_id == Player.id)
                     .where(Player.game_id == game_id, PlayerEffect.type == EffectType.TRAPPED,
                            PlayerEffect.is_active.is_(True), PlayerEffect.ends_at > now))).all()
            for effect, victim in traps:
                if victim.id != player_id and (not player_state.role_ref or
                    player_state.role_ref.victory_condition != VictoryConditionType.SEEKER):
                    continue
                trap = effect.data or {}
                if not all(key in trap for key in ('center_lat', 'center_lng', 'radius', 'zone_type')):
                    continue
                await connection_manager.send_personal({'type': 'trap_activated', 'data': {
                    'effect_id': str(effect.id), 'zone_type': trap['zone_type'],
                    'center_lat': trap['center_lat'], 'center_lng': trap['center_lng'],
                    'radius': trap['radius'], 'remaining_seconds': max(0, math.ceil((effect.ends_at - now).total_seconds())),
                    'player_name': victim.name, 'is_victim': victim.id == player_id, 'notify': False,
                }}, player_id)
        except Exception as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": f"Failed to get state: {e}"
            }, player_id)

    elif msg_type == "hunter_found_player":
        try:
            target_id = uuid.UUID(data.get("founded_player_id"))
            async with catch_requests.lock:
                game = await db.get(Game, game_id, populate_existing=True)
                hunter = await player_service.get_player_in_game(game_id, player_id)
                target = await player_service.get_player_in_game(game_id, target_id)
                if not game or game.status != GameStatus.ACTIVE:
                    raise ValueError("Поимка доступна только во время игры")
                if not hunter or not hunter.is_alive or hunter.role_ref.victory_condition != VictoryConditionType.SEEKER:
                    raise ValueError("Отправить запрос может только активный ищущий")
                if not target or not target.is_alive or target.role_ref.victory_condition != VictoryConditionType.HIDER:
                    raise ValueError("Цель должна быть активным прячущимся в этой игре")
                if not connection_manager.is_online(target_id):
                    raise ValueError("Игрок не подключён к игре")
                request = catch_requests.create(game_id, player_id, target_id)
                sent = await connection_manager.send_personal({"type": "catch_requested", "data": {
                    "request_id": str(request.id), "hunter_name": hunter.name
                }}, target_id)
                if not sent:
                    catch_requests.discard(request.id)
                    raise ValueError("Не удалось доставить запрос игроку")
                await connection_manager.send_personal({"type": "catch_result", "data": {
                    "message": "Запрос отправлен. Ожидайте подтверждения игрока."
                }}, player_id)
        except Exception as e:
            await connection_manager.send_personal({
                "type": "error",
                "message": str(e)
            }, player_id)
    elif msg_type == "respond_to_catch":
        try:
            request_id = uuid.UUID(data.get("request_id"))
            accepted = data.get("accepted")
            if not isinstance(accepted, bool):
                raise ValueError("Требуется подтверждение или отказ")
            async with catch_requests.lock:
                request = catch_requests.consume(request_id, game_id, player_id)
                game = await db.get(Game, game_id, populate_existing=True)
                hunter = await player_service.get_player_in_game(game_id, request.hunter_id)
                target = await player_service.get_player_in_game(game_id, player_id)
                if not game or game.status != GameStatus.ACTIVE or not hunter or not target or not hunter.is_alive or not target.is_alive or hunter.role_ref.victory_condition != VictoryConditionType.SEEKER or target.role_ref.victory_condition != VictoryConditionType.HIDER:
                    raise ValueError("Подтверждение больше недоступно")
                if accepted:
                    await player_service.player_died(game_id, player_id, PlayerDeathCauses.HUNTER_FOUND_PLAYER, request.hunter_id)
                await connection_manager.send_personal({"type": "catch_result", "data": {
                    "message": "Игрок подтвердил поимку." if accepted else "Игрок отклонил запрос о поимке."
                }}, request.hunter_id)
        except Exception as e:
            await connection_manager.send_personal({"type": "error", "message": str(e)}, player_id)
    else:
        await connection_manager.send_personal({
            "type": "error",
            "message": f"Unknown message type: {msg_type}"
        }, player_id)


# регистрация websocket_endpoint
def register_websocket_endpoint(app):
    if any(getattr(route, "path", None) == "/ws/{game_id}/{player_id}" for route in app.routes):
        return
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
                game = await game_service.get_game_with_relations(game_id)
                if game is None:
                    await websocket.close(code=4004, reason="Game not found")
                    return

                player_service = PlayerService(db)

                player = await player_service.get_player_in_game(game_id, player_id)
                if player is None:
                    await websocket.close(code=4001, reason="Player not in game")
                    return

                if not player.is_alive:
                    await websocket.close(code=4003, reason="Player is no longer active")
                    return

                if (reconnect_grace.has_deadline(game_id, player_id) and
                        not reconnect_grace.allows(game_id, player_id)):
                    await websocket.close(code=4003, reason="Reconnect deadline expired")
                    return

                await connection_manager.connect(game_id, player_id, websocket)
                reconnect_grace.cancel(game_id, player_id)

                # Оповестить других
                await connection_manager.broadcast_to_game(
                    game_id,
                    {
                        "type": "player_online",
                        "data": {
                            "player_id": str(player_id),
                            "player_name": str(player.name),
                            "role_id": str(player.role_ref.id) if player.role_ref else None,
                            "role_ref": to_dict(player.role_ref)
                        }
                    },
                    exclude_player=player_id
                )

                # Отправить начальное состояние (player_service уже создан)
                initial_state = await player_service.get_player_in_game(game_id, player_id)
                game_data = await game_service.get_game_with_relations(game_id)

                print(f"[DEBUG] Sending websocket_connected_player to {player_id}")

                await connection_manager.send_personal(
                    message={
                        "type": "websocket_connected_player",
                        "data": {
                            "player_data": to_dict(initial_state),
                            "game_data": game_to_dict(game_data, player_id)
                        }
                    },
                    player_id=player_id
                )

                # Цикл сообщений
                while True:
                    try:
                        message = await websocket.receive_json()  # ← уже dict
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
                        import traceback
                        traceback.print_exc()
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
                disconnected = connection_manager.disconnect(game_id, player_id, websocket)
                try:
                    player_service = PlayerService(db)
                    player = await player_service.get_player_in_game(game_id, player_id)
                    if player and disconnected:
                        player.is_online = False
                        game = await db.get(Game, game_id, populate_existing=True)
                        if player.is_alive and game and game.status in (GameStatus.HIDE_TIME, GameStatus.ACTIVE):
                            reconnect_grace.start(game_id, player_id, datetime.now(timezone.utc), expire_disconnected_player)
                        await connection_manager.broadcast_to_game(
                        game_id,
                        {
                            "type": "player_offline",
                            "data": {
                                "player_id": str(player_id),
                                "player_name": str(player.name)
                            }
                        },
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
