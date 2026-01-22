import sys

import alive


def test_resolve_port_defaults_to_8080(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    assert alive.resolve_port() == 8080


def test_resolve_port_invalid_uses_default(monkeypatch):
    monkeypatch.setenv("PORT", "invalid")
    assert alive.resolve_port() == 8080


def test_resolve_server_mode_env_override(monkeypatch):
    monkeypatch.setenv("ALIVE_SERVER", "wsgiref")
    assert alive.resolve_server_mode() == "wsgiref"


def test_build_gunicorn_command_uses_port_and_defaults(monkeypatch):
    monkeypatch.setenv("ALIVE_WORKERS", "2")
    monkeypatch.setenv("ALIVE_THREADS", "8")
    cmd = alive.build_gunicorn_command(5050)
    assert cmd[0] == sys.executable
    assert "alive:app" in cmd
    assert "0.0.0.0:5050" in cmd
    assert "--workers" in cmd
    assert "--threads" in cmd
