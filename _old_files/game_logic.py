import math
import random
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional
from geopy.distance import distance as geopy_distance
from shapely.geometry import Point

from database.models import (
    Game, Zone, ZoneType, EventType, AbilityType, PlayerRole
)


class GameLogic:
    """Вся игровая механика"""

    @staticmethod
    def calculate_distance(loc1: Point, loc2: Point) -> float:
        """Расстояние между двумя точками в метрах"""
        return geopy_distance(
            (loc1.lat, loc1.lng),
            (loc2.lat, loc2.lng)
        ).meters

    @staticmethod
    def is_point_in_zone(location: Point, zone: Zone) -> bool:
        """Проверка, находится ли точка внутри зоны"""
        distance = GameLogic.calculate_distance(location, zone.center)
        return distance <= zone.radius

    @staticmethod
    def get_random_location_near(center: Point, radius: float) -> Point:
        """Получить случайную точку в радиусе от центра"""
        # Простая реализация, для продакшена лучше использовать равномерное распределение
        angle = random.uniform(0, 2 * math.pi)
        r = radius * math.sqrt(random.random())
        dx = r * math.cos(angle) / 111320  # приблизительное преобразование в градусы
        dy = r * math.sin(angle) / 110540
        return Point(
            lat=center.lat + dy,
            lng=center.lng + dx
        )

    @staticmethod
    def shrink_safe_zone(game: Game) -> Zone:
        """Сузить безопасную зону"""
        new_radius = max(
            game.min_zone_radius,
            game.current_safe_zone.radius * 0.7
        )

        # Новая зона может сместиться случайно
        new_center = GameLogic.get_random_location_near(
            game.current_safe_zone.center,
            game.current_safe_zone.radius * 0.3
        )

        game.current_safe_zone = Zone(
            type=ZoneType.SAFE,
            center=new_center,
            radius=new_radius
        )

        return game.current_safe_zone

    @staticmethod
    async def check_zone_violations(game: Game, player_id: str, location: Point) -> bool:
        """
        Проверить, нарушил ли игрок границы зоны.
        Возвращает True, если игрок получил урон/выбыл
        """
        player = game.players.get(player_id)
        if not player or not player.is_alive:
            return False

        # Проверка на безопасную зону
        in_safe_zone = GameLogic.is_point_in_zone(location, game.current_safe_zone)

        if not in_safe_zone:
            # Игрок вне безопасной зоны - теряет здоровье
            if player.shield_active:
                player.shield_active = False
                return False

            player.health -= 20
            if player.health <= 0:
                player.is_alive = False
                return True  # Игрок выбыл

        # Проверка на активные опасные зоны (бомбы и т.д.)
        for zone in game.active_zones.values():
            if zone.type in [ZoneType.DANGER, ZoneType.WARNING]:
                if GameLogic.is_point_in_zone(location, zone):
                    if zone.type == ZoneType.DANGER:
                        player.is_alive = False
                        return True
                    elif zone.type == ZoneType.WARNING:
                        # Оранжевая зона пока не убивает
                        pass

        # Проверка на капканы/ловушки
        for zone in game.active_zones.values():
            if zone.type in [ZoneType.TRAP, ZoneType.SNARE] and zone.is_active:
                if GameLogic.is_point_in_zone(location, zone):
                    if player.role == PlayerRole.HIDER:
                        # Прячущийся попал в капкан
                        if zone.type == ZoneType.TRAP:
                            player.is_trapped = True
                            player.trapped_until = datetime.now() + timedelta(minutes=1)
                        elif zone.type == ZoneType.SNARE:
                            player.is_trapped = True
                            player.trapped_until = datetime.now() + timedelta(minutes=10)

        return False

    @staticmethod
    async def spawn_event(game: Game, event_type: EventType) -> Zone:
        """Создать случайное событие"""

        if event_type == EventType.BOMB:
            # Красная зона в случайном месте
            center = GameLogic.get_random_location_near(
                game.current_safe_zone.center,
                game.current_safe_zone.radius * 2
            )
            zone = Zone(
                type=ZoneType.DANGER,
                center=center,
                radius=random.uniform(30, 80),
                expires_at=datetime.now() + timedelta(minutes=2)
            )
            game.active_zones[zone.id] = zone

            # Через 2 минуты удаляем зону
            asyncio.create_task(GameLogic.auto_remove_zone(game, zone.id, 120))
            return zone

        elif event_type == EventType.AIRDROP:
            # Желтая зона (в коде используем WARNING)
            center = GameLogic.get_random_location_near(
                game.current_safe_zone.center,
                game.current_safe_zone.radius * 1.5
            )
            zone = Zone(
                type=ZoneType.WARNING,
                center=center,
                radius=20,
                expires_at=datetime.now() + timedelta(minutes=3)
            )
            game.active_zones[zone.id] = zone
            asyncio.create_task(GameLogic.auto_remove_zone(game, zone.id, 180))
            return zone

        elif event_type == EventType.BOMBARDMENT:
            # Множество маленьких красных зон
            for _ in range(random.randint(5, 15)):
                center = GameLogic.get_random_location_near(
                    game.current_safe_zone.center,
                    game.current_safe_zone.radius * 1.2
                )
                zone = Zone(
                    type=ZoneType.DANGER,
                    center=center,
                    radius=random.uniform(10, 30),
                    expires_at=datetime.now() + timedelta(minutes=1)
                )
                game.active_zones[zone.id] = zone
                asyncio.create_task(GameLogic.auto_remove_zone(game, zone.id, 60))
            return None

        elif event_type == EventType.REVEAL:
            # Подсветка всех игроков - реализуется на клиенте
            # Сервер просто уведомляет всех
            return None

        return None

    @staticmethod
    async def auto_remove_zone(game: Game, zone_id: str, delay: int):
        """Автоматическое удаление зоны через delay секунд"""
        await asyncio.sleep(delay)
        if zone_id in game.active_zones:
            del game.active_zones[zone_id]

    @staticmethod
    def get_nearby_players(game: Game, player_id: str, radius: float) -> List[str]:
        """Найти игроков в радиусе"""
        player = game.players.get(player_id)
        if not player:
            return []

        nearby = []
        for pid, p in game.players.items():
            if pid != player_id and p.is_alive:
                distance = GameLogic.calculate_distance(player.location, p.location)
                if distance <= radius:
                    nearby.append(pid)

        return nearby

    @staticmethod
    def use_ability(game: Game, player_id: str, ability_type: AbilityType,
                    target_location: Optional[Point] = None) -> dict:
        """Использовать способность"""
        player = game.players.get(player_id)
        if not player or not player.is_alive:
            return {"success": False, "message": "Игрок не найден или мертв"}

        # Находим способность
        ability = None
        for a in player.abilities:
            if a.type == ability_type and a.uses_left > 0:
                ability = a
                break

        if not ability:
            return {"success": False, "message": "Способность недоступна"}

        ability.uses_left -= 1

        # Обработка разных способностей
        if ability_type == AbilityType.SHIELD:
            player.shield_active = True
            return {"success": True, "message": "Щит активирован"}

        elif ability_type == AbilityType.INTEL:
            # Разведданные - ближайший противник
            if player.role == PlayerRole.HIDER:
                # Ищем ближайшего воду
                nearest = None
                min_dist = float('inf')
                for seeker_id in game.seekers:
                    seeker = game.players.get(seeker_id)
                    if seeker and seeker.is_alive:
                        dist = GameLogic.calculate_distance(player.location, seeker.location)
                        if dist < min_dist:
                            min_dist = dist
                            nearest = seeker_id
                if nearest:
                    return {"success": True, "nearest_seeker": nearest, "distance": min_dist}
            else:
                # Вода ищет ближайшего прячущегося
                nearest = None
                min_dist = float('inf')
                for hider_id in game.hiders:
                    hider = game.players.get(hider_id)
                    if hider and hider.is_alive:
                        dist = GameLogic.calculate_distance(player.location, hider.location)
                        if dist < min_dist:
                            min_dist = dist
                            nearest = hider_id
                if nearest:
                    return {"success": True, "nearest_hider": nearest, "distance": min_dist}

            return {"success": False, "message": "Нет ближайших игроков"}

        elif ability_type == AbilityType.SCAN:
            # Сканирование местности - видит всех
            all_players = []
            for pid, p in game.players.items():
                if p.is_alive:
                    all_players.append({
                        "id": pid,
                        "name": p.name,
                        "role": p.role,
                        "location": p.location
                    })
            return {"success": True, "players": all_players}

        elif ability_type == AbilityType.PERSONAL_BOMB:
            # Личная бомба
            if target_location:
                zone = Zone(
                    type=ZoneType.DANGER,
                    center=target_location,
                    radius=30,
                    expires_at=datetime.now() + timedelta(minutes=1)
                )
                game.active_zones[zone.id] = zone
                asyncio.create_task(GameLogic.auto_remove_zone(game, zone.id, 60))
                return {"success": True, "zone_id": zone.id}
            return {"success": False, "message": "Нужна цель для бомбы"}

        elif ability_type == AbilityType.SAFE_HOUSE:
            # "Я в домике" - создает зеленую зону, которая для вод выглядит как красная
            zone = Zone(
                type=ZoneType.DECOY,  # Для вод отображается как красная
                center=player.location,
                radius=30,
                owner_id=player_id,
                expires_at=datetime.now() + timedelta(minutes=2)
            )
            game.active_zones[zone.id] = zone
            asyncio.create_task(GameLogic.auto_remove_zone(game, zone.id, 120))
            return {"success": True, "zone_id": zone.id}

        elif ability_type == AbilityType.TRAP:
            # Капкан (для вод)
            if target_location:
                zone = Zone(
                    type=ZoneType.TRAP,
                    center=target_location,
                    radius=15,
                    owner_id=player_id,
                    expires_at=datetime.now() + timedelta(hours=1),
                    is_active=True
                )
                game.active_zones[zone.id] = zone
                return {"success": True, "zone_id": zone.id}

        elif ability_type == AbilityType.SNARE:
            # Ловушка (сильная)
            if target_location:
                zone = Zone(
                    type=ZoneType.SNARE,
                    center=target_location,
                    radius=15,
                    owner_id=player_id,
                    expires_at=datetime.now() + timedelta(hours=1),
                    is_active=True
                )
                game.active_zones[zone.id] = zone
                return {"success": True, "zone_id": zone.id}

        return {"success": False, "message": "Неизвестная способность"}