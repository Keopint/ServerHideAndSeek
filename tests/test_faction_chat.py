from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from database.models import GameStatus, VictoryConditionType
from services import faction_chat as chat_module


@pytest.mark.asyncio
async def test_chat_sends_and_restores_only_own_side(monkeypatch):
    game_id = uuid4()
    hunter = SimpleNamespace(id=uuid4(), name='Hunter', is_alive=True)
    teammate = SimpleNamespace(id=uuid4(), name='Teammate', is_alive=True)
    hider = SimpleNamespace(id=uuid4(), name='Hider', is_alive=True)
    rows = [(hunter, VictoryConditionType.SEEKER), (teammate, VictoryConditionType.SEEKER),
            (hider, VictoryConditionType.HIDER)]
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status=GameStatus.ACTIVE)),
                         execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)))
    manager = SimpleNamespace(send_personal=AsyncMock())
    monkeypatch.setattr(chat_module, 'faction_chat', chat_module.FactionChat())
    await chat_module.handle_faction_chat(db, manager, game_id, hunter.id, 'chat_send', {'text': '  привет  '})
    calls = manager.send_personal.await_args_list
    assert {call.args[1] for call in calls} == {hunter.id, teammate.id}
    assert calls[0].args[0]['data']['text'] == 'привет'
    manager.send_personal.reset_mock()
    await chat_module.handle_faction_chat(db, manager, game_id, hider.id, 'chat_history_request', {})
    assert manager.send_personal.await_args.args[0]['data']['messages'] == []
    await chat_module.handle_faction_chat(db, manager, game_id, teammate.id, 'chat_history_request', {})
    assert manager.send_personal.await_args.args[0]['data']['messages'][0]['text'] == 'привет'


@pytest.mark.asyncio
async def test_chat_rejects_empty_or_excessive_messages():
    chat = chat_module.FactionChat()
    sender = SimpleNamespace(id=uuid4(), name='Hunter')
    with pytest.raises(ValueError):
        await chat.append(uuid4(), VictoryConditionType.SEEKER, sender, '  ')
    with pytest.raises(ValueError):
        await chat.append(uuid4(), VictoryConditionType.SEEKER, sender, 'a' * 501)


@pytest.mark.asyncio
async def test_eliminated_player_cannot_read_or_send(monkeypatch):
    player = SimpleNamespace(id=uuid4(), name='Hider', is_alive=False)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status=GameStatus.ACTIVE)),
                         execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(player, VictoryConditionType.HIDER)])))
    manager = SimpleNamespace(send_personal=AsyncMock())
    for message_type in ('chat_history_request', 'chat_send'):
        with pytest.raises(ValueError):
            await chat_module.handle_faction_chat(db, manager, uuid4(), player.id, message_type, {'text': 'hello'})
    manager.send_personal.assert_not_awaited()
