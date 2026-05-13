# timers.py
import asyncio
import logging
from datetime import datetime
from functools import partial
from typing import Callable, Awaitable, Dict, Union
import uuid
from enum import Enum
from database.models import GameZone, Game, Event, EventType, role_events, Role, Player
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import timezone, timedelta

from services.websocket_manager import connection_manager

logger = logging.getLogger(__name__)


class TimerType(Enum):
    ZONE_SHRINK = "zone_shrink"
    ZONE = "zone"
    EFFECT = "effect"
    EVENT = "event"


class TimerManager:
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
        callback: Callable[[], Awaitable[None]],
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
            logger.info(f"Timer {task_key} already expired, executing immediately")
            try:
                await callback()
            except Exception as e:
                logger.exception(f"Error in immediate callback for {task_key}: {e}")
            return

        async def _waiter():
            try:
                await asyncio.sleep(delay)
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
                    await self._tasks.pop(task_key, None)

        task = asyncio.create_task(_waiter())
        self._tasks[task_key] = task
        logger.debug(f"Scheduled timer {task_key} to fire in {delay:.1f}s")

    async def reveal_event_schedule(
            self,
            game_id: uuid.UUID,
            event: Event,  # событие типа REVEAL
            end_time: datetime,
            db: AsyncSession
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
        result = await db.execute(stmt_roles_with_reveal)
        reveal_role_ids = {row[0] for row in result.all()}  # set of UUID

        if not reveal_role_ids:
            # Если нет ролей с REVEAL, выходим (но такого не должно быть)
            return

        # 2. Получаем всех игроков игры и сразу определяем получателей
        stmt = select(Player).where(
            Player.game_id == game_id
        )
        players = (await self.db.execute(stmt)).scalars().all()
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
                str(p.id): {"lat": p.location_lat, "lng": p.location_lng}
                for p in current_players
            }

            # Отправляем каждому получателю
            for recipient in recipients:
                await connection_manager.send_personal(
                    message={
                        "type": "reveal_event_players_locations",
                        "data": locations
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


    async def timer_to_hide(
            self,
            game_id: uuid.UUID,
            end_time: datetime,
            callback: Callable[[], Awaitable[None]]
    ):
        type_str = "TIMER_TO_HIDE"
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
                await asyncio.sleep(delay)
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
                    await self._tasks.pop(task_key, None)

        task = asyncio.create_task(_waiter())
        self._tasks[task_key] = task
        logger.debug(f"Scheduled timer {task_key} to fire in {delay:.1f}s")

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
            # Обновляем запись в БД
            stmt = (
                update(GameZone)
                .where(GameZone.id == safe_zone.id)
                .values(radius=new_radius, zone_data={"last_shrink": datetime.now(timezone.utc).isoformat()})
            )
            await db.execute(stmt)
            await db.commit()

            connection_manager.broadcast_to_game(
                game_id=game_id,
                message={
                    "type": "update_safe_zone",
                    "data": {
                        "safe_zone_id": safe_zone.id,
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
        """Отменяет запланированную задачу по ключу."""
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