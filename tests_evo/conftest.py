"""Explicit synthetic single-guild configuration for Evo regression scenarios."""
import pytest


@pytest.fixture(autouse=True)
def evolution_test_guild(monkeypatch):
    monkeypatch.setenv("BOT_GUILD_ID", "1")
    monkeypatch.delenv("EVO_GUILD_ID", raising=False)
