"""Je vérifie les retraits slash sans modifier les parcours métier conservés."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands

from slash_commands import SlashCommandsCog
from test_slash_commands import load_command_inventory, make_interaction, slash_bot
from utils.command_policy import (
    retired_slash_reason, unavailable_reason, unavailable_slash_roots,
)
from utils.slash_help import help_catalog
from utils.slash_support import interaction_command_path


@pytest.fixture(params=[False, True])
def ai_enabled(request, monkeypatch):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "1" if request.param else "0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    return request.param


def test_slash_retirement_is_independent_from_prefix_policy(ai_enabled):
    assert retired_slash_reason(("stats", "classement"))
    assert retired_slash_reason(("annonce-config",))
    assert unavailable_reason("stats ladder") is None
    assert (unavailable_reason("annonce-config") is None) == ai_enabled
    assert "annonce-config" in unavailable_slash_roots()
    assert "event-rapide" in unavailable_slash_roots()
    assert "stats" not in unavailable_slash_roots()
    for path in [("stats",), ("stats", "presence"), ("ladder",), ("exo",),
                 ("stats", "classement", "autre"), ("autre", "classement")]:
        assert retired_slash_reason(path) is None


@pytest.mark.asyncio
async def test_catalog_only_loses_the_two_retired_entries(slash_bot, monkeypatch, ai_enabled):
    load_command_inventory(slash_bot)

    @slash_bot.command(name="annonce-config")
    async def legacy_config(ctx):
        await ctx.send("Configuration historique")

    exo = slash_bot.tree.get_command("exo")
    native_schema = deepcopy(exo.to_dict(slash_bot.tree))
    native_autocomplete = {name: param.autocomplete for name, param in exo._params.items()}
    with monkeypatch.context() as original_policy:
        original_policy.setattr("slash_commands.retired_slash_reason", lambda path: None)
        original_cog = SlashCommandsCog(slash_bot)
        original_cog.register_commands()
    before = {
        command.name: deepcopy(command.to_dict(slash_bot.tree))
        for command in slash_bot.tree.get_commands()
    }
    prefix_commands = {command.qualified_name: command for command in slash_bot.walk_commands()}
    original_cog.cog_unload()

    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    try:
        actual = {
            command.name: command.to_dict(slash_bot.tree)
            for command in slash_bot.tree.get_commands()
        }
        expected = deepcopy(before)
        expected.pop("annonce-config", None)
        expected["stats"]["options"] = [
            option for option in expected["stats"]["options"] if option["name"] != "classement"
        ]
        assert actual == expected
        assert {command.qualified_name: command for command in slash_bot.walk_commands()} == prefix_commands
        assert slash_bot.get_command("stats ladder") is prefix_commands["stats ladder"]
        assert slash_bot.get_command("ladder") is prefix_commands["ladder"]
        if ai_enabled:
            assert slash_bot.get_command("annonce-config") is legacy_config
        assert set(cog.excluded_commands) == (
            {"stats ladder", "annonce-config"} if ai_enabled else {"stats ladder"}
        )
        assert cog.covered_commands | cog.excluded_commands.keys() == prefix_commands.keys()
        assert cog.covered_commands.isdisjoint(cog.excluded_commands)
        listed = {command.qualified_name for entries in help_catalog(slash_bot).values()
                  for command in entries}
        assert "stats classement" not in listed
        assert "annonce-config" not in listed
        assert {"ladder", "stats presence", "annonce-list", "annonce-cancel", "organisation"} <= listed
        assert {"exo", "objet", "recette", "equipement", "monstre"} <= listed
        assert slash_bot.tree.get_command("exo") is exo
        assert exo.to_dict(slash_bot.tree) == native_schema
        assert {name: param.autocomplete for name, param in exo._params.items()} == native_autocomplete
        for name in ("objet", "recette", "equipement", "monstre"):
            assert actual[name] == before[name]
            assert any(option.get("autocomplete") for option in actual[name]["options"])
    finally:
        cog.cog_unload()


@pytest.mark.asyncio
async def test_prefix_stats_ladder_still_forwards_all_options(slash_bot):
    from stats import StatsCog

    load_command_inventory(slash_bot)
    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    context = SimpleNamespace(invoke=AsyncMock(), send=AsyncMock())
    try:
        await StatsCog.stats_ladder.callback(
            SimpleNamespace(bot=slash_bot), context, arg="class iop page 2",
        )
        context.invoke.assert_awaited_once_with(
            slash_bot.get_command("ladder"), arg="class iop page 2",
        )
        context.send.assert_not_awaited()
    finally:
        cog.cog_unload()


@pytest.mark.asyncio
async def test_retired_native_entries_do_not_remove_siblings_or_context_menus(slash_bot, monkeypatch):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def callback(interaction: discord.Interaction):
        pass

    async def message_callback(interaction: discord.Interaction, message: discord.Message):
        pass

    stats = app_commands.Group(name="stats", description="Statistiques")
    stats.add_command(app_commands.Command(name="classement", description="Ancien relais", callback=callback))
    presence = app_commands.Command(name="presence", description="Présence", callback=callback)
    stats.add_command(presence)
    slash_bot.tree.add_command(stats)
    slash_bot.tree.add_command(app_commands.Command(
        name="annonce-config", description="Ancien réglage", callback=callback,
    ))
    menu = app_commands.ContextMenu(name="annonce-config", callback=message_callback)
    slash_bot.tree.add_command(menu)

    @slash_bot.command(name="annonce-config")
    async def legacy_config(ctx):
        pass

    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    assert slash_bot.tree.get_command("annonce-config") is None
    assert slash_bot.tree.get_command("annonce-config", type=discord.AppCommandType.message) is menu
    assert slash_bot.get_command("annonce-config") is legacy_config
    assert set(cog.excluded_commands) == {"annonce-config"}
    assert slash_bot.tree.get_command("stats") is stats
    assert stats.commands == [presence]


@pytest.mark.asyncio
@pytest.mark.parametrize("data,replacement", [
    ({"name": "stats", "type": 1, "options": [{"name": "classement", "type": 1}]}, "/ladder"),
    ({"name": "annonce-config", "type": 1}, "configuration"),
])
async def test_stale_menu_is_rejected_before_local_lookup(slash_bot, ai_enabled, data, replacement):
    click = make_interaction(slash_bot, None)
    click.data = data
    slash_bot.tree._get_app_command_options = Mock(side_effect=AssertionError("Résolution interdite"))
    await slash_bot.tree._call(click)
    slash_bot.tree._get_app_command_options.assert_not_called()
    assert click.command_failed
    click.response.send_message.assert_awaited_once()
    assert click.response.send_message.call_args.kwargs["ephemeral"]
    assert replacement in click.response.send_message.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {"name": "stats", "type": 1, "options": [{"name": "presence", "type": 1}]},
    {"name": "stats", "type": 1, "options": [{"name": "classement", "type": 3, "value": "texte"}]},
    {"name": "stats", "type": 1, "options": [{"name": "autre", "type": 2, "options": [
        {"name": "classement", "type": 1}]}]},
    {"name": "stats", "type": 1, "options": [{"name": "classement", "type": 2, "options": [
        {"name": "voir", "type": 1}]}]},
    {"name": "annonce-config", "type": 2},
    {"name": "annonce-config", "type": 3},
    {"name": "exo", "type": 1, "options": [{"name": "objet", "type": 3, "value": "stats classement"}]},
    {"name": "objet", "type": 1, "options": [{"name": "nom", "type": 3, "value": "annonce-config"}]},
])
async def test_other_paths_and_context_menus_remain_usable(slash_bot, monkeypatch, data):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    click = make_interaction(slash_bot, None)
    click.data = data
    assert await slash_bot.tree.interaction_check(click)
    click.response.send_message.assert_not_awaited()


def test_path_reconstruction_only_traverses_subcommands():
    click = SimpleNamespace(data={"name": "racine", "type": 1, "options": [
        {"name": "groupe", "type": 2, "options": [
            {"name": "action", "type": 1, "options": [
                {"name": "texte", "type": 3, "value": "classement"},
            ]},
        ]},
    ]})
    assert interaction_command_path(click) == ("racine", "groupe", "action")


@pytest.mark.asyncio
async def test_missing_retired_command_has_a_private_actionable_error(slash_bot):
    click = make_interaction(slash_bot, None)
    click.data = {"name": "stats", "type": 1, "options": [{"type": 1, "name": "classement"}]}
    error = app_commands.CommandNotFound("classement", ["stats"])
    await slash_bot.tree.on_error(click, error)
    assert "/ladder" in click.response.send_message.call_args.args[0]
    assert click.response.send_message.call_args.kwargs["ephemeral"]
