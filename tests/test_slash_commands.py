import asyncio
import importlib
import inspect
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from aioresponses import aioresponses
from discord import app_commands
from discord.ext import commands
import pytest
import pytest_asyncio

from slash_commands import SlashCommandsCog, generic_arguments, generic_route
from utils.slash_catalog import custom_routes, format_arguments, quote_token
from utils.slash_support import (
    EvolutionCommandTree, SlashContext, delete_invocation_message, invoke_from_slash,
    notify_private_workflow, parse_message_reference,
)

EVALUATE_ANNOTATION = discord.utils.evaluate_annotation
IS_INSIDE_CLASS = discord.utils.is_inside_class
AUTHOR_ID = 111000000000000001


@pytest_asyncio.fixture
async def slash_bot(monkeypatch):
    monkeypatch.setattr(discord.utils, "evaluate_annotation", EVALUATE_ANNOTATION)
    monkeypatch.setattr(discord.utils, "is_inside_class", IS_INSIDE_CLASS)
    async with commands.Bot(
        command_prefix="!", intents=discord.Intents.none(), help_command=None,
        tree_cls=EvolutionCommandTree,
    ) as bot:
        bot._connection.user = discord.ClientUser(
            state=bot._connection,
            data={"id": "777", "username": "Evolution BOT", "discriminator": "0", "avatar": None},
        )
        yield bot


def make_interaction(bot, command, *, roles=(), permission_value=0):
    guild = discord.Guild(state=bot._connection, data={
        "id": "100", "name": "Test", "owner_id": "999", "roles": [
            {"id": "100", "name": "@everyone", "permissions": "0"},
            *[{"id": str(index), "name": name, "permissions": "0"} for index, name in enumerate(roles, 200)],
        ],
    })
    channel = discord.TextChannel(state=bot._connection, guild=guild, data={
        "id": "300", "name": "test", "type": 0, "position": 0, "permission_overwrites": [],
    })
    guild._channels[channel.id] = channel
    author = discord.Member(state=bot._connection, guild=guild, data={
        "user": {"id": str(AUTHOR_ID), "username": "Hero", "discriminator": "0", "avatar": None},
        "flags": 0,
        "roles": [str(index) for index, _ in enumerate(roles, 200)],
        "joined_at": "2025-01-01T00:00:00+00:00",
    })
    guild._members[author.id] = author
    response = SimpleNamespace(is_done=Mock(return_value=False), send_message=AsyncMock())

    async def defer(**kwargs):
        response.is_done.return_value = True

    response.defer = AsyncMock(side_effect=defer)
    return SimpleNamespace(
        client=bot, command=command, message=None, _state=bot._connection,
        id=discord.utils.time_snowflake(discord.utils.utcnow()), data={"type": 1},
        channel=channel, channel_id=channel.id, guild=guild, guild_id=guild.id,
        user=author, namespace=[], command_failed=False, response=response,
        followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=900))),
        is_expired=lambda: False, permissions=discord.Permissions(permission_value),
        app_permissions=discord.Permissions.all(),
    )


def load_command_inventory(bot):
    modules = (
        "job", "activite", "ticket", "players", "sondage", "stats", "help", "welcome",
        "member_guard", "calcul", "dofus_wiki", "perco", "avis", "organisation", "event_conversation",
        "ia", "music", "defender", "moderation", "up", "entree", "slash_events",
        "cogs.profil", "cogs.annonce_ai", "iastaff",
    )
    for module_name in modules:
        module = importlib.import_module(module_name)
        for cls in vars(module).values():
            if not inspect.isclass(cls) or cls.__module__ != module_name or not issubclass(cls, commands.Cog):
                continue
            for source in cls.__cog_commands__:
                if source.parent is None:
                    bot.remove_command(source.name)
                    bot.add_command(source.copy())
            for source in cls.__cog_app_commands__:
                bot.tree.add_command(source, override=True)

    @bot.command(name="ping")
    async def ping(ctx):
        await ctx.send("Pong!")


@pytest.mark.asyncio
async def test_slash_catalog_covers_every_installed_command_with_valid_discord_schema(slash_bot):
    load_command_inventory(slash_bot)
    existing = {command.name: command for command in slash_bot.tree.get_commands()}
    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()

    assert cog.covered_commands == {command.qualified_name for command in slash_bot.walk_commands()}
    assert len(cog.covered_commands) >= 60
    assert len(slash_bot.tree.get_commands()) <= 100
    assert slash_bot.tree.get_command("event") is not existing["event-rapide"]
    for name, command in existing.items():
        assert slash_bot.tree.get_command(name) is command

    def validate(schema):
        assert re.fullmatch(r"[a-z0-9_-]{1,32}", schema["name"])
        assert 1 <= len(schema["description"]) <= 100
        assert len(schema.get("options", [])) <= 25
        optional_seen = False
        for option in schema.get("options", []):
            validate(option)
            if option["type"] not in (1, 2):
                if not option.get("required", False):
                    optional_seen = True
                else:
                    assert not optional_seen

    for command in slash_bot.tree.get_commands():
        validate(command.to_dict(slash_bot.tree))
    add_job = slash_bot.tree.get_command("job").get_command("ajouter")
    options = {option["name"]: option for option in add_job.to_dict(slash_bot.tree)["options"]}
    assert options["niveau"]["min_value"] == 1
    assert options["niveau"]["max_value"] == 100
    assert options["metier"]["autocomplete"] is True
    cog.cog_unload()
    assert {command.name for command in slash_bot.tree.get_commands()} == set(existing)


@pytest.mark.asyncio
async def test_slash_job_route_uses_existing_handler_and_preserves_quotes(slash_bot):
    calls = []

    @slash_bot.command(name="job")
    async def job(ctx, *args):
        calls.append(args)
        await ctx.send("Métier enregistré.")

    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    command = slash_bot.tree.get_command("job").get_command("ajouter")
    interaction = make_interaction(slash_bot, command)
    await command.callback(interaction, metier='Forgeur d\'armes "rare"', niveau=100)

    assert calls == [("add", 'Forgeur d\'armes "rare"', "100")]
    interaction.response.defer.assert_awaited_once_with(thinking=True)
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.call_args.kwargs["content"] == "Métier enregistré."


@pytest.mark.asyncio
@pytest.mark.parametrize("name,values,expected", [
    ("objet", {"nom": "item:1"}, "Objet · Gelano"),
    ("recette", {"objet": "item:1", "quantite": 3}, "Recette · Gelano"),
    ("equipement", {"type": "Anneau", "niveau": 100}, "Anneau · Niveau 1 à 100"),
    ("monstre", {"nom": "monster:craqueleur"}, "Monstre · Craqueleur"),
])
async def test_wiki_slash_commands_defer_and_reach_the_real_handlers(slash_bot, name, values, expected):
    from dofus_wiki import DofusWikiCog
    from utils.dofus_wiki import WIKI_ORIGIN, WikiDetail, parse_entries

    items = parse_entries([{"id": 1, "name": "Gelano", "type": "Anneau", "level": 60,
                            "url": WIKI_ORIGIN + "/items/gelano/"}], "item")
    monsters = parse_entries([{"id": 1, "name": "Craqueleur", "level_min": "25 à 37",
                               "url": WIKI_ORIGIN + "/monstres/craqueleur/"}], "monster")

    def detail(entry):
        return WikiDetail(entry, {
            "name": entry.name, "level": 60, "stats": ["+1 PA"], "grades": [],
            "recipe": [{"item_id": 10, "name": "Gelée", "qty": 100}],
        }, None, False)

    client = SimpleNamespace(
        items=AsyncMock(return_value=items), monsters=AsyncMock(return_value=monsters),
        detail=AsyncMock(side_effect=detail), warmup=AsyncMock(), close=AsyncMock(),
        peek=Mock(return_value=items), is_stale=Mock(return_value=False),
    )
    wiki = DofusWikiCog(slash_bot, client=client)
    await slash_bot.add_cog(wiki)
    catalog = SlashCommandsCog(slash_bot)
    catalog.register_commands()
    command = slash_bot.tree.get_command(name)
    schema = command.to_dict(slash_bot.tree)
    assert schema["options"][0]["autocomplete"] is True
    if name == "recette":
        assert schema["options"][1]["min_value"] == 1
        assert schema["options"][1]["max_value"] == 10000
    selected = make_interaction(slash_bot, command)
    await command.callback(selected, **values)
    selected.response.defer.assert_awaited_once_with(thinking=True)
    assert selected.followup.send.call_args.kwargs["embed"].title == expected
    if name == "recette":
        assert "300 ×" in selected.followup.send.call_args.kwargs["embed"].fields[0].value
    await slash_bot.remove_cog("DofusWikiCog")
    assert not wiki.views
    client.close.assert_awaited_once()


@pytest_asyncio.fixture
async def network_wiki_bot(slash_bot):
    from dofus_wiki import DofusWikiCog
    from utils.dofus_wiki import DofusWikiClient, WIKI_ORIGIN

    items = [
        {"id": 1, "name": "Gelano", "type": "Anneau", "level": 60,
         "url": WIKI_ORIGIN + "/items/gelano/"},
        {"id": 2, "name": "Sac du Bouftou", "type": "Sac à dos", "level": 50,
         "url": WIKI_ORIGIN + "/items/sac-du-bouftou/"},
        {"id": 3, "name": "Sac du Bouftou Royal", "type": "Sac à dos", "level": 120,
         "url": WIKI_ORIGIN + "/items/sac-du-bouftou-royal/"},
    ]
    monster_url = WIKI_ORIGIN + "/monstres/craqueleur/"
    with aioresponses() as http:
        http.get(WIKI_ORIGIN + "/api/items.json", payload=items)
        http.get(WIKI_ORIGIN + "/api/monsters.json", payload=[{
            "id": 999, "name": "Craqueleur", "level_min": "25 à 37", "url": monster_url,
        }])
        http.get(WIKI_ORIGIN + "/api/search-index.json", payload=[{
            "u": "/items/gelano/", "i": "/icons/gelano.png",
        }])
        http.get(WIKI_ORIGIN + "/api/item/1.json", payload={
            **items[0], "weight": 5, "stats": ["+1 PA"],
            "recipe": [{"item_id": 10, "name": "Gelée Bleutée", "qty": 100}],
        })
        http.get(monster_url, content_type="text/html", body=(
            '<a href="/api/monster/37.json">JSON</a>'
            '<meta property="og:image" content="/icons/craqueleur.png">'
        ))
        http.get(WIKI_ORIGIN + "/api/monster/37.json", payload={
            "id": 37, "name": "Craqueleur", "url": monster_url,
            "grades": [{"level": 25, "hp": 150, "ap": 5, "mp": 3,
                        "resist": {"neutral": 0, "earth": 25, "fire": -50,
                                   "water": 6, "air": -12}}],
        })
        wiki = DofusWikiCog(slash_bot, client=DofusWikiClient())
        await slash_bot.add_cog(wiki)
        await wiki._warmup
        catalog = SlashCommandsCog(slash_bot)
        catalog.register_commands()
        errors = []

        async def record_error(cog, ctx, error):
            errors.append(error)

        for name in ("objet", "recette", "equipement", "monstre"):
            command = slash_bot.get_command(name)
            command.error(record_error)
            command._buckets = command._buckets.copy()
            command._buckets._cache = {}
        yield slash_bot, wiki, errors
        catalog.cog_unload()
        await slash_bot.remove_cog("DofusWikiCog")


async def invoke_wiki_prefix(bot, content):
    interaction = make_interaction(bot, bot.tree.get_command(content.split()[0][1:]))
    message = SimpleNamespace(
        id=discord.utils.time_snowflake(discord.utils.utcnow()),
        author=interaction.user, guild=interaction.guild, channel=interaction.channel,
        content=content, _state=bot._connection, attachments=[], mentions=[],
        created_at=discord.utils.utcnow(), edited_at=None,
    )
    ctx = await bot.get_context(message)
    await bot.invoke(ctx)
    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["slash", "prefix"])
@pytest.mark.parametrize("name,values,text,title", [
    ("objet", {"nom": "GELANO"}, "!objet GELANO", "Objet · Gelano"),
    ("recette", {"objet": "Gelano", "quantite": 3}, "!recette 3 Gelano", "Recette · Gelano"),
    ("equipement", {"type": "Sac à dos", "niveau": 100, "niveau_min": 40, "nom": "bouftou"},
     '!equipement "Sac à dos" 100 40 bouftou', "Sac à dos · Niveau 40 à 100"),
    ("monstre", {"nom": "Craqueleur"}, "!monstre Craqueleur", "Monstre · Craqueleur"),
])
async def test_wiki_full_route_from_http_to_discord_embed(
    network_wiki_bot, monkeypatch, mode, name, values, text, title,
):
    bot, wiki, errors = network_wiki_bot
    sent = AsyncMock(return_value=SimpleNamespace(id=900, edit=AsyncMock()))
    monkeypatch.setattr(discord.abc.Messageable, "send", sent)
    if mode == "slash":
        command = bot.tree.get_command(name)
        interaction = make_interaction(bot, command)
        interaction.followup.send = sent
        await command.callback(interaction, **values)
        interaction.response.defer.assert_awaited_once_with(thinking=True)
    else:
        await invoke_wiki_prefix(bot, text)
    assert errors == []
    assert sent.await_count == 1
    payload = sent.call_args.kwargs
    assert payload["embed"].title == title
    assert len(payload["embed"]) <= 6000
    assert payload["allowed_mentions"].everyone is False
    if name == "recette":
        assert "300 ×" in payload["embed"].fields[0].value
    elif name == "equipement":
        assert [entry.identifier for entry in payload["view"].entries] == ["2"]
    elif name == "monstre":
        assert any(field.value == "Feu (-50 %)" for field in payload["embed"].fields)
    else:
        assert payload["embed"].thumbnail.url.endswith("/icons/gelano.png")
    assert len(wiki.views) == 1


@pytest.mark.asyncio
async def test_wiki_cooldown_is_shared_between_slash_and_prefix(network_wiki_bot, monkeypatch):
    bot, wiki, errors = network_wiki_bot
    sent = AsyncMock(return_value=SimpleNamespace(id=900, edit=AsyncMock()))
    monkeypatch.setattr(discord.abc.Messageable, "send", sent)
    for _ in range(2):
        await invoke_wiki_prefix(bot, "!objet Gelano")
    command = bot.tree.get_command("objet")
    for _ in range(3):
        interaction = make_interaction(bot, command)
        interaction.followup.send = sent
        await command.callback(interaction, nom="Gelano")
    assert len(errors) == 1
    assert isinstance(errors[0], commands.CommandOnCooldown)
    assert sent.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["slash", "prefix"])
async def test_wiki_equipment_rejects_inverted_level_range(network_wiki_bot, monkeypatch, mode):
    bot, wiki, errors = network_wiki_bot
    sent = AsyncMock(return_value=SimpleNamespace(id=900, edit=AsyncMock()))
    monkeypatch.setattr(discord.abc.Messageable, "send", sent)
    if mode == "slash":
        command = bot.tree.get_command("equipement")
        interaction = make_interaction(bot, command)
        interaction.followup.send = sent
        await command.callback(interaction, type="Sac à dos", niveau=40, niveau_min=100)
    else:
        await invoke_wiki_prefix(bot, '!equipement "Sac à dos" 40 100')
    assert errors == []
    assert not isinstance(sent.call_args.kwargs.get("embed"), discord.Embed)
    content = sent.call_args.kwargs.get("content") or sent.call_args.args[0]
    assert "minimum" in content.lower()
    assert not wiki.views


@pytest.mark.asyncio
async def test_slash_group_fallback_cannot_accidentally_invoke_a_subcommand(slash_bot):
    calls = []

    @slash_bot.group(name="profil", invoke_without_command=True)
    async def profil(ctx, *, maybe_name: str = None):
        calls.append(("voir", maybe_name))
        await ctx.send("Profil.")

    @profil.command(name="delete")
    async def delete(ctx):
        calls.append(("delete", None))

    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    command = slash_bot.tree.get_command("profil").get_command("voir")
    await command.callback(make_interaction(slash_bot, command), joueur="delete")
    assert calls == [("voir", "delete")]


@pytest.mark.asyncio
async def test_slash_preserves_parent_checks_global_checks_and_cooldowns(slash_bot):
    calls = []
    errors = []

    @slash_bot.group(name="secure", invoke_without_command=True)
    @commands.has_role("Staff")
    async def secure(ctx):
        pass

    @secure.command(name="action")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def action(ctx):
        calls.append(ctx.author.id)
        await ctx.send("OK")

    @action.error
    async def error_handler(ctx, error):
        errors.append(error)
        await ctx.send("Refusé.")

    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    command = slash_bot.tree.get_command("secure").get_command("action")
    await command.callback(make_interaction(slash_bot, command))
    assert isinstance(errors[-1], commands.MissingRole)
    assert calls == []
    await command.callback(make_interaction(slash_bot, command, roles=("Staff",)))
    await command.callback(make_interaction(slash_bot, command, roles=("Staff",)))
    assert calls == [AUTHOR_ID]
    assert isinstance(errors[-1], commands.CommandOnCooldown)

    async def deny(ctx):
        return False

    slash_bot.add_check(deny, call_once=True)
    await command.callback(make_interaction(slash_bot, command, roles=("Staff",)))
    assert isinstance(errors[-1], commands.CheckFailure)
    assert calls == [AUTHOR_ID]


@pytest.mark.asyncio
async def test_slash_typed_members_run_converters_and_hooks(slash_bot):
    calls = []

    @slash_bot.command(name="warnings")
    async def warnings(ctx, member: discord.Member):
        calls.append(member)

    @warnings.before_invoke
    async def before(ctx):
        calls.append("before")

    @warnings.after_invoke
    async def after(ctx):
        calls.append("after")

    cog = SlashCommandsCog(slash_bot)
    cog.register_commands()
    command = slash_bot.tree.get_command("warnings")
    interaction = make_interaction(slash_bot, command)
    await command.callback(interaction, membre=interaction.user)
    assert calls == ["before", interaction.user, "after"]
    assert interaction.followup.send.call_args.kwargs["content"] == "Commande terminée."


@pytest.mark.parametrize("value", ["42", "https://discord.com/channels/100/300/42"])
def test_profile_import_accepts_message_from_current_channel(value):
    reference = parse_message_reference(value, guild_id=100, channel_id=300)
    assert reference.message_id == 42


@pytest.mark.parametrize("value", ["https://discord.com/channels/100/301/42", "https://discord.com/channels/101/300/42", "https://example.com/42", "0"])
def test_profile_import_rejects_other_channels_and_invalid_links(value):
    with pytest.raises(ValueError):
        parse_message_reference(value, guild_id=100, channel_id=300)


@pytest.mark.asyncio
async def test_delete_invocation_skips_slash_and_preserves_prefix():
    ctx = SimpleNamespace(interaction=object(), command="ticket", message=SimpleNamespace(delete=AsyncMock()))
    await delete_invocation_message(ctx, delay=0)
    ctx.message.delete.assert_not_awaited()
    ctx.interaction = None
    await delete_invocation_message(ctx, delay=0)
    ctx.message.delete.assert_awaited_once_with(delay=0)


@pytest.mark.parametrize("text", ["forgeur d'armes", 'Un "nom"', "C:\\test", "", "nom avec espaces"])
def test_token_encoding_roundtrips_through_discord_parser(text):
    from discord.ext.commands.view import StringView

    assert StringView(quote_token(text)).get_quoted_word() == text


def test_poll_and_activity_fields_are_translated_to_existing_syntax():
    routes = {route.path: route for route in custom_routes()}
    assert format_arguments(routes[("sondage",)], {
        "titre": "Sortie ?", "choix": "Oui | Non", "duree": "00:02:00",
    }) == "Sortie ? ; Oui ; Non ; temps=00:02:00"
    assert format_arguments(routes[("activite", "creer")], {
        "titre": "Donjon guilde", "date": "25/09/2026 21:00", "description": "Venez nombreux",
    }) == "creer Donjon guilde 25/09/2026 21:00 Venez nombreux"


@pytest.mark.parametrize("values", [
    {"titre": "Question ; autre", "choix": "Oui|Non"},
    {"titre": "Question", "choix": "Oui|"},
    {"titre": "Question", "choix": "Oui|temps=00:01:00"},
    {"titre": "Question", "choix": "Oui|Non", "duree": "00:25:00"},
])
def test_poll_fields_reject_ambiguous_legacy_syntax(values):
    route = next(route for route in custom_routes() if route.target == "sondage")
    with pytest.raises(ValueError):
        format_arguments(route, values)


@pytest.mark.asyncio
async def test_slash_job_correction_replaces_prefix_confirmation_and_persists(slash_bot):
    from job import JobCog

    job = JobCog(slash_bot)
    job.cog_load = AsyncMock()
    job.initialized = True
    job.load_from_console = AsyncMock(return_value=True)
    job.save_data_local = Mock()
    job.dump_data_to_console = AsyncMock(return_value=True)

    async def send_embed(ctx, embed):
        await ctx.send(embed=embed)

    job.send_logo_embed = send_embed
    await slash_bot.add_cog(job)
    catalog = SlashCommandsCog(slash_bot)
    catalog.register_commands()
    command = slash_bot.tree.get_command("job").get_command("ajouter")
    interaction = make_interaction(slash_bot, command)
    prefix_ctx = SimpleNamespace(
        guild=interaction.guild, channel=interaction.channel, author=interaction.user, send=AsyncMock()
    )
    waiting = asyncio.Event()
    reply = asyncio.get_running_loop().create_future()

    def wait_for(*args, **kwargs):
        waiting.set()
        return reply

    slash_bot.wait_for = wait_for
    previous = asyncio.create_task(job.job_command.callback(job, prefix_ctx, "forgeur de dague", "100"))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=2)
        await command.callback(interaction, metier="forgeur d'armes", niveau=100)
        await asyncio.wait_for(previous, timeout=2)
    finally:
        if not previous.done():
            previous.cancel()
        await asyncio.gather(previous, return_exceptions=True)

    assert reply.cancelled()
    assert prefix_ctx.send.await_count == 1
    assert job.jobs_data[str(AUTHOR_ID)]["jobs"] == {"Forgeur d’armes": 100}
    job.dump_data_to_console.assert_awaited_once_with(interaction.guild)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert embed.title == "Mise à jour du métier"


@pytest.mark.asyncio
async def test_slash_profile_import_passes_reference_to_existing_handler(slash_bot):
    references = []

    @slash_bot.group(name="profil", invoke_without_command=True)
    async def profil(ctx):
        pass

    @profil.command(name="import")
    async def import_profile(ctx):
        references.append(ctx.message.reference.message_id)

    catalog = SlashCommandsCog(slash_bot)
    catalog.register_commands()
    command = slash_bot.tree.get_command("profil").get_command("importer")
    await command.callback(make_interaction(slash_bot, command), message="42")
    assert references == [42]


@pytest.mark.asyncio
async def test_help_embeds_fit_discord_limits(slash_bot):
    from help import HelpCog

    cog = HelpCog(slash_bot)
    ctx = SimpleNamespace(send=AsyncMock())
    await cog.aide_command.callback(cog, ctx)
    for call in ctx.send.call_args_list:
        embed = call.kwargs["embed"]
        assert len(embed) <= 6000
        assert len(embed.fields) <= 25
        assert all(len(field.value) <= 1024 for field in embed.fields)


@pytest.mark.asyncio
async def test_snowflake_option_is_a_string_to_preserve_full_precision(slash_bot):
    @slash_bot.command(name="close_sondage")
    async def close_poll(ctx, message_id: int = None):
        pass

    catalog = SlashCommandsCog(slash_bot)
    catalog.register_commands()
    command = slash_bot.tree.get_command("close_sondage")
    option = command.to_dict(slash_bot.tree)["options"][0]
    assert option["type"] == discord.AppCommandOptionType.string.value
    assert option["required"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("guild_id", [None, "12345"])
async def test_startup_syncs_slash_commands_by_default(monkeypatch, guild_id):
    import main

    monkeypatch.delenv("SYNC_SLASH_COMMANDS", raising=False)
    if guild_id is None:
        monkeypatch.delenv("SYNC_SLASH_GUILD_ID", raising=False)
    else:
        monkeypatch.setenv("SYNC_SLASH_GUILD_ID", guild_id)
    tree = SimpleNamespace(copy_global_to=Mock(), sync=AsyncMock(return_value=[]))
    await main.EvoBot._sync_app_commands(SimpleNamespace(tree=tree))
    if guild_id is None:
        tree.copy_global_to.assert_not_called()
        tree.sync.assert_awaited_once_with()
    else:
        assert tree.sync.call_args.kwargs["guild"].id == int(guild_id)
        tree.copy_global_to.assert_called_once()


@pytest.mark.asyncio
async def test_startup_can_disable_sync_and_loads_catalog_last(monkeypatch):
    import main

    monkeypatch.setenv("SYNC_SLASH_COMMANDS", "0")
    tree = SimpleNamespace(sync=AsyncMock())
    await main.EvoBot._sync_app_commands(SimpleNamespace(tree=tree))
    tree.sync.assert_not_awaited()
    loaded = []

    async def safe_load(name):
        loaded.append(name)
        return True

    async def load_iastaff():
        loaded.append("iastaff")

    bot = SimpleNamespace(
        remove_command=Mock(), _safe_load=safe_load, _load_iastaff_anywhere=load_iastaff,
        _sync_app_commands=AsyncMock(), commands=[],
    )
    await main.EvoBot.setup_hook(bot)
    assert loaded[-2:] == ["iastaff", "slash_commands"]
    bot._sync_app_commands.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_tree_returns_a_clear_message_for_invalid_fields(slash_bot):
    interaction = make_interaction(slash_bot, SimpleNamespace(name="sondage", qualified_name="sondage"))
    error = app_commands.CommandInvokeError(interaction.command, ValueError("Durée invalide."))
    await slash_bot.tree.on_error(interaction, error)
    message = interaction.response.send_message.call_args.args[0]
    assert "Durée invalide" in message
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_private_workflow_acknowledges_before_waiting_for_dm(slash_bot):
    opened = asyncio.Event()
    finish = asyncio.Event()

    @slash_bot.command(name="ticket")
    async def ticket(ctx):
        await notify_private_workflow(ctx)
        opened.set()
        await finish.wait()

    catalog = SlashCommandsCog(slash_bot)
    catalog.register_commands()
    command = slash_bot.tree.get_command("ticket")
    interaction = make_interaction(slash_bot, command)
    running = asyncio.create_task(command.callback(interaction))
    try:
        await asyncio.wait_for(opened.wait(), timeout=2)
        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        assert not running.done()
        sent = interaction.followup.send.call_args.kwargs
        assert "messages privés" in sent["content"]
        assert sent["ephemeral"] is True
    finally:
        finish.set()
        await asyncio.wait_for(running, timeout=2)
    assert interaction.followup.send.await_count == 1


@pytest.mark.asyncio
async def test_expired_private_interaction_finishes_in_dm_instead_of_public_channel(slash_bot, monkeypatch):
    interaction = make_interaction(slash_bot, SimpleNamespace(name="ticket"))
    interaction.is_expired = lambda: True
    send_dm = AsyncMock(return_value=SimpleNamespace(id=900))
    monkeypatch.setattr(discord.Member, "send", send_dm)
    ctx = await SlashContext.from_interaction(interaction)
    ctx.private_response = True

    await ctx.send("Résultat privé.")

    send_dm.assert_awaited_once_with("Résultat privé.")
    interaction.followup.send.assert_not_awaited()
    assert ctx.response_count == 1


@pytest.mark.asyncio
async def test_autocomplete_jobs_matches_accents_and_limits_choices(slash_bot, monkeypatch):
    cog = SimpleNamespace(jobs_data={"1": {"jobs": {f"Métier spécial {i}": 100 for i in range(40)}}})
    monkeypatch.setattr(slash_bot, "get_cog", lambda name: cog)
    catalog = SlashCommandsCog(slash_bot)

    choices = await catalog.autocomplete_jobs(SimpleNamespace(), "metier special")

    assert len(choices) == 25
    assert all(choice.value.startswith("Métier spécial") for choice in choices)


@pytest.mark.asyncio
async def test_autocomplete_activities_excludes_cancelled_events(slash_bot, monkeypatch):
    cog = SimpleNamespace(activities_data={"events": {
        "1": {"titre": "Donjon guilde", "cancelled": False},
        "2": {"titre": "Donjon annulé", "cancelled": True},
    }})
    monkeypatch.setattr(slash_bot, "get_cog", lambda name: cog)
    choices = await SlashCommandsCog(slash_bot).autocomplete_activities(SimpleNamespace(), "donjon")
    assert [(choice.name, choice.value) for choice in choices] == [("1 — Donjon guilde", "1")]
