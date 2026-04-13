import uuid
from database.models import Player, PlayerEffect, EffectType
from sqlalchemy import select
from datetime import datetime, timezone, timedelta

from services.base import BaseService
from timers import timer_manager, TimerType


class EffectService(BaseService):
    """Сервис для создания, завершения и проверки эффектов игроков."""

    async def create_effect(
        self,
        player_id: uuid.UUID,
        effect_type: EffectType,
        duration_seconds: int,
        zone_id: uuid.UUID | None = None,
        data: dict | None = None
    ) -> PlayerEffect:
        """Создаёт эффект и планирует его окончание."""
        now = datetime.now(timezone.utc)
        effect = PlayerEffect(
            player_id=player_id,
            type=effect_type,
            starts_at=now,
            ends_at=now + timedelta(seconds=duration_seconds),
            zone_id=zone_id,
            data=data,
            is_active=True
        )
        self.db.add(effect)
        await self.db.flush()

        # Планируем завершение
        player = await self.db.get(Player, player_id)
        if player:
            await timer_manager.schedule(
                game_id=player.game_id,
                entity_type=TimerType.EFFECT,
                entity_id=effect.id,
                end_time=effect.ends_at,
                callback=lambda: self._on_effect_expired_callback(effect.id)
            )

        return effect

    async def _on_effect_expired_callback(self, effect_id: uuid.UUID):
        from database.db import get_db
        async for db in get_db():
            service = EffectService(db)
            await service.handle_effect_expired(effect_id)
            break

    async def handle_effect_expired(self, effect_id: uuid.UUID):
        """Деактивирует эффект по истечении времени."""
        effect = await self.db.get(PlayerEffect, effect_id)
        if effect and effect.is_active:
            effect.is_active = False
            self.db.add(effect)
            await self.db.commit()

    async def apply_trapped_effect(
        self,
        player_id: uuid.UUID,
        game_id: uuid.UUID,
        zone_id: uuid.UUID,
        duration_seconds: int,
        single_use: bool = False
    ):
        """Накладывает эффект обездвиживания (капкан/ловушка)."""
        # Проверяем, не активен ли уже такой эффект для этой зоны
        stmt = select(PlayerEffect).where(
            PlayerEffect.player_id == player_id,
            PlayerEffect.zone_id == zone_id,
            PlayerEffect.is_active == True
        )
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none():
            return  # Уже в ловушке

        await self.create_effect(
            player_id=player_id,
            effect_type=EffectType.TRAPPED,
            duration_seconds=duration_seconds,
            zone_id=zone_id
        )