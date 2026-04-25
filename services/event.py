import uuid
from database.models import Game, Player, ZoneType, GameZone, EventType, Event, role_events, GameEvent
from sqlalchemy import select, Null
from datetime import datetime, timezone, timedelta
from geopy.distance import distance
from services.base import BaseService
import random, math
from zone import ZoneService


class EventService(BaseService):
    """Сервис для создания, проверки и завершения событий"""

    async def activate_event(self, game_id: uuid.UUID, event_type: EventType):
        stmt = select(Game).where(Game.id == game_id)
        result = await self.db.execute(stmt)
        game = result.scalar_one_or_none()
        if not game:
            raise ValueError(f"Game {game_id} not found")

        # Получаем данные события из таблицы Event
        stmt = select(Event).join(role_events).where(
            role_events.c.game_id == game_id,
            Event.type == event_type
        )
        event = (await self.db.execute(stmt)).scalar_one_or_none()
        if not event:
            raise ValueError(f"Event {event_type} not found for game {game_id}")

        event_data = event.event_data
        now = datetime.now(timezone.utc)

        duration_seconds = event_data.get("duration_seconds")
        ends_at = now + timedelta(seconds=duration_seconds) if duration_seconds else None

        new_game_event = GameEvent(
            game_id=game_id,
            event_type=event_type,
            starts_at=now,
            ends_at=ends_at,
            event_data=event_data
        )
        self.db.add(new_game_event)
        await self.db.flush()

        if new_game_event.event_type == EventType.BOMB:

            lat, lng = await self.generate_point_in_circle(
                game.safe_zone_center_lat,
                game.safe_zone_center_lng,
                game.safe_zone_radius
            )

            zone_service = ZoneService(self.db)

            await zone_service.create_zone(
                game_id=game_id,
                zone_type=ZoneType.DANGER,
                center_lat=lat,
                center_lng=lng,
                duration_seconds=duration_seconds,
                radius=event_data.get("radius"),
                damage=event_data.get("damage")
            )
        elif new_game_event.event_type == EventType.AIRDROP:

            lat, lng = await self.generate_point_in_circle(
                game.safe_zone_center_lat,
                game.safe_zone_center_lng,
                game.safe_zone_radius
            )

            zone_service = ZoneService(self.db)

            await zone_service.create_zone(
                game_id=game_id,
                zone_type=ZoneType.AIRDROP,
                center_lat=lat,
                center_lng=lng,
                duration_seconds=duration_seconds,
                radius=event_data.get("radius")
            )
        elif new_game_event.event_type == EventType.BOMBARDMENT:

            lat, lng = await self.generate_point_in_circle(
                game.safe_zone_center_lat,
                game.safe_zone_center_lng,
                game.safe_zone_radius
            )

            zone_service = ZoneService(self.db)

            await zone_service.create_zone(
                game_id=game_id,
                zone_type=ZoneType.WARNING,
                center_lat=lat,
                center_lng=lng,
                duration_seconds=duration_seconds,
                radius=event_data.get("radius"),
                damage=event_data.get("damage")
            )


    async def generate_point_in_circle(self, lat_center, lon_center, radius_meters):
        """
        Генерирует случайную точку в круге с центром (lat_center, lon_center)
        и радиусом radius_meters.
        """

        # 1. Случайное направление (в радианах от 0 до 2π)
        bearing = random.uniform(0, 360)

        # 2. Случайное расстояние в метрах
        # Использование math.sqrt для равномерного распределения внутри круга
        d = radius_meters * math.sqrt(random.uniform(0, 1))

        # 3. Геодезическая задача: ищем точку на заданном расстоянии и направлении
        origin = (lat_center, lon_center)
        destination = distance(meters=d).destination(origin, bearing)

        return destination.latitude, destination.longitude
