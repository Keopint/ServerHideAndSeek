# timers.py
import asyncio
import logging
from datetime import datetime
from functools import partial
import random
from typing import Callable, Awaitable, Dict, Union
import uuid
from enum import Enum
from database.db import get_db
from database.models import GameZone, Game, Event, EventType, role_events, Role, Player, Ability, PlayerEffect
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import timezone, timedelta

from services.websocket_manager import connection_manager
from utils.geo import calculate_distance, calculate_distance_between_two_players

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
        # 1. Определяем ID ролей, у которых есть событие REVEAL
        stmt_roles_with_reveal = (
            select(Role.id)
            .join(role_events, Role.id == role_events.c.role_id)
            .join(Event, Event.id == role_events.c.event_id)
            .where(
                Event.type == EventType.REVEAL
            )
        )

        from database.db import get_db
        async for db in get_db():
            result = await db.execute(stmt_roles_with_reveal)
            reveal_role_ids = {row[0] for row in result.all()}  # set of UUID

            if not reveal_role_ids:
                # Если нет ролей с REVEAL, выходим (но такого не должно быть)
                return
            break

        # 2. Получаем всех игроков игры и сразу определяем получателей
        stmt = select(Player).where(
            Player.game_id == game_id
        )

        from database.db import get_db
        async for db in get_db():
            players = (await db.execute(stmt)).scalars().all()
            break
        recipients = [p for p in players if p.role_id in reveal_role_ids]

        if not recipients:
            return

        # 3. Длительность события в секундах (целое число)
        now = datetime.now(timezone.utc)
        total_seconds = max(0, int((end_time - now).total_seconds()))
        if total_seconds <= 0:
            return

        # 4. Функция одного шага (отправка координат)
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
            for recipient in recipients:
                filtered_locations = {k: v for k, v in locations.items() if k != str(recipient.id)}
                await connection_manager.send_personal(
                    message={
                        "type": "show_players_locations",
                        "data": filtered_locations
                    },
                    player_id=recipient.id
                )

            # Планируем следующий шаг, если не последний
            if step < total_seconds:
                await timer_manager.schedule(
                    game_id=game_id,
                    entity_type=TimerType.EVENT,
                    entity_id=event.id,
                    end_time=datetime.now(timezone.utc) + timedelta(seconds=1),
                    callback=partial(send_location_step, step + 1)
                )

        # Запускаем первый шаг
        await send_location_step(1)
        try:
            await connection_manager.broadcast_to_game(
                message={
                    "type": "remove_location_markers",
                    "data": {}
                },
                game_id=game_id
            )
        except Exception as e:
            logger.exception(f"Error in immediate callback for scan_ability_shedule: {e}")
        return

    async def timer(
            self,
            game_id: uuid.UUID,
            end_time: datetime,
            callback: Callable[[], Awaitable[None]]
    ):
        type_str = "TIMER"
        task_key = f"{game_id}:{type_str}"

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
            # Выбираем случайное событие на основе весов
            chosen_event = None
            rand_val = random.random()
            cumulative = 0.0
            for event in events:
                await db.refresh(event)
                weight = frequency_weights.get(event.activation_frequency, 0.1)
                cumulative += weight
                if rand_val <= cumulative:
                    chosen_event = event
                    break

            if chosen_event:
                # Активируем выбранное событие
                from services.event import EventService
                event_service = EventService(db)
                await event_service.activate_event(game_id, chosen_event)

            # Если есть ещё шаги, планируем следующий через 30 секунд
            if remaining_steps > 1:
                await self.timer(
                    game_id=game_id,
                    end_time=datetime.now(timezone.utc) + timedelta(seconds=30),
                    callback=lambda: generate_event(step_number + 1, remaining_steps - 1)
                )

        # события начнут генерироваться через 60 секунд
        await self.timer(
            game_id=game_id,
            end_time=datetime.now(timezone.utc) + timedelta(seconds=60),
            callback=lambda: generate_event(1, steps)
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
        # Получаем параметры игры
        game = await db.get(Game, game_id)

        initial_radius = safe_zone.radius
        min_radius = game.min_zone_radius
        total_duration = (end_time - datetime.now(timezone.utc)).total_seconds()

        if total_duration <= 0:
            # Игра уже должна была закончиться
            return

        # Количество шагов (уменьшений) – можно сделать фиксированным или динамическим
        steps = max(1, int(total_duration // game.zone_shrink_interval))
        step_radius = (initial_radius - min_radius) / steps

        async def shrink_step(
                step_number: int,
                current_radius: float,
                remaining_steps: int
        ):
            # Расчёт нового радиуса
            new_radius = max(min_radius, current_radius - step_radius)

            # Обновляем запись в БД и сразу фиксируем
            stmt = (
                update(GameZone)
                .where(GameZone.id == safe_zone.id)
                .values(radius=new_radius, zone_data={"last_shrink": datetime.now(timezone.utc).isoformat()})
            )
            await db.execute(stmt)
            await db.commit()  # <--- КОММИТТИМ СРАЗУ

            # ТОЛЬКО ПОСЛЕ ЭТОГО отправляем сообщения игрокам
            await connection_manager.broadcast_to_game(
                game_id=game_id,
                message={
                    "type": "update_safe_zone",
                    "data": {
                        "safe_zone_id": str(safe_zone.id),
                        "new_radius": new_radius
                    }
                }
            )

            # Если ещё не достигли минимума и есть следующие шаги, планируем следующий
            if new_radius > min_radius and remaining_steps > 1:
                # Планируем следующий шаг через zone_shrink_interval секунд
                await self.schedule(
                    game_id=game_id,
                    entity_type=TimerType.ZONE_SHRINK,
                    entity_id=safe_zone.id,
                    end_time=datetime.now(timezone.utc) + timedelta(seconds=game.zone_shrink_interval),
                    callback=lambda: shrink_step(
                        step_number + 1,
                        new_radius,
                        remaining_steps - 1
                    )
                )

        # Запускаем первый шаг
        await shrink_step(1, initial_radius, steps)

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