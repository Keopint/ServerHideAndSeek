import enum
import uuid
from datetime import datetime
from sqlalchemy.orm import attributes

from database.models import Game


def player_side(player) -> object | None:
    """Сторона игрока (victory_condition его роли) или None, если роль не загружена."""
    role = getattr(player, 'role_ref', None)
    return getattr(role, 'victory_condition', None) if role is not None else None


def game_to_dict(game: Game, viewer_id=None) -> dict:
    result = {
        "id": str(game.id),
        "game_code": game.game_code,
        "name": game.name,
        "status": game.status.value,
        "created_at": game.created_at.isoformat(),
        "safe_zone_center_lat": game.safe_zone_center_lat,
        "safe_zone_center_lng": game.safe_zone_center_lng,
        "safe_zone_radius": game.safe_zone_radius,
        "min_zone_radius": game.min_zone_radius,
        "zone_shrink_interval": game.zone_shrink_interval,
        "game_duration": game.game_duration,
        "time_to_hide": game.time_to_hide,
        "zone_boundary_damage": game.zone_boundary_damage,
        "current_safe_zone_id": str(game.current_safe_zone_id) if game.current_safe_zone_id else None,
        "last_shrink_at": game.last_shrink_at.isoformat() if game.last_shrink_at else None,
        "players": [to_dict(p) for p in game.players],
        "roles": [to_dict(r) for r in game.roles],
        "events": [to_dict(e) for e in game.events],
    }
    # Координаты видны только самому игроку и союзникам по роли (одинаковый victory_condition).
    viewer_side = next(
        (player_side(player) for player in game.players
         if str(getattr(player, 'id', '')) == str(viewer_id)),
        None,
    )
    for player_obj, player in zip(game.players, result['players']):
        is_viewer = str(player.get('id')) == str(viewer_id)
        is_ally = viewer_side is not None and player_side(player_obj) == viewer_side
        if not is_viewer and not is_ally:
            player['location_lat'] = None
            player['location_lng'] = None
            player['last_location_update'] = None
    return result

def to_dict(obj, visited=None, filter_none_in_lists=True, filter_none_in_dicts=False):
    if visited is None:
        visited = set()

    if obj is None:
        return None

    # примитивы
    if isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, uuid.UUID):
        return str(obj)

    if isinstance(obj, datetime):
        return obj.isoformat()

    if isinstance(obj, enum.Enum):
        return obj.value

    # коллекции
    if isinstance(obj, (list, tuple, set)):
        result = [to_dict(item, visited, filter_none_in_lists, filter_none_in_dicts) for item in obj]
        if filter_none_in_lists:
            result = [item for item in result if item is not None]
        if isinstance(obj, tuple):
            return tuple(result)
        if isinstance(obj, set):
            return set(result)
        return result

    # словари
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            val = to_dict(value, visited, filter_none_in_lists, filter_none_in_dicts)
            if filter_none_in_dicts and val is None:
                continue
            result[key] = val
        return result

    # SQLAlchemy ORM объект
    try:
        obj_id = id(obj)
        if obj_id in visited:
            return None
        visited.add(obj_id)

        # Получаем словарь всех загруженных атрибутов (включая отношения)
        state = attributes.instance_state(obj)
        # state.dict содержит колонки и загруженные отношения
        data = {}
        for key, value in state.dict.items():
            if key.startswith('_'):
                continue
            data[key] = to_dict(value, visited, filter_none_in_lists, filter_none_in_dicts)

        return data
    except Exception:
        # fallback
        return str(obj)
