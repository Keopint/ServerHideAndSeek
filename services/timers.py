# timers.py
import asyncio
import logging
from datetime import datetime
from functools import partial
import random
import math
from typing import Callable, Awaitable, Dict, Union
import uuid
from enum import Enum
from database.db import get_db
from database.models import GameZone, Game, Event, EventType, role_events, Role, Player, Ability, PlayerEffect
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import timezone, timedelta

from services.websocket_manager import connection_manager
from utils.geo import calculate_distance, calculate_distance_between_two_players, choose_next_zone_center

logger = logging.getLogger(__name__)


class TimerType(Enum):
    ZONE_SHRINK = "zone_shrink"
    ZONE = "zone"
    EFFECT = "effect"
    EVENT = "event"


class TimerManager():
    """
    Управляет отложенными задачами для зон, эффектов и событий.
    Поддерживает восстановление после перезапуска сервера.
    """

    def __init__(self):
        # Ключ: f"{game_id}:{entity_type}:{entity_id}"
        self._tasks: Dict[str, asyncio.Task] = {}
        self._cleanup_lock = asyncio.Lock()
        self._phase_deadlines: Dict[uuid.UUID, datetime] = {}

    def set_phase_deadline(self, game_id: uuid.UUID, deadline: datetime) -> None:
        self._phase_deadlines[game_id] = deadline

    def phase_remaining_seconds(self, game_id: uuid.UUID) -> int | None:
        deadline = self._phase_deadlines.get(game_id)
        if deadline is None:
            return None
        return max(0, math.ceil((deadline - datetime.now(timezone.utc)).total_seconds()))

    def clear_phase_deadline(self, game_id: uuid.UUID) -> None:
        self._phase_deadlines.pop(game_id, None)

    async def schedule(
        self,
        game_id: uuid.UUID,
        entity_type: Union[TimerType, str],
        entity_id: uuid.UUID,
        end_time: datetime,
        callback: Callable[[], Awaitable[None]]
    ) -> None:
        """
        Планирует выполнение callback в указанное время end_time (UTC).
        Если end_time уже в прошлом, callback выполняется немедленно.
        """

        # Приводим enum к строке для ключа
        type_str = entity_type.value if isinstance(entity_type, TimerType) else entity_type
        task_key = f"{game_id}:{type_str}:{entity_id}"

        # Удаляем старую задачу, если есть (например, при обновлении)
        if self._tasks.get(task_key) is not asyncio.current_task():
            await self.cancel(task_key)

        now = datetime.now(timezone.utc)
        delay = (end_time - now).total_seconds()

        if delay <= 0:
            print(f"Timer {task_key} already expired, executing immediately")
            try:
                await callback()
            except Exception as e:
                print(f"Error in immediate callback for {task_key}: {e}")
            return

        async def _waiter():
            try:
                await asyncio.sleep(delay)
                print(f"Timer {task_key} triggered")
                await callback()  # ← обязательно await
            except asyncio.CancelledError:
                print(f"Timer {task_key} cancelled")
                raise
            except Exception as e:
                print(f"Error in timer callback for {task_key}: {e}")
            finally:
                async with self._cleanup_lock:
                    if self._tasks.get(task_key) is asyncio.current_task():
                        self._tasks.pop(task_key, None)

        task = asyncio.create_task(_waiter())
        self._tasks[task_key] = task
        print(f"Scheduled timer {task_key} to fire in {delay:.1f}s")

    async def intel_ability_shedule(
            self,
            game_id: uuid.UUID,
            player_id: uuid.UUID,
            effect: PlayerEffect,
            end_time: datetime,
            callback: Callable[[], Awaitable[None]]
    ):
        from database.db import get_db
        async for db in get_db():
            current_player = db.get(Player, player_id)
            stmt = select(Player).where(
                Player.game_id == game_id
            )

            players = (await db.execute(stmt)).scalars().all()
            nearest_player = players[0]
            min_distance = 100000000000.0
            for player in players:
                distance = calculate_distance_between_two_players(current_player, player)
                if player.id != player_id and (distance < min_distance):
                    nearest_player = player
                    min_distance = distance
            break

        now = datetime.now(timezone.utc)
        total_seconds = max(0, int((end_time - now).total_seconds()))
        if total_seconds <= 0:
            return

        # Функция одного шага (отправка координат)
        async def send_location_step(step: int):
            # Получаем свежие координаты всех игроков (один запрос)
            await db.refresh(nearest_player)

            # Отправляем сообщение клиенту, активировавшему способность
            await connection_manager.send_personal(
                message={
                    "type": "show_players_locations",
                    "data": {
                        f"{str(player_id)}": {
                            "location_lat": nearest_player.location_lat,
                            "location_lng": nearest_player.location_lng
                        }
                    }
                },
                player_id=player_id
            )

            # Планируем следующий шаг, если не последний
            if step < total_seconds:
                await timer_manager.schedule(
                    game_id=game_id,
                    entity_type=TimerType.EFFECT,
                    entity_id=effect.id,
                    end_time=datetime.now(timezone.utc) + timedelta(seconds=1),
                    callback=partial(send_location_step, step + 1)
                )

        # Запускаем первый шаг
        await send_location_step(1)
        try:
            await connection_manager.send_personal(
                message={
                    "type": "remove_location_markers",
                    "data": {}
                },
                player_id=player_id
            )
            await callback()
        except Exception as e:
            logger.exception(f"Error in immediate callback for intel_ability_shedule: {e}")
        return

    async def scan_ability_shedule(
            self,
            game_id: uuid.UUID,
            player_id: uuid.UUID,
            ability: Ability,
            end_time: datetime,
            callback: Callable[[], Awaitable[None]]
    ):
        from database.db import get_db
        async for db in get_db():
            current_player = await db.get(Player, player_id)

            now = datetime.now(timezone.utc)
            total_seconds = max(0, int((end_time - now).total_seconds()))
            if total_seconds <= 0:
                return

            # Функция одного шага (отправка координат)
            async def send_location_step(step: int):
                # Получаем свежие координаты всех игроков (один запрос)

                stmt_players = select(Player).where(Player.game_id == game_id)
                result_players = await db.execute(stmt_players)
                current_players = result_players.scalars().all()

                # Формируем словарь координат
                locations = {
                    str(p.id): {"location_lat": p.location_lat, "location_lng": p.location_lng}
                    for p in current_players
                }

                # Отправляем каждому получателю
                filtered_locations = {k: v for k, v in locations.items() if k != str(current_player.id)}
                await connection_manager.send_personal(
                    message={
                        "type": "show_players_locations",
                        "data": filtered_locations
                    },
                    player_id=current_player.id
                )

                # Планируем следующий шаг, если не последний
                if step < total_seconds:
                    await timer_manager.schedule(
                        game_id=game_id,
                        entity_type=TimerType.EVENT,
                        entity_id=ability.id,
                        end_time=datetime.now(timezone.utc) + timedelta(seconds=1),
                        callback=partial(send_location_step, step + 1)
                    )

            # Запускаем первый шаг
            await send_location_step(1)
            break
        try:
            await connection_manager.send_personal(
                message={
                    "type": "remove_location_markers",
                    "data": {}
                },
                player_id=player_id
            )
            await callback()
        except Exception as e:
            logger.exception(f"Error in immediate callback for scan_ability_shedule: {e}")
        return

    async def reveal_event_schedule(
            self,
            game_id: uuid.UUID,
            event: Event,  # событие типа REVEAL
            end_time: datetime
    ):
        """
        Периодически (каждую секунду) рассылает координаты всех игроков тем,
        у кого роль поддерживает событие REVEAL.
        """
        if end_time is None:
            return
        event_id = event.id

        async def send_location_step():
            from database.models import GameStatus
            async for db in get_db():
                game = await db.get(Game, game_id)
                roles = (await db.execute(select(role_events.c.role_id).where(
                    role_events.c.event_id == event_id))).scalars().all()
                players = (await db.execute(select(Player).where(Player.game_id == game_id))).scalars().all()
                active = bool(game and game.status == GameStatus.ACTIVE and datetime.now(timezone.utc) < end_time)
                locations = {str(p.id): {"location_lat": p.location_lat, "location_lng": p.location_lng}
                             for p in players if p.is_alive and p.location_lat is not None and p.location_lng is not None}
                for recipient in players:
                    if recipient.role_id in roles:
                        visible = {key: value for key, value in locations.items() if key != str(recipient.id)} if active else {}
                        await connection_manager.send_personal({'type': 'reveal_players_locations', 'data': visible}, recipient.id)
                if active:
                    await self.schedule(game_id, TimerType.EVENT, event_id,
                        min(end_time, datetime.now(timezone.utc) + timedelta(seconds=1)), send_location_step)
                return

        await send_location_step()

    async def timer(
            self,
            game_id: uuid.UUID,
            end_time: datetime,
            callback: Callable[[], Awaitable[None]],
            timer_name: str = "TIMER"
    ):
        type_str = timer_name
        task_key = f"{game_id}:{type_str}"

        if self._tasks.get(task_key) is not asyncio.current_task():
            await self.cancel(task_key)

        now = datetime.now(timezone.utc)
        delay = (end_time - now).total_seconds()

        if delay <= 0:
            logger.info(f"Timer {task_key} already expired, executing immediately")
            try:
                await callback()
            except Exception as e:
                logger.exception(f"Error in immediate callback for {task_key}: {e}")
            return

        async def _waiter():
            try:
                print("[START TIMER]")
                await asyncio.sleep(delay)
                print("[END TIMER]")
                logger.info(f"Timer {task_key} triggered")
                await callback()
            except asyncio.CancelledError:
                logger.info(f"Timer {task_key} cancelled")
                raise
            except Exception as e:
                logger.exception(f"Error in timer callback for {task_key}: {e}")
            finally:
                # Удаляем задачу из словаря после выполнения
                async with self._cleanup_lock:
                    if self._tasks.get(task_key) is asyncio.current_task():
                        self._tasks.pop(task_key, None)

        task = asyncio.create_task(_waiter())
        self._tasks[task_key] = task
        logger.debug(f"Scheduled timer {task_key} to fire in {delay:.1f}s")

    async def start_events(
            self,
            game_id: uuid.UUID,
            events,
            end_time: datetime,
            db: AsyncSession
    ):
        if not events:
            return
        type_str = "Event_generation"
        task_key = f"{game_id}:{type_str}"

        await self.cancel(task_key)

        total_duration = (end_time - datetime.now(timezone.utc)).total_seconds()
        steps = max(1, int((total_duration - 90) // 30))

        # Веса для частоты активации
        frequency_weights = {
            "FREQUENT": 0.7,
            "COMMON": 0.2,
            "RARE": 0.1
        }

        async def generate_event(step_number: int, remaining_steps: int):
            # Отложенное событие открывает новую сессию вместо закрытой сессии запроса.
            from database.models import GameStatus
            async for fresh_db in get_db():
                game = await fresh_db.get(Game, game_id)
                if not game or game.status != GameStatus.ACTIVE:
                    return
                chosen_event = None
                rand_val = random.random()
                cumulative = 0.0
                for event_id in event_ids:
                    event = await fresh_db.get(Event, event_id)
                    if event is None:
                        continue
                    weight = frequency_weights.get(event.activation_frequency.value, 0.1)
                    cumulative += weight
                    if rand_val <= cumulative:
                        chosen_event = event
                        break
                if chosen_event:
                    from services.event import EventService
                    active_event = await EventService(fresh_db).activate_event(game_id, chosen_event)
                    await fresh_db.commit()
                    await connection_manager.broadcast_to_game(game_id, {
                        'type': 'event_started',
                        'data': {
                            'event_type': active_event.event_type.value,
                            'duration_seconds': max(0, int((active_event.ends_at - datetime.now(timezone.utc)).total_seconds())) if active_event.ends_at else None,
                        },
                    })
                break

            # Если есть ещё шаги, планируем следующий через 30 секунд
            if remaining_steps > 1:
                await self.timer(
                    game_id=game_id,
                    end_time=datetime.now(timezone.utc) + timedelta(seconds=30),
                    callback=lambda: generate_event(step_number + 1, remaining_steps - 1),
                    timer_name="EVENT_GENERATION"
                )

        event_ids = [event.id for event in events]
        # события начнут генерироваться через 60 секунд
        await self.timer(
            game_id=game_id,
            end_time=datetime.now(timezone.utc) + timedelta(seconds=60),
            callback=lambda: generate_event(1, steps),
            timer_name="EVENT_GENERATION"
        )

    async def safe_zone_schedule(
        self,
        game_id: uuid.UUID,
        safe_zone: GameZone,
        end_time: datetime,
        db: AsyncSession
    ):
        """
           Планирует периодическое уменьшение безопасной зоны до указанного времени.

           :param game_id: ID игры
           :param safe_zone: текущий объект безопасной зоны (GameZone)
           :param end_time: момент времени, когда зона должна достичь минимального радиуса
           :param callback: функция, вызываемая после каждого успешного уменьшения (может обновлять клиентов)
           :param db: асинхронная сессия SQLAlchemy
       """
        from database.models import GameStatus, PlayerDeathCauses
        game = await db.get(Game, game_id)
        total_duration = (end_time - datetime.now(timezone.utc)).total_seconds()
        if not game or game.zone_shrink_interval <= 0 or total_duration <= 0 or safe_zone.radius <= game.min_zone_radius:
            return
        zone_id = safe_zone.id
        steps = max(1, int(total_duration // game.zone_shrink_interval))
        step_radius = (safe_zone.radius - game.min_zone_radius) / steps

        async def plan_next(remaining_steps):
            async for session in get_db():
                current_game = await session.get(Game, game_id)
                zone = await session.get(GameZone, zone_id)
                if not current_game or current_game.status != GameStatus.ACTIVE or not zone:
                    return
                radius = max(current_game.min_zone_radius, zone.radius - step_radius)
                lat, lng = choose_next_zone_center(zone.center_lat, zone.center_lng, zone.radius, radius)
                deadline = datetime.now(timezone.utc) + timedelta(seconds=current_game.zone_shrink_interval)
                if deadline >= end_time:
                    zone.zone_data = {key: value for key, value in (zone.zone_data or {}).items() if key != 'next_safe_zone'}
                    await session.commit()
                    await connection_manager.broadcast_to_game(game_id, {'type': 'next_safe_zone', 'data': {'available': False}})
                    return
                next_zone = {'center_lat': lat, 'center_lng': lng, 'radius': radius, 'ends_at': deadline.isoformat()}
                zone.zone_data = {**(zone.zone_data or {}), 'next_safe_zone': next_zone}
                await session.commit()
                await connection_manager.broadcast_to_game(game_id, {'type': 'next_safe_zone', 'data': {
                    **next_zone, 'remaining_seconds': max(0, math.ceil((deadline - datetime.now(timezone.utc)).total_seconds()))
                }})
                await self.schedule(game_id, TimerType.ZONE_SHRINK, zone_id, deadline,
                                    lambda: shrink_step(remaining_steps))
                return

        async def shrink_step(remaining_steps):
            from services.player import PlayerService
            async for session in get_db():
                current_game = await session.get(Game, game_id)
                zone = await session.get(GameZone, zone_id)
                if not current_game or current_game.status != GameStatus.ACTIVE or not zone:
                    return
                planned = (zone.zone_data or {}).get('next_safe_zone')
                if not planned:
                    return
                zone.center_lat, zone.center_lng, zone.radius = planned['center_lat'], planned['center_lng'], planned['radius']
                zone.zone_data = {key: value for key, value in (zone.zone_data or {}).items() if key != 'next_safe_zone'}
                current_game.safe_zone_center_lat, current_game.safe_zone_center_lng = zone.center_lat, zone.center_lng
                current_game.safe_zone_radius = zone.radius
                players = (await session.execute(select(Player).where(Player.game_id == game_id, Player.is_alive.is_(True)))).scalars().all()
                outside = [p for p in players if p.location_lat is not None and p.location_lng is not None and
                           calculate_distance(p.location_lat, p.location_lng, zone.center_lat, zone.center_lng) > zone.radius]
                for player in outside:
                    player.is_alive = False
                    player.health = 0
                await session.commit()
                await connection_manager.broadcast_to_game(game_id, {'type': 'update_safe_zone', 'data': {
                    'safe_zone_id': str(zone_id), 'new_radius': zone.radius,
                    'center_lat': zone.center_lat, 'center_lng': zone.center_lng
                }})
                for player in outside:
                    await connection_manager.send_personal({'type': 'you_died', 'data': {
                        'reason': PlayerDeathCauses.HP_ARE_OVER.value, 'hunter_player_id': None}}, player.id)
                    await connection_manager.broadcast_to_game(game_id, {'type': 'player_died', 'data': {
                        'reason': PlayerDeathCauses.HP_ARE_OVER.value, 'player_id': str(player.id)}}, exclude_player=player.id)
                    connection_manager.disconnect(game_id, player.id)
                if outside:
                    await PlayerService(session)._finish_game_if_side_eliminated(game_id)
                await session.refresh(current_game)
                if current_game.status == GameStatus.ACTIVE and remaining_steps > 1 and zone.radius > current_game.min_zone_radius:
                    await plan_next(remaining_steps - 1)
                else:
                    await connection_manager.broadcast_to_game(game_id, {'type': 'next_safe_zone', 'data': {'available': False}})
                return

        await plan_next(steps)

    async def cancel(self, task_key: str) -> bool:
        task = self._tasks.pop(task_key, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True
        return False

    async def cancel_for_entity(
        self, game_id: uuid.UUID, entity_type: Union[TimerType, str], entity_id: uuid.UUID
    ) -> bool:
        type_str = entity_type.value if isinstance(entity_type, TimerType) else entity_type
        task_key = f"{game_id}:{type_str}:{entity_id}"
        return await self.cancel(task_key)

    def get_pending_count(self) -> int:
        return len(self._tasks)


# Глобальный экземпляр (синглтон)
timer_manager = TimerManager()
