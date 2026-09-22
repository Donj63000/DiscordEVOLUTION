"""Serveur de suivi : vrais échanges HTTP locaux, aucun accès à Discord."""
import asyncio
import sys
from unittest.mock import Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import alive


@pytest.mark.asyncio
async def test_readiness_tracks_state_without_changing_home():
    alive.set_ready(False)
    try:
        async with TestClient(TestServer(alive.create_aiohttp_app())) as client:
            for ready, status in [(False, 503), (True, 200), (False, 503)]:
                alive.set_ready(ready)
                response = await client.get("/ready")
                assert response.status == status
                assert await response.text() == ("ready" if ready else "not ready")
                home = await client.get("/")
                assert home.status == 200
                assert await home.text() == alive.HOME_TEXT
    finally:
        alive.set_ready(False)


def test_wsgi_readiness():
    client = alive.create_app().test_client()
    try:
        for ready, status in [(False, 503), (True, 200), (False, 503)]:
            alive.set_ready(ready)
            assert client.get("/ready").status_code == status
    finally:
        alive.set_ready(False)


def test_original_port_and_command_contract(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    assert alive.resolve_port() == 8080
    monkeypatch.setenv("PORT", "invalid")
    assert alive.resolve_port() == 8080
    monkeypatch.setenv("ALIVE_WORKERS", "2")
    monkeypatch.setenv("ALIVE_THREADS", "8")
    command = alive.build_gunicorn_command(5050)
    assert command[:4] == [sys.executable, "-m", "gunicorn", "alive:app"]
    assert "0.0.0.0:5050" in command
    assert command[command.index("--workers") + 1] == "2"
    assert command[command.index("--threads") + 1] == "8"


@pytest.mark.parametrize("mode", ["aiohttp", "gunicorn", "wsgiref"])
def test_server_mode_is_explicit_and_reversible(monkeypatch, mode):
    monkeypatch.setenv("ALIVE_SERVER", mode)
    assert alive.resolve_server_mode() == mode


def test_wsgi_application_stays_available_and_is_created_once(monkeypatch):
    app = object()
    create = Mock(return_value=app)
    monkeypatch.setattr(alive, "_wsgi_app", None)
    monkeypatch.setattr(alive, "create_app", create)
    assert alive.app is app
    assert alive.get_wsgi_app() is app
    create.assert_called_once()
    with pytest.raises(AttributeError):
        getattr(alive, "missing_attribute")


def test_aiohttp_mode_does_not_spawn_gunicorn(monkeypatch):
    monkeypatch.setenv("ALIVE_SERVER", "aiohttp")
    monkeypatch.setenv("PORT", "54321")
    runner = Mock()
    process = Mock(side_effect=AssertionError("Processus HTTP supplémentaire"))
    monkeypatch.setattr(web, "run_app", runner)
    monkeypatch.setattr(alive.subprocess, "Popen", process)
    alive.run_server(blocking=False)
    process.assert_not_called()
    runner.assert_called_once()
    options = runner.call_args.kwargs
    assert options["port"] == 54321
    assert options["host"] == "0.0.0.0"
    assert options["handle_signals"] is False


@pytest.mark.asyncio
async def test_actual_get_head_not_found_and_no_command_route():
    async with TestClient(TestServer(alive.create_aiohttp_app())) as client:
        response = await client.get("/")
        assert response.status == 200
        assert await response.text() == alive.HOME_TEXT
        response = await client.head("/")
        assert response.status == 200
        assert await response.read() == b""
        response = await client.get("/memory")
        assert response.status == 404
        response = await client.post("/")
        assert response.status == 405


@pytest.mark.asyncio
async def test_concurrent_health_requests():
    async with TestClient(TestServer(alive.create_aiohttp_app())) as client:
        async def fetch():
            async with client.get("/") as response:
                return response.status, await response.text()
        responses = await asyncio.gather(*(fetch() for _ in range(30)))
        assert all(item == (200, alive.HOME_TEXT) for item in responses)
