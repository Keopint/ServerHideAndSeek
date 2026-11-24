"""Проверки протокола тестового клиента без настоящей БД и сетевых запросов."""
import uuid

import pytest
from tools.test_player import TestPlayer as SimulatedPlayer, make_ws_url, normalize_base, parse_coordinates


def test_addresses_and_coordinates():
    game, player = str(uuid.uuid4()), str(uuid.uuid4())
    assert make_ws_url('https://example.test/prefix', game, player) == f'wss://example.test/prefix/ws/{game}/{player}'
    assert make_ws_url('http://localhost:8000', game, player) == f'ws://localhost:8000/ws/{game}/{player}'
    with pytest.raises(ValueError):
        normalize_base('https://user:pass@example.test/')
    with pytest.raises(ValueError):
        parse_coordinates('91', '10')


def test_role_ready_and_catch_commands():
    player = SimulatedPlayer('self')
    player.on_message({'type': 'game_state', 'data': {'game_data': {
        'roles': [{'id': 'role-id', 'name': 'Охотник', 'victory_condition': 'SEEKER'}],
        'players': [{'id': 'self', 'name': 'Me'}, {'id': 'other', 'name': 'Other'}]
    }}})
    assert player.make_command('role 1')['data']['role_id'] == 'role-id'
    assert player.make_command('ready')['data']['status'] is True
    assert player.make_command('catch 2')['data']['founded_player_id'] == 'other'
    with pytest.raises(ValueError):
        player.make_command('catch 1')


def test_confirmation_requires_request():
    player = SimulatedPlayer('self')
    with pytest.raises(ValueError):
        player.make_command('accept')
    player.on_message({'type': 'catch_requested', 'data': {'request_id': 'request-1', 'hunter_name': 'Охотник'}})
    assert player.make_command('accept') == {
        'type': 'respond_to_catch', 'data': {'request_id': 'request-1', 'accepted': True}
    }
    assert player.make_command('reject')['data']['accepted'] is False


def test_ability_command():
    player = SimulatedPlayer('self')
    assert player.make_command('ability shield') == {'type': 'use_ability', 'data': {'ability_type': 'SHIELD'}}
    assert player.make_command('ability trap 55.75 37.61')['data']['center_lat'] == 55.75
    with pytest.raises(ValueError):
        player.make_command('ability invalid')
