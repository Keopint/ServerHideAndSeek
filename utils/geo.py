import math
from typing import Tuple

def validate_coordinates(lat: float, lng: float) -> bool:
    """Проверяет, что координаты находятся в допустимых пределах."""
    return -90 <= lat <= 90 and -180 <= lng <= 180

def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
        Вычисляет расстояние между двумя точками в метрах (формула гаверсинусов для
        точного вычисления расстояния, учитывая изогнутость поверхности Земли).
    """
    R = 6371000  # радиус Земли в метрах
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lng = math.radians(lng2 - lng1)

    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) *
         math.sin(delta_lng / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def is_point_in_circle(
    point: Tuple[float, float],
    center: Tuple[float, float],
    radius: float
) -> bool:
    """Проверяет, находится ли точка внутри круга."""
    distance = calculate_distance(point[0], point[1], center[0], center[1])
    return distance <= radius