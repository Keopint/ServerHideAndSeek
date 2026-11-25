"""Интерактивный второй игрок для локального тестирования LURK.

Он создаёт настоящего игрока через API и управляется командами из терминала.
"""
import argparse
import asyncio
import json
import sys
import threading
import uuid
from urllib.parse import quote, urlsplit, urlunsplit

import requests
import websockets
from websockets.exceptions import WebSocketException


HELP = r"""Команды:
   .\.venv\Scripts\python.exe tools\test_player.py --code ATTUGC --name Kira3 --lat 55.665 --lng 37.55
  roles                показать доступные роли
  role НОМЕР           выбрать роль из списка (в лобби)
  ready / unready      подтвердить / отменить готовность
  players              показать игроков и их UUID
  state                запросить актуальное состояние игры
  catch НОМЕР          отправить запрос о поимке игроку из списка
  accept / reject      подтвердить / отклонить входящий запрос
  location ШИРОТА ДОЛГОТА  отправить тестовую геопозицию
  ability ТИП [ШИРОТА ДОЛГОТА]  применить способность (например, ability SHIELD)
  help                 показать команды
  quit                 завершить работу
"""


def normalize_base(value):
    """Один базовый URL задаёт и HTTP, и WebSocket; допускается префикс пути."""
    parts = urlsplit(value)
    if (parts.scheme not in ('http', 'https') or not parts.netloc or
            parts.username is not None or parts.password is not None or
            parts.query or parts.fragment):
        raise ValueError('Адрес должен быть HTTP(S) URL без учётных данных, query и fragment')
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/') + '/', '', ''))


def make_ws_url(base, game_id, player_id):
    parts = urlsplit(normalize_base(base))
    # UUID проверяются до построения URL, чтобы не передавать произвольные пути.
    game_id, player_id = str(uuid.UUID(game_id)), str(uuid.UUID(player_id))
    path = parts.path + f'ws/{game_id}/{player_id}'
    return urlunsplit(('wss' if parts.scheme == 'https' else 'ws', parts.netloc, path, '', ''))


def parse_coordinates(lat, lng):
    lat, lng = float(lat), float(lng)
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise ValueError('Допустимы широта от -90 до 90 и долгота от -180 до 180')
    return lat, lng


def join_game(base, code, name, lat, lng):
    url = normalize_base(base) + 'api/connect_player/' + quote(code, safe='')
    response = requests.post(url, json={
        'name': name, 'player_location_lat': lat, 'player_location_lng': lng,
    }, timeout=15)
    if not response.ok:
        # Не выводим detail 500: он может содержать SQL или служебные данные.
        raise RuntimeError(f'Сервер отклонил вход (HTTP {response.status_code}). Проверьте код комнаты и журнал сервера.')
    payload = response.json()
    return str(uuid.UUID(payload['game_id'])), str(uuid.UUID(payload['player_id']))


class TestPlayer:
    def __init__(self, player_id):
        self.player_id = player_id
        self.roles = []
        self.players = []
        self.pending_catch = None

    def show_roles(self):
        if not self.roles:
            print('Список ролей ещё не получен. Введите state.')
        for index, role in enumerate(self.roles, 1):
            print(f"  {index}. {role.get('name', '?')} ({role.get('victory_condition', '?')})")

    def show_players(self):
        if not self.players:
            print('Список игроков ещё не получен. Введите state.')
        for index, player in enumerate(self.players, 1):
            name = player.get('name', '?')
            role = (player.get('role_ref') or {}).get('name', '?')
            print(f"  {index}. {name} — {role} — {player.get('id', '?')}")

    def on_message(self, message):
        kind = message.get('type', '?')
        data = message.get('data') or {}
        if kind in ('websocket_connected_player', 'game_state'):
            game = data.get('game_data') or {}
            self.roles = game.get('roles') or []
            self.players = game.get('players') or []
            print(f"\nСостояние игры: {game.get('status', '?')}. Вы: {self.player_id}")
            print('Роли:')
            self.show_roles()
            print('Игроки:')
            self.show_players()
        elif kind == 'catch_requested':
            self.pending_catch = data.get('request_id')
            print(f"\nИгрок {data.get('hunter_name', '?')} сообщил о поимке. Введите accept или reject в течение 2 минут.")
        elif kind == 'catch_result':
            print('\n' + data.get('message', 'Ответ получен.'))
        elif kind == 'ability_used':
            print(f"\nСпособность {data.get('ability', '?')}: результат {data.get('result', '?')}")
        elif kind == 'error':
            print('\nОшибка сервера: ' + str(message.get('message') or data.get('message') or 'неизвестная ошибка'))
        elif kind == 'player_online':
            print(f"\nПодключился игрок: {data.get('player_name', '?')}. Введите state, чтобы обновить список.")
        elif kind in ('start_timer_to_hide', 'start_timer_for_game'):
            phase = 'Время спрятаться' if kind == 'start_timer_to_hide' else 'Игра началась'
            print(f"\n{phase}: {data.get('duration_seconds', '?')} сек.")
        elif kind == 'game_finished':
            print('\nИгра окончена: ' + ('победа' if data.get('is_victory') else 'поражение'))
        elif kind == 'you_died':
            print('\nВы выбыли. Причина: ' + str(data.get('reason', '?')))
        elif kind == 'role_changed':
            print('\nРоль изменена.')
        elif kind == 'ready_status_changed':
            print(f"\nГотовность: {data.get('ready_status')}")
        else:
            # Данные сообщений могут содержать геолокацию; не выводим их целиком.
            print(f'\nСобытие: {kind}')

    def make_command(self, text):
        parts = text.strip().split()
        if not parts:
            return None
        command = parts[0].lower()
        if command == 'help' and len(parts) == 1:
            print(HELP)
        elif command == 'roles' and len(parts) == 1:
            self.show_roles()
        elif command == 'players' and len(parts) == 1:
            self.show_players()
        elif command == 'state' and len(parts) == 1:
            return {'type': 'get_game_state', 'data': {}}
        elif command in ('ready', 'unready') and len(parts) == 1:
            return {'type': 'change_ready_status', 'data': {'status': command == 'ready'}}
        elif command == 'role' and len(parts) == 2:
            index = int(parts[1]) - 1
            if index < 0 or index >= len(self.roles):
                raise ValueError('Неверный номер роли; введите roles')
            return {'type': 'change_role', 'data': {'role_id': self.roles[index]['id']}}
        elif command == 'catch' and len(parts) == 2:
            index = int(parts[1]) - 1
            if index < 0 or index >= len(self.players):
                raise ValueError('Неверный номер игрока; введите players')
            target_id = self.players[index]['id']
            if target_id == self.player_id:
                raise ValueError('Нельзя выбрать себя')
            return {'type': 'hunter_found_player', 'data': {'founded_player_id': target_id}}
        elif command in ('accept', 'reject') and len(parts) == 1:
            if not self.pending_catch:
                raise ValueError('Нет ожидающего запроса о поимке')
            return {'type': 'respond_to_catch', 'data': {
                'request_id': self.pending_catch, 'accepted': command == 'accept'
            }}
        elif command == 'location' and len(parts) == 3:
            lat, lng = parse_coordinates(parts[1], parts[2])
            return {'type': 'update_location', 'data': {'lat': lat, 'lng': lng}}
        elif command == 'ability' and len(parts) in (2, 4):
            kind = parts[1].upper()
            if kind not in {'SHIELD', 'INTEL', 'SCAN', 'PERSONAL_BOMB', 'TRAP', 'SNARE', 'SAFE_HOUSE', 'SAFE_MANSION'}:
                raise ValueError('Неизвестный тип способности')
            data = {'ability_type': kind}
            if len(parts) == 4:
                lat, lng = parse_coordinates(parts[2], parts[3])
                data.update(center_lat=lat, center_lng=lng)
            return {'type': 'use_ability', 'data': data}
        else:
            raise ValueError('Неизвестная команда или неверные аргументы. Введите help.')
        return None


async def receive_messages(ws, player):
    async for raw in ws:
        try:
            player.on_message(json.loads(raw))
        except (json.JSONDecodeError, TypeError, AttributeError):
            print('\nНе удалось прочитать сообщение сервера.')


async def run_player(ws_url, player):
    # Ввод читается в daemon-потоке: закрытие сокета не зависает на input().
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def read_commands():
        while not loop.is_closed():
            try:
                line = input('> ')
            except (EOFError, OSError):
                line = 'quit'
            try:
                loop.call_soon_threadsafe(queue.put_nowait, line)
            except RuntimeError:
                return
            if line.strip().lower() == 'quit':
                return

    async with websockets.connect(ws_url, open_timeout=15) as ws:
        print('Подключено. Введите help для списка команд.')
        receiver = asyncio.create_task(receive_messages(ws, player))
        threading.Thread(target=read_commands, daemon=True).start()
        try:
            while True:
                input_task = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((receiver, input_task), return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    input_task.cancel()
                    await asyncio.gather(input_task, return_exceptions=True)
                    await receiver
                    print('Соединение закрыто сервером.')
                    break
                line = input_task.result()
                if line.strip().lower() == 'quit':
                    break
                try:
                    message = player.make_command(line)
                    if message:
                        await ws.send(json.dumps(message, ensure_ascii=False))
                        if message['type'] == 'respond_to_catch':
                            player.pending_catch = None
                except (KeyError, ValueError) as error:
                    print(f'Ошибка команды: {error}')
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description='Второй тестовый игрок LURK')
    parser.add_argument('--server', default='http://127.0.0.1:8000/', help='HTTP(S)-адрес сервера')
    parser.add_argument('--code', required=True, help='код созданной комнаты')
    parser.add_argument('--name', default='Тестовый игрок', help='имя игрока')
    parser.add_argument('--lat', required=True, help='тестовая широта внутри игровой зоны')
    parser.add_argument('--lng', required=True, help='тестовая долгота внутри игровой зоны')
    args = parser.parse_args()
    try:
        lat, lng = parse_coordinates(args.lat, args.lng)
        base = normalize_base(args.server)
        game_id, player_id = join_game(base, args.code.strip().upper(), args.name.strip(), lat, lng)
        print('Игрок присоединился к комнате. Подключаем WebSocket…')
        asyncio.run(run_player(make_ws_url(base, game_id, player_id), TestPlayer(player_id)))
    except (ValueError, KeyError, RuntimeError, requests.RequestException, OSError, WebSocketException) as error:
        print(f'Не удалось продолжить: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nЗавершено.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
