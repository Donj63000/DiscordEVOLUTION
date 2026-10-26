import os
os.environ.setdefault("BOT_GUILD_ID", "1")

import discord
import pytest


@pytest.fixture(autouse=True)
def _patch_discord_utils(monkeypatch):
    # Every test starts from a known single-guild configuration. Module-level
    # fixtures override this explicitly; negative tests remove/change it in
    # their body. Failed unittest setup must not leak configuration downstream.
    monkeypatch.setenv("BOT_GUILD_ID", "1")
    monkeypatch.delenv("EVO_GUILD_ID", raising=False)
    monkeypatch.delenv("XIXOU_API_KEY", raising=False)
    monkeypatch.setattr(discord.utils, "evaluate_annotation", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(discord.utils, "is_inside_class", lambda obj: False, raising=False)
