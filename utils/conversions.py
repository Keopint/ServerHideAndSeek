import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy.inspection import inspect

def to_dict(obj, visited=None):
    """
    Универсальная сериализация SQLAlchemy ORM объекта в dict.

    Поддерживает:
    - UUID -> str
    - datetime -> isoformat
    - Enum -> value
    - relationship
    - list relationship
    - вложенные ORM объекты

    Защита от циклических ссылок.
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
        return [to_dict(item, visited) for item in obj]

    # dict
    if isinstance(obj, dict):
        return {
            key: to_dict(value, visited)
            for key, value in obj.items()
        }

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

            data[key] = to_dict(value, visited)

        # relationships
        for relationship in mapper.mapper.relationships:
            key = relationship.key

            # не грузим lazy relation
            if key not in obj.__dict__:
                continue

            value = getattr(obj, key)

            data[key] = to_dict(value, visited)

        return data

    except Exception:
        pass

    # fallback
    return str(obj)