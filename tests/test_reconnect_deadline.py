from datetime import datetime, timedelta, timezone

import pytest

from services.reconnect_deadline import can_reconnect, reconnect_deadline
from services.reconnect_grace import ReconnectGrace
from unittest.mock import AsyncMock
from uuid import uuid4
import asyncio


def test_reconnect_deadline_boundary():
    disconnected = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert can_reconnect(disconnected, disconnected + timedelta(seconds=59, milliseconds=999))
    assert not can_reconnect(disconnected, disconnected + timedelta(seconds=60))
    assert reconnect_deadline(disconnected) == disconnected + timedelta(minutes=1)


def test_reconnect_deadline_requires_aware_datetimes():
    aware = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        reconnect_deadline(datetime.now())
    with pytest.raises(ValueError):
        can_reconnect(aware, datetime.now())


@pytest.mark.asyncio
async def test_expired_disconnect_runs_callback():
    grace = ReconnectGrace()
    game, player = uuid4(), uuid4()
    expire = AsyncMock()
    grace.start(game, player, datetime.now(timezone.utc) - timedelta(minutes=2), expire)
    await grace._tasks[(game, player)]
    expire.assert_awaited_once_with(game, player)


@pytest.mark.asyncio
async def test_reconnect_cancels_pending_exclusion():
    grace = ReconnectGrace()
    game, player = uuid4(), uuid4()
    expire = AsyncMock()
    grace.start(game, player, datetime.now(timezone.utc), expire)
    task = grace._tasks[(game, player)]
    grace.cancel(game, player)
    with pytest.raises(asyncio.CancelledError):
        await task
    expire.assert_not_awaited()
