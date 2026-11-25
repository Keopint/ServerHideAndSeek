"""Тесты базовых правил без подключения к пользовательской базе."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from database.models import Game, GameStatus, Role, VictoryConditionType
from services.catch_requests import CatchRequests
from services.player import PlayerService
from routes import websocketRoutes
from services.timers import TimerManager
from services.game_management import GameService


@pytest.mark.asyncio
async def test_voluntary_exit_eliminates_current_player(monkeypatch):
    game_id, player_id = uuid4(), uuid4()
    player = SimpleNamespace(is_alive=True)
    game = SimpleNamespace(status=GameStatus.HIDE_TIME)
    service = SimpleNamespace(get_player_in_game=AsyncMock(return_value=player), player_died=AsyncMock())
    monkeypatch.setattr(websocketRoutes, 'PlayerService', lambda db: service)
    db = SimpleNamespace(get=AsyncMock(return_value=game))
    await websocketRoutes.handle_client_message(game_id, player_id, {'type': 'get_out_of_the_game', 'data': {}}, db)
    from database.models import PlayerDeathCauses
    service.player_died.assert_awaited_once_with(game_id, player_id, PlayerDeathCauses.HP_ARE_OVER, voluntary_quit=True)


def test_catch_request_only_target_can_respond_and_only_once():
    requests = CatchRequests()
    game, hunter, target = uuid4(), uuid4(), uuid4()
    request = requests.create(game, hunter, target)
    with pytest.raises(ValueError):
        requests.consume(request.id, game, uuid4())
    assert requests.consume(request.id, game, target) == request
    with pytest.raises(ValueError):
        requests.consume(request.id, game, target)


def test_catch_request_expiration(monkeypatch):
    requests = CatchRequests()
    game, hunter, target = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr('services.catch_requests.time.monotonic', lambda: 10)
    request = requests.create(game, hunter, target)
    monkeypatch.setattr('services.catch_requests.time.monotonic', lambda: 131)
    with pytest.raises(ValueError, match='истекло'):
        requests.consume(request.id, game, target)


@pytest.mark.asyncio
async def test_location_stored_and_boundary_damage_only_active(monkeypatch):
    player = SimpleNamespace(is_alive=True, health=100, location_lat=0.0, location_lng=0.0, last_location_update=None)
    game = SimpleNamespace(status=GameStatus.WAITING, safe_zone_center_lat=0.0,
                           safe_zone_center_lng=0.0, safe_zone_radius=100, zone_boundary_damage=2)
    db = SimpleNamespace(get=AsyncMock(return_value=game), add=lambda obj: None, flush=AsyncMock())
    service = PlayerService(db)
    service.get_player_in_game = AsyncMock(return_value=player)
    service.get_active_player_effects = AsyncMock(return_value=[])
    service.apply_damage = AsyncMock()
    service.player_died = AsyncMock()
    zone_check = AsyncMock()
    monkeypatch.setattr('services.player.ZoneService', lambda db: SimpleNamespace(check_player_in_zones=zone_check))
    game_id, player_id = uuid4(), uuid4()
    await service.update_player_location(game_id, player_id, 1.0, 1.0)
    assert (player.location_lat, player.location_lng) == (1.0, 1.0)
    assert db.get.await_args.kwargs.get('populate_existing') is True
    service.apply_damage.assert_not_awaited()
    game.status = GameStatus.ACTIVE
    await service.update_player_location(game_id, player_id, 1.1, 1.1)
    from database.models import PlayerDeathCauses
    service.player_died.assert_awaited_once_with(game_id, player_id, PlayerDeathCauses.HP_ARE_OVER)
    assert player.health == 0
    service.apply_damage.assert_not_awaited()
    zone_check.assert_not_awaited()


@pytest.mark.asyncio
async def test_catch_requires_confirmation(monkeypatch):
    game_id, hunter_id, target_id = uuid4(), uuid4(), uuid4()
    hunter = SimpleNamespace(id=hunter_id, name='Ищущий', is_alive=True,
                             role_ref=SimpleNamespace(victory_condition=VictoryConditionType.SEEKER))
    target = SimpleNamespace(id=target_id, name='Прячущийся', is_alive=True,
                             role_ref=SimpleNamespace(victory_condition=VictoryConditionType.HIDER))
    class FakeService:
        def __init__(self, db): pass
        async def get_player_in_game(self, game, player):
            return {hunter_id: hunter, target_id: target}.get(player)
        player_died = AsyncMock()
    sent = []
    async def send(message, player_id):
        sent.append((message, player_id))
        return True
    monkeypatch.setattr(websocketRoutes, 'PlayerService', FakeService)
    monkeypatch.setattr(websocketRoutes, 'catch_requests', CatchRequests())
    monkeypatch.setattr(websocketRoutes.connection_manager, 'send_personal', send)
    monkeypatch.setattr(websocketRoutes.connection_manager, 'is_online', lambda _: True)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status=GameStatus.ACTIVE)))
    await websocketRoutes.handle_client_message(game_id, hunter_id, {
        'type': 'hunter_found_player', 'data': {'founded_player_id': str(target_id)}}, db)
    FakeService.player_died.assert_not_awaited()
    request_id = sent[0][0]['data']['request_id']
    await websocketRoutes.handle_client_message(game_id, target_id, {
        'type': 'respond_to_catch', 'data': {'request_id': request_id, 'accepted': False}}, db)
    FakeService.player_died.assert_not_awaited()
    await websocketRoutes.handle_client_message(game_id, hunter_id, {
        'type': 'hunter_found_player', 'data': {'founded_player_id': str(target_id)}}, db)
    request_id = [m for m, _ in sent if m['type'] == 'catch_requested'][-1]['data']['request_id']
    await websocketRoutes.handle_client_message(game_id, target_id, {
        'type': 'respond_to_catch', 'data': {'request_id': request_id, 'accepted': True}}, db)
    FakeService.player_died.assert_awaited_once()


@pytest.mark.asyncio
async def test_hider_cannot_request_a_catch(monkeypatch):
    game_id, player_id, target_id = uuid4(), uuid4(), uuid4()
    hider = SimpleNamespace(is_alive=True, role_ref=SimpleNamespace(victory_condition=VictoryConditionType.HIDER))
    class FakeService:
        def __init__(self, db): pass
        async def get_player_in_game(self, game, player): return hider
        player_died = AsyncMock()
    sent = []
    async def send(message, recipient):
        sent.append((message, recipient))
        return True
    monkeypatch.setattr(websocketRoutes, 'PlayerService', FakeService)
    monkeypatch.setattr(websocketRoutes, 'catch_requests', CatchRequests())
    monkeypatch.setattr(websocketRoutes.connection_manager, 'send_personal', send)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status=GameStatus.ACTIVE)))
    await websocketRoutes.handle_client_message(game_id, player_id, {
        'type': 'hunter_found_player', 'data': {'founded_player_id': str(target_id)}}, db)
    assert sent[0][0]['type'] == 'error'
    assert 'ищущий' in sent[0][0]['message'] or 'ищущ' in sent[0][0]['message']
    FakeService.player_died.assert_not_awaited()


@pytest.mark.asyncio
async def test_distinct_game_and_event_timers_do_not_cancel_each_other():
    manager = TimerManager()
    done = []
    async def callback(name):
        done.append(name)
    now = datetime.now(timezone.utc)
    await manager.timer(uuid4_id := uuid4(), now + timedelta(milliseconds=30), lambda: callback('game'))
    await manager.timer(uuid4_id, now + timedelta(milliseconds=15), lambda: callback('event'), timer_name='EVENT_GENERATION')
    await asyncio.sleep(0.1)
    assert set(done) == {'game', 'event'}


def test_phase_remaining_is_based_on_deadline_and_clamped():
    manager = TimerManager()
    game_id = uuid4()
    assert manager.phase_remaining_seconds(game_id) is None
    manager.set_phase_deadline(game_id, datetime.now(timezone.utc) + timedelta(seconds=25))
    assert 24 <= manager.phase_remaining_seconds(game_id) <= 25
    manager.set_phase_deadline(game_id, datetime.now(timezone.utc) - timedelta(seconds=1))
    assert manager.phase_remaining_seconds(game_id) == 0
    manager.clear_phase_deadline(game_id)
    assert manager.phase_remaining_seconds(game_id) is None


def test_other_players_coordinates_are_not_in_state(monkeypatch):
    from utils import conversions
    own, other = uuid4(), uuid4()
    players = [SimpleNamespace(id=own), SimpleNamespace(id=other)]
    monkeypatch.setattr(conversions, 'to_dict', lambda p: {
        'id': str(p.id), 'location_lat': 55.0, 'location_lng': 37.0, 'last_location_update': 'now'
    })
    game = SimpleNamespace(id=uuid4(), game_code='ABCDEF', name='test', status=SimpleNamespace(value='ACTIVE'),
        created_at=datetime.now(timezone.utc), safe_zone_center_lat=0, safe_zone_center_lng=0,
        safe_zone_radius=100, min_zone_radius=10, zone_shrink_interval=60, game_duration=600,
        time_to_hide=60, zone_boundary_damage=1, current_safe_zone_id=None, last_shrink_at=None,
        players=players, roles=[], events=[])
    state = conversions.game_to_dict(game, own)
    assert state['players'][0]['location_lat'] == 55.0
    assert state['players'][1]['location_lat'] is None
    assert state['players'][1]['location_lng'] is None


@pytest.mark.asyncio
async def test_start_rejects_one_player(monkeypatch):
    game = SimpleNamespace(status=GameStatus.WAITING)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: game)), rollback=AsyncMock())
    monkeypatch.setattr('services.game_management.PlayerService', lambda db: SimpleNamespace(
        get_players_in_game=AsyncMock(return_value=[SimpleNamespace(is_player_ready=True)])
    ))
    with pytest.raises(ValueError, match='минимум два'):
        await GameService(db).start_game(uuid4())


@pytest.mark.asyncio
async def test_start_rejects_two_players_same_team(monkeypatch):
    game = SimpleNamespace(status=GameStatus.WAITING)
    role = SimpleNamespace(victory_condition=VictoryConditionType.SEEKER)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: game)),
                         get=AsyncMock(return_value=role), rollback=AsyncMock())
    players = [SimpleNamespace(role_id=uuid4(), is_player_ready=True) for _ in range(2)]
    monkeypatch.setattr('services.game_management.PlayerService', lambda db: SimpleNamespace(
        get_players_in_game=AsyncMock(return_value=players)
    ))
    with pytest.raises(ValueError, match='ищущий'):
        await GameService(db).start_game(uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'seeker_alive, hider_alive, should_finish',
    [
        (False, True, True),   # последний охотник погиб — игра должна завершиться
        (True, False, True),
        (True, True, False),
        (False, False, True),
    ],
)
async def test_game_finishes_when_one_side_has_no_alive_players(
        monkeypatch, seeker_alive, hider_alive, should_finish):
    game_id = uuid4()
    seeker_role_id, hider_role_id = uuid4(), uuid4()
    players = [
        SimpleNamespace(id=uuid4(), is_alive=seeker_alive, role_id=seeker_role_id),
        SimpleNamespace(id=uuid4(), is_alive=hider_alive, role_id=hider_role_id),
    ]
    roles = {
        seeker_role_id: VictoryConditionType.SEEKER,
        hider_role_id: VictoryConditionType.HIDER,
    }

    async def get(model, ident, **kwargs):
        if model is Game:
            return SimpleNamespace(status=GameStatus.ACTIVE)
        if model is Role:
            return SimpleNamespace(victory_condition=roles[ident])
        return None

    db = SimpleNamespace(get=AsyncMock(side_effect=get))
    service = PlayerService(db)
    service.get_players_in_game = AsyncMock(return_value=players)
    finish_game = AsyncMock()
    monkeypatch.setattr(GameService, 'finish_game', finish_game)

    await service._finish_game_if_side_eliminated(game_id)

    if should_finish:
        finish_game.assert_awaited_once_with(game_id=game_id)
    else:
        finish_game.assert_not_awaited()
