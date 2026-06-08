# API игры «Hunt & Hide»

## 📖 Общее описание

**Hunt & Hide** — многопользовательская геолокационная игра, в которой участники делятся на **ищущих** (SEEKER) и **прячущихся** (HIDER). Игроки перемещаются в реальном мире, используют способности, попадают в опасные зоны и взаимодействуют с игровыми событиями.

Сервер предоставляет **HTTP** эндпоинты для создания и присоединения к игре, а также **WebSocket** для всей последующей коммуникации.

### Основные сущности

| Сущность | Описание |
|----------|----------|
| **Игра** | Игровая сессия с настройками, зоной безопасности, списком игроков, ролями и событиями. |
| **Игрок** | Участник игры: здоровье, местоположение, роль, статус готовности и т.д. |
| **Роль** | Определяет здоровье, условие победы (SEEKER/HIDER) и список доступных способностей. |
| **Способность** | Активное действие, которое игрок может использовать на карте (бомба, ловушка, щит и др.). |
| **Зона** | Область на карте: безопасная, опасная, временная (ловушка, аирдроп). |
| **Событие** | Глобальное событие, активируемое сервером (бомбардировка, бомба, аирдроп). |

### Статусы игры

| Статус      | Описание |
|-------------|----------|
| `WAITING`   | Ожидание игроков. Можно менять роль и статус готовности. |
| `ACTIVE`    | Игра идёт. |
| `FINISHED`  | Игра завершена (победа одной из команд). |

### Условия победы

| Тип      | Победители |
|----------|------------|
| `SEEKER` | Ищущие (Hunter, Amogus и другие роли с этим условием). |
| `HIDER`  | Прячущиеся. |

---

## 🌐 HTTP API

### 1. Создание игры

**Endpoint:** `POST /api/games`  
**Тело запроса:**

```json
{
    "name": "Тестовая игра",
    "center_lat": 55.751244,
    "center_lng": 37.618423,
    "safe_zone_radius": 500.0,
    "min_zone_radius": 50.0,
    "zone_shrink_interval": 120,
    "game_duration": 1800,
    "time_to_hide": 300,
    "host_player": {
        "host_name": "Keopint",
        "host_player_location_lat": 20.0,
        "host_player_location_lng": 20.0
    },
    "game_roles": {
        "Amogus": {
            "health": 200,
            "victory_condition": "SEEKER"
        },
        "Hunter": {
            "health": 100,
            "victory_condition": "SEEKER"
        },
        "Hider": {
            "health": 100,
            "victory_condition": "HIDER"
        }
    },
    "roles_abilities": { /* см. раздел "Способности" */ },
    "roles_events": { /* см. раздел "События" */ },
    "events_configurations": { /* см. раздел "События" */ }
}
```

**Ответ (201 Created):**

```json
{
  "game": {
    "id": "9d4be194-3316-4941-afd9-835825f8a332",
    "game_code": "AGFMAF",
    "name": "Тестовая игра",
    "status": "WAITING",
    "safe_zone_center_lat": 55.751244,
    "safe_zone_center_lng": 37.618423,
    "safe_zone_radius": 500,
    "min_zone_radius": 50,
    "zone_shrink_interval": 120,
    "game_duration": 1800,
    "time_to_hide": 300,
    "zone_boundary_damage": 1,
    "players": [ /* список игроков */ ],
    "roles": [ /* список ролей с их способностями */ ],
    "events": [ /* список событий */ ]
  },
  "host_player_id": "de3c3966-962e-41a5-b694-89331524c6c6"
}
```

### 2. Присоединение к игре

**Endpoint:** `POST /api/games/join`  
**Тело запроса:**

```json
{
    "name": "Igor",
    "player_location_lat": 20.0,
    "player_location_lng": 33.0
}
```

**Ответ (200 OK):**

```json
{
    "game_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1",
    "player_id": "awfrawde8-4bba-ab83-fsfe-eerwd5555b1",
    "player_name": "Igor",
    "game_status": "WAITING",
    "ws_url": "ws://your-server.com/ws/{game_id}/{player_id}"
}
```

> После получения `ws_url` необходимо открыть WebSocket-соединение.

---

## 🔌 WebSocket протокол

**Базовый URL:** `ws://your-server.com/ws/{game_id}/{player_id}`  

Все сообщения имеют единый формат:

```json
{
    "type": "тип_сообщения",
    "data": { ... }
}
```

### 1. Установка соединения и начальное состояние

#### `websocket_connected_player` (сервер → клиент)

Отправляется сразу после успешного открытия WebSocket. Содержит полные данные об игре и текущем игроке.

```json
{
    "type": "websocket_connected_player",
    "data": {
        "player_data": { /* см. объект игрока */ },
        "game_data": { /* см. объект игры */ }
    }
}
```

### 2. Проверка связи

**Клиент → сервер:**
```json
{ "type": "ping", "data": {} }
```

**Сервер → клиент:**
```json
{
    "type": "pong",
    "data": { "server_time": "2026-04-28T08:01:53.075554" }
}
```

### 3. Действия в лобби (статус игры `WAITING`)

#### `change_role` – смена роли

**Клиент → сервер:**
```json
{
    "type": "change_role",
    "data": { "role_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1" }
}
```

**Сервер → отправителю:**
```json
{
    "type": "role_changed",
    "data": { "role_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1" }
}
```

**Сервер → всем остальным игрокам:**
```json
{
    "type": "player_role_changed",
    "data": {
        "player_id": "de3c3966-962e-41a5-b694-89331524c6c6",
        "role_id": "548c6267-eb46-4d02-8f17-e043ed92ad42"
    }
}
```

#### `change_ready_status` – изменение готовности

**Клиент → сервер:**
```json
{
    "type": "change_ready_status",
    "data": { "ready_status": true }
}
```

**Сервер → отправителю:**
```json
{
    "type": "ready_status_changed",
    "data": { "ready_status": true }
}
```

**Сервер → всем игрокам:**
```json
{
    "type": "player_ready_status_changed",
    "data": {
        "player_id": "de3c3966-962e-41a5-b694-89331524c6c6",
        "ready_status": true
    }
}
```

### 4. Игровая активность (статус игры `ACTIVE`)

#### `update_location` – обновление геолокации

**Клиент → сервер:**
```json
{
    "type": "update_location",
    "data": { "lat": 20.2443, "lng": 33.2453 }
}
```

**Сервер → всем (кроме отправителя):**
```json
{
    "type": "player_moved",
    "data": {
        "player_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1",
        "location_lat": 20.2443,
        "location_lng": 33.2453,
        "timestamp": "2026-04-28T08:01:53.075554"
    }
}
```

#### `use_ability` – использование способности

**Клиент → сервер:**
```json
{
    "type": "use_ability",
    "data": {
        "ability_type": "PERSONAL_BOMB",
        "center_lat": 20.3,
        "center_lng": 24.5
    }
}
```

**Сервер → отправителю:**
```json
{
    "type": "ability_used",
    "data": {
        "ability": "PERSONAL_BOMB",
        "result": 0   // 0 = успешно, другие коды = ошибка
    }
}
```

#### `hunter_found_player` – охотник нашёл игрока (только для SEEKER)

**Клиент → сервер:**
```json
{
    "type": "hunter_found_player",
    "data": { "found_player_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1" }
}
```

**Сервер → всем:**
```json
{
    "type": "player_died",
    "data": {
        "reason": "HUNTER_FOUND_PLAYER",
        "player_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1",
        "hunter_player_id": "66665833-e5e8-4bba-ab83-e40d5d7182b1"
    }
}
```

#### `get_game_state` – запрос полного состояния

**Клиент → сервер:**
```json
{ "type": "get_game_state", "data": {} }
```

**Сервер → клиенту:**
```json
{
    "type": "game_state",
    "data": {
        "game_info": { /* полный объект игры */ },
        "player_info": { /* данные игрока + его роль с abilities */ }
    }
}
```

### 5. Системные уведомления (сервер → клиент)

#### `start_timer_for_game` – начало активной фазы

```json
{
    "type": "start_timer_for_game",
    "data": { "duration_seconds": 1800 }
}
```

#### `game_finished` – завершение игры

```json
{
    "type": "game_finished",
    "data": { "is_victory": true }
}
```

#### `you_died` – смерть игрока (адресату)

❗ **Клиент обязан закрыть WebSocket после получения этого сообщения.**

```json
{
    "type": "you_died",
    "data": {
        "reason": "HP_ARE_OVER",
        "hunter_player_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1"   // может быть null
    }
}
```

#### `player_died` – уведомление всех о смерти игрока

```json
{
    "type": "player_died",
    "data": {
        "reason": "HUNTER_FOUND_PLAYER",
        "player_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1",
        "hunter_player_id": "66665833-e5e8-4bba-ab83-e40d5d7182b1"
    }
}
```

#### `create_zone` / `delete_zone` – управление зонами на клиенте

**Создать зону:**
```json
{
    "type": "create_zone",
    "data": {
        "zone_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1",
        "zone_type": "DANGER",
        "center_lat": 20.2443,
        "center_lng": 33.2453,
        "radius": 11.323
    }
}
```

**Удалить зону:**
```json
{
    "type": "delete_zone",
    "data": { "zone_id": "23345833-e5e8-4bba-ab83-e40d5d7182b1" }
}
```

#### `player_entered_zone` / `player_exited_zone` – вход/выход из зоны

```json
{
    "type": "player_entered_zone",
    "data": {
        "zone_id": "uuid",
        "zone_type": "DANGER",
        "center_lat": 20.2443,
        "center_lng": 33.2453,
        "radius": 10.0
    }
}
```

```json
{
    "type": "player_exited_zone",
    "data": {
        "zone_id": "uuid",
        "zone_type": "DANGER"
    }
}
```

#### `airdrop_collected` – подобран аирдроп

```json
{
    "type": "airdrop_collected",
    "data": {
        "ability": {
            "id": "23345833-e5e8-4bba-ab83-e40d5d7182b1",
            "ability_type": "TRAP",
            "recharge_time": 60,
            "number_uses": 1,
            "duration_seconds": 300
        }
    }
}
```

#### `player_online` / `player_offline` – подключение/отключение игрока

```json
{
    "type": "player_online",
    "player_id": "uuid",
    "player_name": "Igor",
    "role": "Amogus"
}
```

```json
{
    "type": "player_offline",
    "player_id": "uuid",
    "player_name": "Igor"
}
```

---

## 🧩 Способности (abilities)

Роли получают способности из конфигурации игры. Шаблоны `abilities_configurations`:

| Ability         | Описание                | `addition_data`                                  |
|-----------------|-------------------------|--------------------------------------------------|
| `SHIELD`        | Щит                     | `{}`                                             |
| `INTEL`         | Разведданные            | `{}`                                             |
| `SCAN`          | Сканирование местности  | `{}`                                             |
| `PERSONAL_BOMB` | Персональная бомба      | `{"radius": 10.0, "damage": 100}`                |
| `TRAP`          | Ловушка                 | `{"radius": 10.0, "trap_duration_seconds": 120}` |
| `SNARE`         | Капкан                  | `{"radius": 10.0, "trap_duration_seconds": 600}` |
| `SAFE_HOUSE`    | Я в домике              | `{"radius": 20.0}`                               |
| `SAFE_MANSION`  | Я в особняке            | `{"radius": 30.0}`                               |

**Общие поля способности:**
- `duration_seconds` – длительность действия (сек)
- `number_uses` – количество использований
- `recharge_time` – перезарядка (сек)

Пример указания способностей для роли при создании игры:
```json
"roles_abilities": {
    "Amogus": {
        "TRAP": { "duration_seconds": 600, "number_uses": 2, "recharge_time": 60, "addition_data": { "radius": 10.0, "trap_duration_seconds": 120 } },
        "SCAN": { "duration_seconds": 600, "number_uses": 2, "recharge_time": 60, "addition_data": {} }
    }
}
```

---

## 🌍 Типы зон (zone_type)

| Тип            | Цвет      | Описание                 |
|----------------|-----------|--------------------------|
| `SAFE`         | Синий     | Начальная безопасная зона |
| `DANGER`       | Красный   | Зона бомбы               |
| `WARNING`      | Оранжевый | Зона бомбардировки       |
| `AIRDROP`      | Жёлтый    | Зона аирдропа            |
| `SNARE`        | Серый     | Капкан                   |
| `TRAP`         | Чёрный    | Ловушка                  |
| `SAFE_HOUSE`   | –         | Я в домике (защитная зона) |
| `SAFE_MANSION` | –         | Я в особняке (большая защитная зона) |

---

## ⚡ События (events)

Глобальные события, настраиваемые при создании игры:

```json
"events_configurations": {
    "BOMB": {
        "activation_frequency": "FREQUENT",
        "addition_data": { "duration_seconds": 60, "radius": 10.0, "damage": 100 }
    },
    "AIRDROP": {
        "activation_frequency": "RARE",
        "addition_data": { "radius": 10.0 }
    },
    "BOMBARDMENT": {
        "activation_frequency": "COMMON",
        "addition_data": { "duration_seconds": 60, "radius": 5.0, "damage": 50 }
    },
    "REVEAL": {
        "activation_frequency": "COMMON",
        "addition_data": { "duration_seconds": 60 }
    }
}
```

**Частоты активации:** `FREQUENT`, `COMMON`, `RARE`.

Связь ролей с событиями задаётся через `roles_events`:

```json
"roles_events": {
    "Amogus": ["BOMB", "AIRDROP", "BOMBARDMENT"],
    "Hunter": ["BOMB", "BOMBARDMENT"],
    "Hider": ["BOMB", "AIRDROP", "BOMBARDMENT"]
}
```

---

## 💀 Причины смерти (PlayerDeathCauses)

| Значение                 | Описание                         |
|--------------------------|----------------------------------|
| `HUNTER_FOUND_PLAYER`    | Игрок найден охотником           |
| `HP_ARE_OVER`            | Здоровье закончилось (урон от зон, бомб и т.д.) |

---

## 📌 Примечания

- Все временные значения указываются в **секундах**.
- Координаты – широта (`lat`) и долгота (`lng`).
- UUID генерируются сервером и имеют формат `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`.
- После получения `you_died` клиент **обязан** закрыть WebSocket-соединение.
- В статусе `WAITING` игроки могут менять роль и статус готовности, но не могут использовать способности или перемещаться (обновление локации игнорируется).
- Переход в `ACTIVE` происходит автоматически после того, как все игроки подтвердили готовность и закончилось время на подготовку (`time_to_hide`).

---

*Документация актуальна на июнь 2026 года.*