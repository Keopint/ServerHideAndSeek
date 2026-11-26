"""Восстановление таймеров, сроки которых сохранены в базе."""
import logging
from contextlib import aclosing
from datetime import datetime, timezone

from sqlalchemy import select

from database.db import get_db
from database.models import Game, GameStatus, GameZone, PlayerEffect, Player, EffectType
from services.timers import timer_manager, TimerType

logger = logging.getLogger(__name__)


async def recover_phase_timers():
    from services.game_management import GameService

    async with aclosing(get_db()) as sessions:
        async for db in sessions:
            games = (await db.execute(select(Game).where(
                Game.status.in_((GameStatus.HIDE_TIME, GameStatus.ACTIVE))))).scalars().all()
            phases = [(game.id, game.status, game.phase_ends_at) for game in games]
            break

    for game_id, status, deadline in phases:
        if deadline is None:
            logger.warning('Не удалось восстановить фазу игры %s: срок отсутствует', game_id)
            continue
        timer_manager.set_phase_deadline(game_id, deadline)

        async def fire(game_id=game_id, status=status):
            async with aclosing(get_db()) as sessions:
                async for session in sessions:
                    service = GameService(session)
                    if status == GameStatus.HIDE_TIME:
                        await service._on_hide_to_time_finished_callback(game_id)
                    else:
                        game = await session.get(Game, game_id)
                        if game and game.status == GameStatus.ACTIVE:
                            await service.finish_game(game_id)
                    break

        await timer_manager.timer(game_id, deadline, fire)


async def recover_zone_and_effect_timers():
    """Перепланирует зоны и эффекты активных игр по сохранённым ends_at."""
    from services.zone import _on_zone_expired_callback
    from services.player import PlayerService
    from services.visibility import visibility_tick

    async with aclosing(get_db()) as sessions:
        async for db in sessions:
            zone_rows = (await db.execute(select(GameZone.game_id, GameZone.id, GameZone.ends_at)
                .join(Game, Game.id == GameZone.game_id)
                .where(Game.status == GameStatus.ACTIVE, GameZone.is_active.is_(True), GameZone.ends_at.is_not(None)))).all()
            effect_rows = (await db.execute(select(Player.game_id, PlayerEffect.player_id, PlayerEffect.id,
                                                    PlayerEffect.type, PlayerEffect.ends_at)
                .join(Player, Player.id == PlayerEffect.player_id).join(Game, Game.id == Player.game_id)
                .where(Game.status == GameStatus.ACTIVE, PlayerEffect.is_active.is_(True), PlayerEffect.ends_at.is_not(None)))).all()
            break

    for game_id, zone_id, deadline in zone_rows:
        await timer_manager.schedule(game_id, TimerType.ZONE, zone_id, deadline,
            lambda game_id=game_id, zone_id=zone_id: _on_zone_expired_callback(game_id, zone_id))

    for game_id, player_id, effect_id, effect_type, deadline in effect_rows:
        if effect_type in (EffectType.INTEL, EffectType.SCAN):
            await timer_manager.schedule(game_id, TimerType.EFFECT, effect_id, datetime.now(timezone.utc),
                lambda game_id=game_id, player_id=player_id, effect_id=effect_id: visibility_tick(game_id, player_id, effect_id))
        else:
            async def expire(game_id=game_id, effect_id=effect_id):
                async with aclosing(get_db()) as sessions:
                    async for session in sessions:
                        await PlayerService(session).handle_effect_expired(game_id, effect_id)
                        break

            await timer_manager.schedule(game_id, TimerType.EFFECT, effect_id, deadline, expire)
