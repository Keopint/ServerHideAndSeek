import uuid
from typing import Optional
from websocket_manager import connection_manager

class NotificationService:
    """Сервис для отправки сообщений игрокам через WebSocket."""

    async def send_to_player(self, player_id: uuid.UUID, message: dict) -> bool:
        return await connection_manager.send_personal(message, player_id)

    async def broadcast_to_game(
        self,
        game_id: uuid.UUID,
        message: dict,
        exclude_player: Optional[uuid.UUID] = None
    ) -> int:
        return await connection_manager.broadcast_to_game(game_id, message, exclude_player)

    async def notify_zone_created(self, game_id: uuid.UUID, zone: dict, creator_id: Optional[uuid.UUID] = None):
        await self.broadcast_to_game(game_id, {
            "type": "zone_created",
            "zone": zone
        }, exclude_player=creator_id)

    async def notify_zone_expired(self, game_id: uuid.UUID, zone_id: uuid.UUID, affected_players: list):
        await self.broadcast_to_game(game_id, {
            "type": "zone_expired",
            "zone_id": str(zone_id),
            "affected_players": [str(p) for p in affected_players]
        })

    async def notify_player_moved(self, game_id: uuid.UUID, player_id: uuid.UUID, lat: float, lng: float):
        await self.broadcast_to_game(game_id, {
            "type": "player_moved",
            "player_id": str(player_id),
            "location": {"lat": lat, "lng": lng}
        }, exclude_player=player_id)