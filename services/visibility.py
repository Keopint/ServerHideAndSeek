"""Личная разведка и сканирование: координаты получает только владелец эффекта."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from database.db import get_db
from database.models import EffectType, Game, GameStatus, Player, PlayerEffect, Role
from services.timers import TimerType, timer_manager
from services.websocket_manager import connection_manager
from utils.geo import calculate_distance


async def visibility_tick(game_id, player_id, effect_id):
    async for db in get_db():
        effect = await db.get(PlayerEffect, effect_id)
        game = await db.get(Game, game_id)
        now = datetime.now(timezone.utc)
        if not effect or not effect.is_active:
            return
        if not game or game.status != GameStatus.ACTIVE or not effect.ends_at or effect.ends_at <= now:
            effect.is_active = False
            await db.commit()
            await connection_manager.send_personal({'type': 'show_players_locations', 'data': {}}, player_id)
            return
        rows = (await db.execute(select(Player, Role.victory_condition)
                .join(Role, Player.role_id == Role.id)
                .where(Player.game_id == game_id, Player.is_alive.is_(True)))).all()
        owner = next(((player, role) for player, role in rows if player.id == player_id), None)
        if not owner:
            effect.is_active = False
            await db.commit()
            return
        candidates = [(player, role) for player, role in rows if player.id != player_id]
        if effect.type == EffectType.INTEL:
            candidates = [(player, role) for player, role in candidates if role != owner[1]]
            candidates.sort(key=lambda item: calculate_distance(owner[0].location_lat, owner[0].location_lng,
                                                                 item[0].location_lat, item[0].location_lng))
            candidates = candidates[:1]
        locations = {str(player.id): {'location_lat': player.location_lat, 'location_lng': player.location_lng}
                     for player, _ in candidates}
        await connection_manager.send_personal({'type': 'show_players_locations', 'data': locations}, player_id)
        await timer_manager.schedule(game_id, TimerType.EFFECT, effect_id,
            min(effect.ends_at, now + timedelta(seconds=1)),
            lambda: visibility_tick(game_id, player_id, effect_id))
        return
