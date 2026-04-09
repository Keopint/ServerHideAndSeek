# timers.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, Dict, Optional, Union
import uuid
from enum import Enum

logger = logging.getLogger(__name__)


class TimerType(Enum):
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
                    self._tasks.pop(task_key, None)

        task = asyncio.create_task(_waiter())
        self._tasks[task_key] = task
        logger.debug(f"Scheduled timer {task_key} to fire in {delay:.1f}s")

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