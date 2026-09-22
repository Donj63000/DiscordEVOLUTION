"""Je vérifie le verrou réel sans ouvrir le stockage ni contacter Discord."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from build import BuildCog, setup
from utils.build.ai_tools import NAMES, dispatch
from utils.build.models import BuildError
from utils.command_policy import build_available, unavailable_slash_roots
from utils.evo_config import EvoError
from utils.evo_tools import schemas_for
from utils.slash_sync import remove_retired_remote_commands


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [None, "", "false", "true"])
async def test_maintenance_blocks_every_entry_without_storage(monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv("BUILD_ENABLED", raising=False)
    else:
        monkeypatch.setenv("BUILD_ENABLED", setting)
    monkeypatch.setenv("BUILD_AI_ENABLED", "true")
    bot = SimpleNamespace(add_cog=AsyncMock())
    await setup(bot)
    bot.add_cog.assert_not_awaited()
    assert not build_available()
    assert "build" in unavailable_slash_roots()
    assert not ({schema["name"] for schema in schemas_for("mon build")} & NAMES)
    with pytest.raises(BuildError, match="maintenance"):
        BuildCog.ensure_ready(SimpleNamespace(closed=False, ready=True))
    ctx = SimpleNamespace(ensure_access=AsyncMock(), bot=Mock())
    with pytest.raises(EvoError, match="indisponibles"):
        await dispatch("build_lister", ctx)
    ctx.ensure_access.assert_not_awaited()
    ctx.bot.get_cog.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("guild", [None, discord.Object(id=100)])
async def test_cleanup_removes_build_only_in_global_and_guild_catalogs(monkeypatch, guild):
    monkeypatch.setenv("BUILD_ENABLED", "true")
    build = SimpleNamespace(name="build", type=discord.AppCommandType.chat_input,
                            delete=AsyncMock())
    activity = SimpleNamespace(name="activite", type=discord.AppCommandType.chat_input,
                               delete=AsyncMock())
    tree = SimpleNamespace(fetch_commands=AsyncMock(return_value=[build, activity]))
    assert await remove_retired_remote_commands(tree, guild=guild)
    tree.fetch_commands.assert_awaited_once_with(guild=guild)
    build.delete.assert_awaited_once()
    activity.delete.assert_not_awaited()
