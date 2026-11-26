import math
import random
from typing import Tuple
from database.models import Player


def choose_next_zone_center(lat: float, lng: float, current_radius: float, next_radius: float):
    """Равномерно выбирает центр следующего круга целиком внутри текущего."""
    if not 0 <= next_radius <= current_radius:
        raise ValueError('Радиус следующей зоны должен быть внутри текущей')
    distance = (current_radius - next_radius) * math.sqrt(random.random())
    bearing = random.uniform(0.0, 2.0 * math.pi)
    angular = distance / 6371000.0
    lat1, lng1 = math.radians(lat), math.radians(lng)
    lat2 = math.asin(math.sin(lat1) * math.cos(angular) + math.cos(lat1) * math.sin(angular) * math.cos(bearing))
    lng2 = lng1 + math.atan2(math.sin(bearing) * math.sin(angular) * math.cos(lat1),
                            math.cos(angular) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), (math.degrees(lng2) + 180.0) % 360.0 - 180.0

def validate_coordinates(lat: float, lng: float) -> bool:
    """Проверяет, что координаты находятся в допустимых пределах."""
    return -90 <= lat <= 90 and -180 <= lng <= 180

def calculate_distance_between_two_players(player1: Player, player2: Player):
    distance = calculate_distance(player1.location_lat, player1.location_lng, player2.location_lat, player2.location_lng)
    return distance

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
