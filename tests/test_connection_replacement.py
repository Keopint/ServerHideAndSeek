from unittest.mock import AsyncMock
from uuid import uuid4
from types import SimpleNamespace

import pytest

from services.websocket_manager import ConnectionManager
from services.game_management import GameService, DuplicatePlayerName
from database.models import GameStatus


@pytest.mark.asyncio
async def test_old_socket_cannot_disconnect_replacement():
    manager = ConnectionManager()
    game_id, player_id = uuid4(), uuid4()
    old, new = AsyncMock(), AsyncMock()
    await manager.connect(game_id, player_id, old)
    await manager.connect(game_id, player_id, new)

    assert not manager.disconnect(game_id, player_id, old)
    assert manager.is_online(player_id)
    assert await manager.send_personal({'type': 'ping'}, player_id)
    new.send_json.assert_awaited_once()
    assert manager.disconnect(game_id, player_id, new)
    assert not manager.is_online(player_id)


@pytest.mark.asyncio
async def test_join_rejects_duplicate_name_before_creating_player():
    game = SimpleNamespace(id=uuid4(), status=GameStatus.WAITING, roles=[object()])
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[SimpleNamespace(scalar_one_or_none=lambda: game),
                                       SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [object()]))]),
        rollback=AsyncMock(), add=AsyncMock(),
    )
    with pytest.raises(DuplicatePlayerName):
        await GameService(db).add_player('ABCDEF', {
            'name': ' Name ', 'player_location_lat': 0, 'player_location_lng': 0,
        })
    db.add.assert_not_called()
    db.rollback.assert_awaited_once()
