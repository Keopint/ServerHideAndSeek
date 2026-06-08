import enum
import uuid
from datetime import datetime
from sqlalchemy.inspection import inspect

def to_dict(obj, visited=None, filter_none_in_lists=True, filter_none_in_dicts=False):
    """
    Универсальная сериализация SQLAlchemy ORM объекта в dict.

    Поддерживает:
    - UUID -> str
    - datetime -> isoformat
    - Enum -> value
    - relationship
    - list relationship
    - вложенные ORM объекты
    - защиту от циклических ссылок
    - фильтрацию None из списков и (опционально) из словарей.

    Args:
        obj: Объект для сериализации.
        visited: Множество id уже обработанных объектов (для рекурсии).
        filter_none_in_lists: Удалять None из списков/кортежей/множеств.
        filter_none_in_dicts: Удалять пары ключ-значение, где значение равно None.
    """
    if visited is None:
        visited = set()

    # None
    if obj is None:
        return None

    # primitive
    if isinstance(obj, (str, int, float, bool)):
        return obj

    # UUID
    if isinstance(obj, uuid.UUID):
        return str(obj)

    # datetime
    if isinstance(obj, datetime):
        return obj.isoformat()

    # Enum
    if isinstance(obj, enum.Enum):
        return obj.value

    # list / tuple / set
    if isinstance(obj, (list, tuple, set)):
        result = [to_dict(item, visited, filter_none_in_lists, filter_none_in_dicts) for item in obj]
        if filter_none_in_lists:
            result = [item for item in result if item is not None]
        # если исходный был tuple/set, можно вернуть такого же типа, но обычно нужен list
        if isinstance(obj, tuple):
            return tuple(result)
        if isinstance(obj, set):
            return set(result)
        return result

    # dict
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            val = to_dict(value, visited, filter_none_in_lists, filter_none_in_dicts)
            if filter_none_in_dicts and val is None:
                continue
            result[key] = val
        return result

    # SQLAlchemy object
    try:
        mapper = inspect(obj)

        # защита от рекурсии
        obj_id = id(obj)
        if obj_id in visited:
            return None

        visited.add(obj_id)

        data = {}

        # columns
        for column in mapper.mapper.column_attrs:
            key = column.key
            value = getattr(obj, key)
            data[key] = to_dict(value, visited, filter_none_in_lists, filter_none_in_dicts)

        # relationships
        for relationship in mapper.mapper.relationships:
            key = relationship.key
            # не грузим lazy relation
            if key not in obj.__dict__:
                continue
            value = getattr(obj, key)
            data[key] = to_dict(value, visited, filter_none_in_lists, filter_none_in_dicts)

        return data

    except Exception:
        pass

    # fallback
    return str(obj)