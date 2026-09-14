"""Je simule Discord pour vérifier les suppressions et éditions ciblées du catalogue."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord import app_commands

from test_slash_confirm_sync import sync_bot
from utils.slash_sync import (
    cleanup_retired_guild_commands, remove_retired_remote_commands, sync_application_commands,
)


@pytest.fixture
def remote_catalog():
    class RemoteCatalog:
        def __init__(self):
            self.records = {}
            self.state = SimpleNamespace(application_id=777)
            self.state.http = SimpleNamespace(
                edit_global_command=AsyncMock(side_effect=self.edit_global),
                edit_guild_command=AsyncMock(side_effect=self.edit_guild),
                delete_global_command=AsyncMock(side_effect=self.delete_global),
                delete_guild_command=AsyncMock(side_effect=self.delete_guild),
            )
            self.tree = SimpleNamespace(fetch_commands=AsyncMock(side_effect=self.fetch))

        def add(self, name, guild_id=None, *, kind=1, options=()):
            identifier = 1000 + len(self.records)
            data = {
                "id": str(identifier), "application_id": "777", "version": "9000",
                "name": name, "type": kind, "description": "Description conservée",
                "name_localizations": {"en-US": name},
                "description_localizations": {"en-US": "Existing description"},
                "default_member_permissions": "32", "dm_permission": False,
                "contexts": [0], "integration_types": [0], "nsfw": False,
                "options": deepcopy(list(options)),
            }
            if guild_id is not None:
                data["guild_id"] = str(guild_id)
            command = app_commands.AppCommand(data=data, state=self.state)
            data["options"] = [option.to_dict() for option in command.options]
            self.records[(guild_id, identifier)] = data
            return identifier

        async def fetch(self, *, guild=None):
            guild_id = getattr(guild, "id", None)
            return [app_commands.AppCommand(data=deepcopy(data), state=self.state)
                    for (scope, _), data in self.records.items() if scope == guild_id]

        async def edit_global(self, application, identifier, payload):
            return self.edit(None, identifier, payload)

        async def edit_guild(self, application, guild_id, identifier, payload):
            return self.edit(guild_id, identifier, payload)

        def edit(self, scope, identifier, payload):
            self.records[(scope, identifier)].update(deepcopy(payload))
            return deepcopy(self.records[(scope, identifier)])

        async def delete_global(self, application, identifier):
            self.records.pop((None, identifier))

        async def delete_guild(self, application, guild_id, identifier):
            self.records.pop((guild_id, identifier))

    return RemoteCatalog()


def stats_options():
    return [
        {"type": 1, "name": "classement", "description": "Ancien doublon"},
        {
            "type": 1, "name": "presence", "description": "Données de présence",
            "name_localizations": {"en-US": "presence"},
            "description_localizations": {"en-US": "Presence data"},
            "options": [
                {"type": 3, "name": "recherche", "description": "Recherche", "required": True,
                 "min_length": 1, "max_length": 100, "autocomplete": True,
                 "name_localizations": {"en-US": "search"},
                 "description_localizations": {"en-US": "Search"}},
                {"type": 4, "name": "limite", "description": "Limite", "required": False,
                 "min_value": 1, "max_value": 100,
                 "choices": [{"name": "Dix", "value": 10,
                              "name_localizations": {"en-US": "Ten"}}]},
                {"type": 7, "name": "salon", "description": "Salon", "channel_types": [0, 2]},
            ],
        },
        {"type": 2, "name": "archive", "description": "Autre groupe", "options": [
            {"type": 1, "name": "classement", "description": "Homonyme conservé"},
        ]},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("guild_id", [None, 100])
@pytest.mark.parametrize("ai_enabled", [False, True])
async def test_remote_cleanup_only_changes_retired_paths(remote_catalog, monkeypatch, guild_id, ai_enabled):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "1" if ai_enabled else "0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    identifier = remote_catalog.add("stats", guild_id, options=stats_options())
    legacy_id = remote_catalog.add("annonce-config", guild_id)
    event_id = remote_catalog.add("event-rapide", guild_id)
    for name in ("exo", "objet", "recette", "equipement", "monstre", "ladder", "organisation",
                 "annonce-list", "annonce-cancel", "inconnue", "stats-autres"):
        remote_catalog.add(name, guild_id)
    remote_catalog.add("annonce-config", guild_id, kind=2)
    remote_catalog.add("annonce-config", guild_id, kind=3)
    remote_catalog.add("stats", guild_id, kind=3)
    before = deepcopy(remote_catalog.records)
    guild = discord.Object(id=guild_id) if guild_id is not None else None

    assert await remove_retired_remote_commands(remote_catalog.tree, guild=guild)
    expected = deepcopy(before)
    expected.pop((guild_id, legacy_id))
    expected.pop((guild_id, event_id))
    expected[(guild_id, identifier)]["options"] = before[(guild_id, identifier)]["options"][1:]
    assert remote_catalog.records == expected
    http = remote_catalog.state.http
    payload = {"options": expected[(guild_id, identifier)]["options"]}
    if guild_id is None:
        http.edit_global_command.assert_awaited_once_with(777, identifier, payload)
        http.edit_guild_command.assert_not_awaited()
        assert [call.args[1] for call in http.delete_global_command.await_args_list] == [legacy_id, event_id]
        http.delete_guild_command.assert_not_awaited()
    else:
        http.edit_guild_command.assert_awaited_once_with(777, guild_id, identifier, payload)
        http.edit_global_command.assert_not_awaited()
        assert [call.args[2] for call in http.delete_guild_command.await_args_list] == [legacy_id, event_id]
        http.delete_global_command.assert_not_awaited()
    assert await remove_retired_remote_commands(remote_catalog.tree, guild=guild)
    assert remote_catalog.records == expected
    assert http.edit_global_command.await_count + http.edit_guild_command.await_count == 1
    assert http.delete_global_command.await_count + http.delete_guild_command.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    [],
    [{"type": 1, "name": "presence", "description": "Présence"}],
    [{"type": 3, "name": "classement", "description": "Argument homonyme"}],
    [{"type": 2, "name": "classement", "description": "Groupe homonyme", "options": [
        {"type": 1, "name": "voir", "description": "Voir"},
    ]}],
])
async def test_no_remote_edit_without_the_exact_subcommand(remote_catalog, options):
    remote_catalog.add("stats", options=options)
    before = deepcopy(remote_catalog.records)
    assert await remove_retired_remote_commands(remote_catalog.tree)
    assert remote_catalog.records == before
    for method in vars(remote_catalog.state.http).values():
        method.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("guild_id", [None, 100])
async def test_failed_subcommand_edit_can_be_retried(remote_catalog, guild_id):
    remote_catalog.add("stats", guild_id, options=stats_options())
    before = deepcopy(remote_catalog.records)
    guild = discord.Object(id=guild_id) if guild_id else None
    http = remote_catalog.state.http
    method = http.edit_guild_command if guild_id else http.edit_global_command
    original = method.side_effect
    method.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Test")
    assert not await remove_retired_remote_commands(remote_catalog.tree, guild=guild)
    assert remote_catalog.records == before
    method.side_effect = original
    assert await remove_retired_remote_commands(remote_catalog.tree, guild=guild)
    assert method.await_count == 2
    http.delete_global_command.assert_not_awaited()
    http.delete_guild_command.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("evo_enabled", [False, True])
async def test_active_sync_removes_legacy_root_even_with_ai_enabled(
    sync_bot, monkeypatch, evo_enabled,
):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "1")
    monkeypatch.setenv("EVO_ENABLED", "1" if evo_enabled else "0")
    monkeypatch.setenv("EVO_ALLOW_LEGACY_AI", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("SYNC_SLASH_GUILD_ID", "100")
    assert await sync_application_commands(sync_bot)
    names = {call.args[0] for call in sync_bot.tree.remove_command.call_args_list}
    expected = {"annonce-config", "event-rapide"}
    if not evo_enabled:
        expected.update({"evo", "evo-budget", "evo-oublier"})
    assert names == expected
    sync_bot.tree.sync.assert_awaited_once_with(guild=discord.Object(id=100))


@pytest.mark.asyncio
@pytest.mark.parametrize("guild_id", [None, "100"])
async def test_cleanup_flag_preserves_every_secondary_catalog(sync_bot, monkeypatch, guild_id):
    monkeypatch.setenv("SLASH_CLEANUP_RETIRED", "0")
    if guild_id is not None:
        monkeypatch.setenv("SYNC_SLASH_GUILD_ID", guild_id)
    assert await sync_application_commands(sync_bot)
    sync_bot._slash_global_cleanup_pending = True
    await cleanup_retired_guild_commands(sync_bot)
    sync_bot.tree.fetch_commands.assert_not_awaited()
    sync_bot.tree.sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_global_cleanup_failure_is_retried_on_reconnect(sync_bot, monkeypatch):
    monkeypatch.setenv("SYNC_SLASH_GUILD_ID", "100")
    failure = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Test")
    sync_bot.tree.fetch_commands.side_effect = [failure, failure, [], [], []]
    assert await sync_application_commands(sync_bot)
    assert sync_bot._slash_global_cleanup_pending
    await cleanup_retired_guild_commands(sync_bot)
    assert sync_bot._slash_global_cleanup_pending
    assert sync_bot._slash_cleanup_done == {100, 200}
    await cleanup_retired_guild_commands(sync_bot)
    assert not sync_bot._slash_global_cleanup_pending
    assert [getattr(call.kwargs["guild"], "id", None)
            for call in sync_bot.tree.fetch_commands.await_args_list] == [None, None, 100, 200, None]
    await cleanup_retired_guild_commands(sync_bot)
    assert sync_bot.tree.fetch_commands.await_count == 5
    sync_bot.tree.sync.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [False, True])
async def test_pending_global_cleanup_never_runs_after_failed_or_disabled_sync(sync_bot, monkeypatch, disabled):
    sync_bot._slash_global_cleanup_pending = True
    if disabled:
        monkeypatch.setenv("SYNC_SLASH_COMMANDS", "0")
    else:
        sync_bot.tree.sync.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Test",
        )
    assert not await sync_application_commands(sync_bot)
    await cleanup_retired_guild_commands(sync_bot)
    sync_bot.tree.fetch_commands.assert_not_awaited()
    assert not sync_bot._slash_global_cleanup_pending
