from unittest.mock import AsyncMock, Mock
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
    old.close.assert_awaited_once()

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


@pytest.mark.asyncio
@pytest.mark.parametrize('online,within_grace,allowed', [(True, False, True), (False, True, True), (False, False, False)])
async def test_active_game_reuses_player_when_socket_still_online_or_in_grace(monkeypatch, online, within_grace, allowed):
    from services import game_management
    game = SimpleNamespace(id=uuid4(), status=GameStatus.ACTIVE, roles=[object()])
    player = SimpleNamespace(id=uuid4(), is_alive=True)
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: game),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [player]))]),
        rollback=AsyncMock(), add=Mock())
    monkeypatch.setattr(game_management.connection_manager, 'is_online', lambda _: online)
    monkeypatch.setattr(game_management.reconnect_grace, 'allows', lambda *_: within_grace)
    request = {'name': 'geo', 'player_location_lat': 0, 'player_location_lng': 0}
    if allowed:
        assert await GameService(db).add_player('ABCDEF', request) is player
        db.add.assert_not_called()
    else:
        with pytest.raises(ValueError, match='Переподключение недоступно'):
            await GameService(db).add_player('ABCDEF', request)


@pytest.mark.asyncio
async def test_failed_send_starts_grace_before_socket_finally(monkeypatch):
    from services.reconnect_grace import reconnect_grace
    start = Mock()
    monkeypatch.setattr(reconnect_grace, 'start', start)
    manager = ConnectionManager()
    game_id, player_id = uuid4(), uuid4()
    ws = AsyncMock()
    ws.send_json.side_effect = RuntimeError('socket closed')
    await manager.connect(game_id, player_id, ws)
    assert not await manager.send_personal({'type': 'ping'}, player_id)
    assert not manager.is_online(player_id)
    assert not manager.disconnect(game_id, player_id, ws)
    assert start.call_count == 1
