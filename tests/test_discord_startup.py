"""Je simule les limitations de démarrage sans contacter Discord."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from utils import discord_startup as startup


def failure(status=429):
    response = SimpleNamespace(status=status, reason="limited", headers={})
    return discord.HTTPException(response, "secret HTML must not be logged")


class Client:
    token = "secret-token"

    def __init__(self, error=None, setup=False):
        self._startup_setup_started = setup
        self.start = AsyncMock(side_effect=error)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


@pytest.mark.asyncio
async def test_retry_closes_clients_and_recovers(monkeypatch, caplog):
    clients = [Client(failure()) for _ in range(4)] + [Client()]
    factory = Mock(side_effect=clients[1:])
    delays = []

    async def sleep(delay):
        assert clients[len(delays)].closed
        delays.append(delay)

    monkeypatch.setattr(startup.asyncio, "sleep", sleep)
    monkeypatch.setattr(startup.random, "uniform", lambda *_: 2)
    await startup.run_clients(factory, clients[0])
    assert delays == [902, 1802, 3602, 3602]
    assert all(client.closed for client in clients)
    for client in clients:
        client.start.assert_awaited_once_with("secret-token", reconnect=True)
    assert "secret" not in caplog.text
    assert "status=429" in caplog.text


@pytest.mark.parametrize("raw", [None, "", "bad", "nan", "inf", "-1"])
def test_invalid_retry_header_uses_backoff(monkeypatch, raw):
    monkeypatch.setattr(startup.random, "uniform", lambda *_: 1)
    assert startup.retry_delay(1, {"Retry-After": raw}) == 901


def test_retry_header_can_exceed_backoff(monkeypatch):
    monkeypatch.setattr(startup.random, "uniform", lambda *_: 1)
    assert startup.retry_delay(1, {"Retry-After": "7200"}) == 7201
    deadline = datetime.now(timezone.utc) + timedelta(hours=2)
    assert 7199 < startup.retry_delay(1, {"Retry-After": format_datetime(deadline)}) <= 7201


@pytest.mark.asyncio
@pytest.mark.parametrize("error,setup", [
    (failure(403), False), (discord.LoginFailure("invalid"), False),
    (failure(), True), (RuntimeError("extension failed"), True),
])
async def test_fatal_errors_are_not_retried(monkeypatch, error, setup):
    client = Client(error, setup)
    factory = Mock()
    sleep = AsyncMock()
    monkeypatch.setattr(startup.asyncio, "sleep", sleep)
    with pytest.raises(type(error)):
        await startup.run_clients(factory, client)
    assert client.closed
    factory.assert_not_called()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_during_wait_does_not_create_another_client(monkeypatch):
    client = Client(failure())
    factory = Mock()
    monkeypatch.setattr(startup.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await startup.run_clients(factory, client)
    assert client.closed
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_factory_has_independent_commands_and_clean_state():
    import main

    clients = [main.create_bot(), main.create_bot()]
    try:
        for client in clients:
            assert len([c for c in client.commands if c.name == "ping"]) == 1
            assert client.on_command_error is main.on_command_error
            assert not client.extensions
            assert not client._startup_setup_started
            assert not client._singleton_ready
        assert clients[0].get_command("ping") is not clients[1].get_command("ping")
    finally:
        for client in clients:
            await client.close()


@pytest.mark.asyncio
async def test_readiness_requires_connection_and_leadership(monkeypatch):
    import alive
    import main

    client = main.create_bot()
    client._connection.user = SimpleNamespace(id=1, name="test")
    client.ensure_console_channel = AsyncMock()
    client.acquire_leadership = AsyncMock(return_value=True)
    client.heartbeat_loop = AsyncMock()
    client._suspend_evo = AsyncMock()
    monkeypatch.setenv("SYNC_BOT_IDENTITY", "0")
    monkeypatch.setattr(main, "cleanup_retired_guild_commands", AsyncMock())
    alive.set_ready(False)
    try:
        await client.on_resumed()
        assert not alive._ready.is_set()
        await client.on_ready()
        assert alive._ready.is_set()
        await client.on_ready()
        client.acquire_leadership.assert_awaited_once()
        await asyncio.sleep(0)
        client.heartbeat_loop.assert_awaited_once()
        await client.on_disconnect()
        assert not alive._ready.is_set()
        await client.on_resumed()
        assert alive._ready.is_set()
    finally:
        await client.close()
    assert not alive._ready.is_set()


@pytest.mark.asyncio
async def test_sigterm_cancels_and_removes_handler(monkeypatch):
    loop = asyncio.get_running_loop()
    handlers = {}
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, callback: handlers.update({sig: callback}))
    remove = Mock()
    monkeypatch.setattr(loop, "remove_signal_handler", remove)

    async def run(*args):
        handlers[startup.signal.SIGTERM]()
        await asyncio.sleep(0)

    monkeypatch.setattr(startup, "run_clients", run)
    task = asyncio.create_task(startup.run_with_shutdown(Mock()))
    with pytest.raises(asyncio.CancelledError):
        await task
    remove.assert_called_once_with(startup.signal.SIGTERM)
