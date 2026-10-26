"""Player ownership and persistence regressions, with synthetic records only."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import players
from utils.guild_scope import GuildScopeError


@pytest.fixture
def player_cog(monkeypatch):
    monkeypatch.setenv("BOT_GUILD_ID", "1")
    monkeypatch.delenv("EVO_GUILD_ID", raising=False)
    guild = SimpleNamespace(id=1)
    bot = SimpleNamespace(guilds=[guild], user=SimpleNamespace(id=900))
    cog = players.PlayersCog(bot)
    cog.initialized = True
    cog.persos_data = {
        "11": {"discord_name": "Victime", "main": "Personnage", "mules": ["Mule"]},
        "recrue_personnage": {"discord_name": "Personnage", "main": "", "mules": ["Ancienne"]},
    }
    cog.dump_data_to_console = AsyncMock()
    return cog


def context(user_id, name, guild_id=1):
    return SimpleNamespace(guild=SimpleNamespace(id=guild_id),
                           author=SimpleNamespace(id=user_id, display_name=name), send=AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Personnage", "Perso", "Victime", "personnage!"])
async def test_declaring_other_character_never_moves_or_claims_existing_record(player_cog, name):
    original = deepcopy(player_cog.persos_data)
    ctx = context(22, name)
    await players.PlayersCog.membre_principal.callback(player_cog, ctx, nom_perso=name)
    assert player_cog.persos_data["11"] == original["11"]
    assert player_cog.persos_data["recrue_personnage"] == original["recrue_personnage"]
    assert player_cog.persos_data["22"]["mules"] == []
    assert player_cog.persos_data["22"]["main"] == name


@pytest.mark.asyncio
async def test_reading_own_profile_never_changes_ownership(player_cog):
    before = deepcopy(player_cog.persos_data)
    ctx = context(22, "Victime")
    await players.PlayersCog.membre_moi.callback(player_cog, ctx)
    assert player_cog.persos_data == before
    player_cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
async def test_registration_cannot_claim_placeholder_or_other_user(player_cog):
    before = deepcopy(player_cog.persos_data)
    await player_cog.auto_register_member(22, "Victime", "Personnage", guild_id=1)
    assert player_cog.persos_data["11"] == before["11"]
    assert player_cog.persos_data["recrue_personnage"] == before["recrue_personnage"]
    assert player_cog.persos_data["22"]["mules"] == []


@pytest.mark.asyncio
async def test_foreign_command_departure_and_service_do_not_touch_data(player_cog):
    before = deepcopy(player_cog.persos_data)
    ctx = context(11, "Victime", 2)
    with pytest.raises(GuildScopeError):
        await players.PlayersCog.membre_liste.callback(player_cog, ctx)
    ctx.send.assert_not_awaited()
    await player_cog.on_member_remove(SimpleNamespace(id=11, guild=ctx.guild))
    with pytest.raises(GuildScopeError):
        player_cog.get_member_record(2, 11)
    with pytest.raises(GuildScopeError):
        player_cog.set_main(2, 11, "Victime", "Changement")
    assert player_cog.persos_data == before
    player_cog.dump_data_to_console.assert_not_awaited()


def test_reader_returns_copies_and_ambiguous_lookup_does_not_mutate(player_cog):
    record = player_cog.get_member_record(1, 11)
    record["mules"].append("Non enregistrée")
    records = player_cog.list_member_records(1)
    records.clear()
    assert player_cog.persos_data["11"]["mules"] == ["Mule"]
    player_cog.persos_data["33"] = {"discord_name": "Victime", "main": "Autre", "mules": []}
    before = deepcopy(player_cog.persos_data)
    with pytest.raises(ValueError, match="Plusieurs"):
        player_cog.resolve_member_record(1, "Victime")
    assert player_cog.persos_data == before


@pytest.mark.asyncio
async def test_empty_snapshot_is_authoritative_and_foreign_channel_is_not_read(player_cog, monkeypatch):
    current = SimpleNamespace(id=2, author=player_cog.bot.user, webhook_id=None,
                              content=players.PLAYERS_MARKER + "\n```json\n{}\n```",
                              attachments=[], created_at=datetime.now(timezone.utc))
    old = SimpleNamespace(id=1, author=player_cog.bot.user, webhook_id=None,
                          content=players.PLAYERS_MARKER + '\n```json\n{"11":{"main":"Ancien"}}\n```',
                          attachments=[], created_at=current.created_at)
    channel = SimpleNamespace(guild=SimpleNamespace(id=1), pins=AsyncMock(return_value=[]))
    monkeypatch.setattr(players, "fetch_channel_history", AsyncMock(return_value=[old, current]))
    assert await player_cog._load_data_from_console(channel)
    assert player_cog.persos_data == {}
    foreign = SimpleNamespace(guild=SimpleNamespace(id=2), pins=AsyncMock())
    with pytest.raises(GuildScopeError):
        await player_cog._load_data_from_console(foreign)
    foreign.pins.assert_not_awaited()
