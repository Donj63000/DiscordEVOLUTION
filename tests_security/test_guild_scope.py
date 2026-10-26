"""Single-guild boundaries exercised with the real discord.py dispatcher."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from utils.guild_scope import (
    GuildScopeError, allowed_guilds, get_allowed_guild_id, is_allowed_guild, require_guild,
)


@pytest.fixture(autouse=True)
def official_guild(monkeypatch):
    monkeypatch.setenv("BOT_GUILD_ID", "1")
    monkeypatch.delenv("EVO_GUILD_ID", raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "offline-test-token")


def test_missing_invalid_or_conflicting_configuration_fails_closed(monkeypatch):
    for value in ("", "0", "-1", "1,2", "abc", str(2**64)):
        monkeypatch.setenv("BOT_GUILD_ID", value)
        with pytest.raises(GuildScopeError):
            get_allowed_guild_id()
        assert not is_allowed_guild(1)
    monkeypatch.setenv("BOT_GUILD_ID", "1")
    monkeypatch.setenv("EVO_GUILD_ID", "2")
    with pytest.raises(GuildScopeError, match="correspondre"):
        get_allowed_guild_id()


def test_empty_startup_cache_never_infers_another_guild():
    assert get_allowed_guild_id() == 1
    assert allowed_guilds(SimpleNamespace(guilds=[])) == []
    official, foreign = SimpleNamespace(id=1), SimpleNamespace(id=2)
    assert allowed_guilds(SimpleNamespace(guilds=[foreign, official])) == [official]
    with pytest.raises(GuildScopeError):
        require_guild(foreign)


@pytest.mark.asyncio
async def test_real_client_blocks_foreign_listeners_and_component_ingress():
    import main

    bot = main.EvoBot()
    await bot._async_setup_hook()
    callback = AsyncMock()
    bot.add_listener(callback, "on_member_remove")
    try:
        bot.dispatch("member_remove", SimpleNamespace(guild=SimpleNamespace(id=2)))
        await asyncio.sleep(0)
        callback.assert_not_awaited()
        bot.dispatch("member_remove", SimpleNamespace(guild=SimpleNamespace(id=1)))
        await asyncio.sleep(0)
        callback.assert_awaited_once()
        # ViewStore dispatch occurs before Client.dispatch in discord.py. The
        # gateway parser must reject foreign buttons and modal submissions first.
        views = Mock()
        bot._connection._view_store.dispatch_view = views
        bot._connection.parsers["INTERACTION_CREATE"]({
            "guild_id": "2", "type": 3,
            "data": {"component_type": 2, "custom_id": "old-persistent-button"},
        })
        bot._connection.parsers["INTERACTION_CREATE"]({"guild_id": "2", "type": 5})
        views.assert_not_called()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_prefix_global_check_and_native_slash_reject_foreign_context():
    import main

    bot = main.EvoBot()
    await bot._async_setup_hook()
    try:
        ctx = SimpleNamespace(guild=SimpleNamespace(id=2), command=None)
        with pytest.raises(GuildScopeError):
            await bot.can_run(ctx, call_once=True)
        interaction = SimpleNamespace(
            guild_id=2, guild=ctx.guild, data={}, is_expired=lambda: False,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
        )
        assert not await bot.tree.interaction_check(interaction)
        interaction.response.send_message.assert_awaited_once()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_direct_confirmation_callback_and_console_service_are_scoped():
    from utils.console_json_store import ConsoleJSONSnapshotStore
    from utils.slash_confirm import ActionConfirmation

    bot = SimpleNamespace(guilds=[SimpleNamespace(id=1)])
    foreign = SimpleNamespace(guild_id=2, user=SimpleNamespace(id=5),
                              response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))
    view = ActionConfirmation(bot, foreign, SimpleNamespace(), "", {})
    assert not await view.interaction_check(foreign)
    store = ConsoleJSONSnapshotStore(bot, marker="test", filename="synthetic.json")
    with pytest.raises(GuildScopeError):
        await store.resolve_channel(SimpleNamespace(id=2))


@pytest.mark.asyncio
async def test_private_component_parser_requires_current_evolution_membership(monkeypatch):
    import discord.state
    import main

    parsed = Mock()

    def parse_component(state, payload):
        parsed(payload)

    monkeypatch.setattr(discord.state.ConnectionState, "parse_interaction_create", parse_component)
    bot = main.EvoBot()
    await bot._async_setup_hook()
    members = {5: SimpleNamespace(id=5)}
    bot._connection._guilds[1] = SimpleNamespace(id=1, get_member=members.get)
    try:
        payload = {"type": 3, "user": {"id": "5"}}
        bot._connection.parsers["INTERACTION_CREATE"](payload)
        parsed.assert_called_once_with(payload)
        parsed.reset_mock()
        members.clear()
        bot._connection.parsers["INTERACTION_CREATE"](payload)
        bot._connection.parsers["INTERACTION_CREATE"]({"type": 5, "user": {"id": "99"}})
        bot._connection.parsers["INTERACTION_CREATE"]({"type": 2, "user": {"id": "5"}})
        parsed.assert_not_called()
    finally:
        await bot.close()
