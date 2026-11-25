"""Проверки разрешения и расходования способностей без подключения к базе."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from database.models import Ability, AbilityType, Game, GameStatus, ZoneType, VictoryConditionType
from services.player import PlayerService
from services.zone import ZoneService


@pytest.mark.asyncio
async def test_ability_not_owned_is_rejected_without_crash():
    game_id, player_id = uuid4(), uuid4()
    player = SimpleNamespace(id=player_id, is_alive=True)
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=GameStatus.ACTIVE)),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
        rollback=AsyncMock(),
    )
    service = PlayerService(db)
    service.get_player_in_game = AsyncMock(return_value=player)
    with pytest.raises(ValueError, match='нет этой способности'):
        await service.use_ability(game_id, player_id, 'SHIELD')


@pytest.mark.asyncio
async def test_cooldown_rejects_without_spending_a_charge():
    game_id, player_id, ability_id = uuid4(), uuid4(), uuid4()
    player = SimpleNamespace(id=player_id, is_alive=True)
    owned = SimpleNamespace(ability_id=ability_id, number_uses_left=2)
    ability = SimpleNamespace(id=ability_id, duration_seconds=60, recharge_time=60)
    async def get(model, ident, **kwargs):
        return SimpleNamespace(status=GameStatus.ACTIVE) if model is Game else ability if model is Ability else None
    db = SimpleNamespace(get=AsyncMock(side_effect=get), execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: owned),
        SimpleNamespace(scalar_one_or_none=lambda: datetime.now(timezone.utc) - timedelta(seconds=1)),
    ]), rollback=AsyncMock())
    service = PlayerService(db)
    service.get_player_in_game = AsyncMock(return_value=player)
    with pytest.raises(ValueError, match='перезаряжается'):
        await service.use_ability(game_id, player_id, 'SHIELD')
    assert owned.number_uses_left == 2


@pytest.mark.asyncio
async def test_shield_consumes_one_hit_without_damage():
    player = SimpleNamespace(is_alive=True, health=100)
    db = SimpleNamespace(add=lambda obj: None, rollback=AsyncMock())
    service = PlayerService(db)
    service.get_player = AsyncMock(return_value=player)
    service.has_active_shield = AsyncMock(return_value=True)
    service.consume_shield = AsyncMock()
    await service.apply_damage(uuid4(), uuid4(), 50)
    assert player.health == 100
    service.consume_shield.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_contains_remaining_uses_and_cooldown():
    player_id, ability_id = uuid4(), uuid4()
    owned = SimpleNamespace(number_uses_left=1)
    ability = SimpleNamespace(id=ability_id, ability_type=AbilityType.SHIELD,
                              recharge_time=60, duration_seconds=30, data={})
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: [(owned, ability)]),
        SimpleNamespace(scalar_one_or_none=lambda: datetime.now(timezone.utc) - timedelta(seconds=5))
    ]))
    states = await PlayerService(db).get_ability_state(player_id)
    assert states[0]['number_uses_left'] == 1
    assert 53000 <= states[0]['cooldown_remaining_ms'] <= 55000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'ability_type, zone_type',
    [(AbilityType.TRAP, ZoneType.TRAP), (AbilityType.SNARE, ZoneType.SNARE)],
)
async def test_trap_and_snare_are_placed_at_selected_point(monkeypatch, ability_type, zone_type):
    """Ловушка и капкан ставятся в выбранную на карте точку, а не под игрока."""
    game_id, player_id, ability_id = uuid4(), uuid4(), uuid4()
    player = SimpleNamespace(id=player_id, is_alive=True, location_lat=1.0, location_lng=2.0)
    owned = SimpleNamespace(ability_id=ability_id, number_uses_left=2)
    ability = SimpleNamespace(
        id=ability_id,
        duration_seconds=60,
        recharge_time=0,
        data={'radius': 10, 'trap_duration_seconds': 60},
    )
    game = SimpleNamespace(
        status=GameStatus.ACTIVE,
        safe_zone_center_lat=55.5,
        safe_zone_center_lng=37.5,
        safe_zone_radius=1000,
    )

    async def get(model, ident, **kwargs):
        if model is Game:
            return game
        if model is Ability:
            return ability
        return None

    db = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        execute=AsyncMock(side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: owned),
            SimpleNamespace(scalar_one_or_none=lambda: None),
        ]),
        add=lambda obj: None,
        rollback=AsyncMock(),
        commit=AsyncMock(),
    )
    service = PlayerService(db)
    service.get_player_in_game = AsyncMock(return_value=player)

    calls = []

    async def fake_create_zone(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace()

    monkeypatch.setattr(ZoneService, 'create_zone', fake_create_zone)

    await service.use_ability(game_id, player_id, ability_type.value, 55.5, 37.5)

    assert calls, 'зона должна быть создана'
    # args: (self, game_id, zone_type, center_lat, center_lng, ...)
    assert calls[0][2] == zone_type
    assert (calls[0][3], calls[0][4]) == (55.5, 37.5)


@pytest.mark.asyncio
@pytest.mark.parametrize('creator_side', [VictoryConditionType.SEEKER, VictoryConditionType.HIDER])
async def test_trap_location_sent_only_to_creators_side(monkeypatch, creator_side):
    from services import zone as zone_module
    game_id, creator_id, teammate_id, opponent_id = uuid4(), uuid4(), uuid4(), uuid4()
    opponent_side = VictoryConditionType.HIDER if creator_side == VictoryConditionType.SEEKER else VictoryConditionType.SEEKER
    rows = [(SimpleNamespace(id=creator_id), creator_side), (SimpleNamespace(id=teammate_id), creator_side),
            (SimpleNamespace(id=opponent_id), opponent_side)]
    objects = []
    async def flush():
        objects[-1].id = uuid4()
    db = SimpleNamespace(add=objects.append, flush=flush,
                         execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)))
    sent = []
    async def send(message, player_id):
        sent.append((message, player_id))
    monkeypatch.setattr(zone_module.connection_manager, 'send_personal', send)
    await ZoneService(db).create_zone(game_id, ZoneType.TRAP, 55.0, 37.0, None, 10, creator_id=creator_id)
    assert {player_id for _, player_id in sent} == {creator_id, teammate_id}
    assert all(message['data']['zone_type'] == 'TRAP' for message, _ in sent)
