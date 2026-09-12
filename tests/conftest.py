import discord
import pytest


@pytest.fixture(autouse=True)
def _patch_discord_utils(monkeypatch):
    monkeypatch.delenv("XIXOU_API_KEY", raising=False)
    monkeypatch.setattr(discord.utils, "evaluate_annotation", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(discord.utils, "is_inside_class", lambda obj: False, raising=False)
