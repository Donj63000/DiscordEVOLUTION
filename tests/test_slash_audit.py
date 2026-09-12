"""Régressions du catalogue, des champs et du pont d'interactions, sans réseau."""

import asyncio
import importlib
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from slash_commands import SlashCommandsCog
from test_slash_commands import AUTHOR_ID, load_command_inventory, make_interaction, slash_bot
from utils.command_policy import (
    GEMINI_COMMANDS, OPENAI_COMMANDS, ai_service_enabled, unavailable_reason, unavailable_roots,
)
from utils.slash_catalog import custom_routes, format_arguments, quote_token, validate_values
from utils.slash_errors import SlashInputError
from utils.slash_help import HelpView
from utils.slash_support import SlashContext, invoke_from_slash, parse_message_reference


@pytest.fixture(autouse=True)
def offline_ai(monkeypatch):
    monkeypatch.delenv("ENABLE_AI_COMMANDS", raising=False)
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name", sorted(GEMINI_COMMANDS | OPENAI_COMMANDS))
def test_ai_entries_are_removed_even_with_an_old_key(monkeypatch, name):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-old-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-old-key")
    assert unavailable_reason(name)
    assert name in unavailable_roots()


@pytest.mark.parametrize("provider,key", [("gemini", "GOOGLE_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("openai", "OPENAI_API_KEY")])
def test_reactivation_needs_explicit_opt_in_and_correct_key(monkeypatch, provider, key):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "1")
    assert not ai_service_enabled(provider)
    monkeypatch.setenv(key, "test-key")
    assert ai_service_enabled(provider)


@pytest.mark.parametrize("value", ["0", "false", "off", "nonsense", ""])
def test_invalid_or_false_activation_never_spends_api_quota(monkeypatch, value):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", value)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert not ai_service_enabled("openai")


@pytest.mark.parametrize("name", ["calendrier", "activite creer", "organisation", "organisation-sync", "annonce-list", "annonce-cancel", "profil set"])
def test_non_ai_workflows_remain_available(name):
    assert unavailable_reason(name) is None


@pytest.mark.asyncio
async def test_actual_complete_catalog_with_scanner_dependency_isolated(slash_bot, monkeypatch):
    # On isole le validateur d'URL de calcul.py : ce test charge les déclarations,
    # pas la vérification d'URL. Le schéma est produit par la vraie bibliothèque discord.py.
    calcul_was_loaded = "calcul" in sys.modules
    monkeypatch.setitem(sys.modules, "validators", ModuleType("validators"))
    # Le parseur de langage naturel n'est pas sollicité par l'inventaire.
    if "dateparser" not in sys.modules:
        monkeypatch.setitem(sys.modules, "dateparser", ModuleType("dateparser"))
    load_command_inventory(slash_bot)
    before = {command.qualified_name for command in slash_bot.walk_commands()}
    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    remaining = {command.qualified_name for command in slash_bot.walk_commands()}
    assert before - remaining == before & unavailable_roots()
    assert cog.covered_commands == remaining
    assert slash_bot.tree.get_command("organisation") is not None
    assert slash_bot.tree.get_command("annonce-list") is not None
    assert slash_bot.tree.get_command("event-rapide") is None
    assert slash_bot.tree.get_command("calendrier").to_dict(slash_bot.tree).get("options", []) == []
    for command in slash_bot.tree.get_commands():
        schema = command.to_dict(slash_bot.tree)
        assert schema["dm_permission"] is False
        assert schema.get("integration_types") == [0]
        assert len(schema.get("options", [])) <= 25
    guide = HelpView(slash_bot, AUTHOR_ID)
    try:
        listed = {command.qualified_name for entries in guide.catalog.values() for command in entries}
        actual = {command.qualified_name for command in slash_bot.tree.walk_commands()
                  if isinstance(command, app_commands.Command)}
        assert listed == actual
        assert not any(name.split()[0] in unavailable_roots() for name in listed)
        for section in ["Démarrer", *guide.catalog]:
            guide.section = section
            for page in range(guide.pages):
                guide.page = page
                embed = guide.embed()
                assert len(embed) <= 6000
                assert len(embed.fields) <= 25
                assert all(len(field.value) <= 1024 for field in embed.fields)
    finally:
        guide.stop()
        # Ne pas laisser le module calcul lié au faux validateur pour les autres tests.
        if not calcul_was_loaded:
            sys.modules.pop("calcul", None)


@pytest.mark.asyncio
async def test_hidden_and_disabled_prefix_commands_are_not_accidentally_exposed(slash_bot):
    @slash_bot.command(hidden=True)
    async def internal(ctx):
        pass

    @slash_bot.command(enabled=False)
    async def stopped(ctx):
        pass

    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    assert slash_bot.tree.get_commands() == []
    assert set(cog.excluded_commands) == {"internal", "stopped"}


@pytest.mark.asyncio
async def test_tree_rejects_stale_ai_menu_and_private_context(slash_bot):
    interaction = make_interaction(slash_bot, SimpleNamespace(name="ia"))
    interaction.data = {"name": "ia"}
    assert not await slash_bot.tree.interaction_check(interaction)
    assert "désactivées" in interaction.response.send_message.call_args.args[0]
    interaction.data = {"name": "calendrier"}
    interaction.guild = None
    assert not await slash_bot.tree.interaction_check(interaction)


@pytest.mark.asyncio
async def test_unknown_technical_value_error_never_leaks_its_content(slash_bot):
    interaction = make_interaction(slash_bot, SimpleNamespace(name="test", qualified_name="test"))
    error = app_commands.CommandInvokeError(interaction.command, ValueError("secret-token-for-test"))
    await slash_bot.tree.on_error(interaction, error)
    assert "secret-token" not in interaction.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_expired_error_does_not_fall_back_to_a_public_channel(slash_bot):
    interaction = make_interaction(slash_bot, SimpleNamespace(name="test", qualified_name="test"))
    interaction.is_expired = lambda: True
    await slash_bot.tree.on_error(interaction, app_commands.CheckFailure())
    interaction.followup.send.assert_not_awaited()
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_private_response_is_preserved_even_outside_a_private_workflow(slash_bot, monkeypatch):
    interaction = make_interaction(slash_bot, SimpleNamespace(name="public"))
    interaction.is_expired = lambda: True
    sender = AsyncMock()
    monkeypatch.setattr(discord.Member, "send", sender)
    ctx = await SlashContext.from_interaction(interaction)
    await ctx.send("confidentiel", ephemeral=True, reference=object(), mention_author=True)
    sender.assert_awaited_once_with("confidentiel")
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_workflow_cannot_force_a_public_result(slash_bot):
    interaction = make_interaction(slash_bot, SimpleNamespace(name="ticket"))
    interaction.response.is_done.return_value = True
    ctx = await SlashContext.from_interaction(interaction)
    ctx.private_response = True
    await ctx.send("confidentiel", ephemeral=False)
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_prefix_failure_has_an_immediate_single_private_receipt(slash_bot):
    @slash_bot.command(name="locked")
    @commands.has_role("Staff")
    async def locked(ctx):
        raise AssertionError("ne doit pas être appelée")

    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    command = slash_bot.tree.get_command("locked")
    interaction = make_interaction(slash_bot, command)
    ctx = await command.callback(interaction)
    assert ctx.command_failed and ctx.slash_error_handled
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
    assert "permission" in interaction.followup.send.call_args.kwargs["content"]
    assert "permission" not in interaction.edit_original_response.call_args.kwargs["content"]


@pytest.mark.parametrize("text", ["nom\\", "chemin avec espaces\\"])
def test_unrepresentable_legacy_token_is_rejected_not_corrupted(text):
    with pytest.raises(SlashInputError):
        quote_token(text)


@pytest.mark.parametrize("reference", ["https://discord.com/channels/100/300/0", str(2**64), "９９", "1"*1000])
def test_message_reference_rejects_invalid_snowflakes(reference):
    with pytest.raises(SlashInputError):
        parse_message_reference(reference, guild_id=100, channel_id=300)


@pytest.mark.parametrize("payload", [
    {"titre": "temps=00:01:00", "choix": "Oui|Non"},
    {"titre": "Question", "choix": "Oui|oui"},
    {"titre": "Question", "choix": "A"*101 + "|Non"},
    {"titre": "Question", "choix": "Oui|Non", "duree": "99:00:00"},
    {"titre": "Question", "choix": "Oui|Non", "duree": "00:00:00"},
])
def test_poll_inputs_cannot_inject_legacy_directives(payload):
    route = next(route for route in custom_routes() if route.target == "sondage")
    with pytest.raises(SlashInputError):
        format_arguments(route, validate_values(route, payload))


def test_emoji_title_is_limited_by_utf16_before_any_side_effect():
    from utils.activity_data import ActivityError, validate_draft

    with pytest.raises(ActivityError):
        validate_draft({"titre": "😀"*43, "date": "01/01/2099 21:00"})


def test_activity_modification_opens_a_form_without_empty_fields_overwriting_the_record():
    route = next(route for route in custom_routes() if route.path == ("activite", "modifier"))
    assert [option.name for option in route.options] == ["identifiant", "duree"]
    assert validate_values(route, {"identifiant": "1"}) == {"identifiant": "1", "duree": None}


@pytest.mark.asyncio
async def test_perco_acknowledges_before_console_reads(slash_bot):
    from perco import PercoCog, PercoState
    cog = PercoCog(slash_bot)
    interaction = make_interaction(slash_bot, cog.perco_command)

    async def read(guild):
        assert interaction.response.is_done()
        return PercoState()

    cog._ensure_state = AsyncMock(side_effect=read)
    cog._build_status_embed = Mock(return_value=discord.Embed(title="Percepteurs"))
    await cog.perco_command.callback(cog, interaction)
    interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=False)
    assert interaction.followup.send.call_args.kwargs["embed"].title == "Percepteurs"


@pytest.mark.asyncio
async def test_perco_refuses_unauthorized_mutation_before_io(slash_bot):
    from perco import PercoCog
    cog = PercoCog(slash_bot)
    cog._is_staff = Mock(return_value=False)
    cog._ensure_state = AsyncMock()
    interaction = make_interaction(slash_bot, cog.perco_command)
    await cog.perco_command.callback(cog, interaction, app_commands.Choice(name="Pleins", value="full"))
    cog._ensure_state.assert_not_awaited()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_autocomplete_activities_is_chronological_and_ignores_corrupt_records(slash_bot, monkeypatch):
    records = {
        "2": {"titre": "Dernier", "date_str": "2099-03-01 20:00:00", "creator_id": 1, "participants": []},
        "1": {"titre": "Premier", "date_str": "2099-01-01 20:00:00", "creator_id": 1, "participants": []},
        "old": {"titre": "Passé", "date_str": "2000-01-01 20:00:00", "creator_id": 1, "participants": []},
        "bad": {"date_str": "pas une date"},
        "other": {"guild_id": 999, "date_str": "2099-01-01 20:00:00", "creator_id": 1, "participants": []},
    }
    monkeypatch.setattr(slash_bot, "get_cog", lambda name: SimpleNamespace(activities_data={"events": records}))
    selected = make_interaction(slash_bot, SimpleNamespace(name="info"))
    choices = await SlashCommandsCog(slash_bot).autocomplete_activities(selected, "")
    assert [choice.value for choice in choices] == ["1", "2", "old"]


@pytest.mark.asyncio
async def test_autocomplete_jobs_tolerates_a_corrupt_player_record(slash_bot, monkeypatch):
    cog = SimpleNamespace(jobs_data={"1": None, "2": {"jobs": ["invalide"]}, "3": {"jobs": {"Paysan": 100}}})
    monkeypatch.setattr(slash_bot, "get_cog", lambda name: cog)
    choices = await SlashCommandsCog(slash_bot).autocomplete_jobs(SimpleNamespace(), "paysan")
    assert any(choice.value == "Paysan" for choice in choices)


@pytest.mark.parametrize("backend,key", [
    ("gemini", "GOOGLE_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("openai", "OPENAI_API_KEY"),
    ("invalide", "GEMINI_API_KEY"),
])
def test_staff_reactivation_uses_the_configured_provider(monkeypatch, backend, key):
    monkeypatch.setenv("ENABLE_AI_COMMANDS", "1")
    monkeypatch.setenv("IASTAFF_BACKEND", backend)
    assert unavailable_reason("iastaff")
    monkeypatch.setenv(key, "test-key")
    assert ai_service_enabled("staff")
    assert unavailable_reason("iastaff") is None
