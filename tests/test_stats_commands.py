from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stats import StatsCog
from cogs.profil import ProfilCog


class DummyContext:
    """Minimal async context object used to simulate Discord invocations in tests."""

    def __init__(self, bot: commands.Bot, guild_id: int = 0, author_id: int = 0):
        self.bot = bot
        self.guild = SimpleNamespace(id=guild_id)
        self.author = SimpleNamespace(id=author_id)
        self.message = SimpleNamespace(mentions=[])
        self.sent_messages: list[str] = []
        self.replies: list[dict] = []
        self.invocations: list[tuple[commands.Command, tuple, dict]] = []

    async def send(self, content: str):
        self.sent_messages.append(content)

    async def reply(self, content: str | None = None, *, embed=None, file=None):
        self.replies.append({"content": content, "embed": embed, "file": file})

    async def invoke(self, command: commands.Command, *args, **kwargs):
        self.invocations.append((command, args, kwargs))
        if command.cog is not None:
            return await command.callback(command.cog, self, *args, **kwargs)
        return await command.callback(self, *args, **kwargs)


@pytest.mark.asyncio
async def test_stats_ladder_proxies_existing_command():
    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)
    stats_cog = StatsCog(bot)
    stats_cog.save_loop.cancel()

    calls: list[str] = []

    @commands.command(name="ladder")
    async def dummy_ladder(ctx: DummyContext, *, arg: str = ""):
        calls.append(arg)

    bot.add_command(dummy_ladder)

    ctx = DummyContext(bot)
    try:
        await StatsCog.stats_ladder.callback(stats_cog, ctx, arg="class iop")

        assert calls == ["class iop"]
        assert ctx.sent_messages == []
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_stats_ladder_missing_command_sends_hint():
    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)
    stats_cog = StatsCog(bot)
    stats_cog.save_loop.cancel()

    ctx = DummyContext(bot)
    try:
        await StatsCog.stats_ladder.callback(stats_cog, ctx, arg="")

        assert ctx.sent_messages == [
            "La commande `!ladder` est actuellement indisponible. Réessaie plus tard."
        ]
    finally:
        await bot.close()


def _make_profile(
    guild_id: int,
    owner_id: int,
    slug: str,
    name: str,
    level: int,
    classe: str,
    force: int,
    intelligence: int,
    chance: int,
    agilite: int,
    vitalite: int,
    sagesse: int,
    initiative: int,
    pa: int,
    pm: int,
):
    def _stat(total: int):
        return {"base": total, "bonus": 0}

    return {
        "guild_id": guild_id,
        "owner_id": owner_id,
        "player_name": name,
        "player_slug": slug,
        "level": level,
        "classe": classe,
        "alignement": "Bonta",
        "stats": {
            "force": _stat(force),
            "intelligence": _stat(intelligence),
            "chance": _stat(chance),
            "agilite": _stat(agilite),
            "vitalite": _stat(vitalite),
            "sagesse": _stat(sagesse),
        },
        "initiative": initiative,
        "pa": pa,
        "pm": pm,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_ladder_command_builds_embed(monkeypatch):
    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)
    profil_cog = ProfilCog(bot)

    guild_id = 123
    profiles = [
        _make_profile(guild_id, 10, "alpha", "Alpha", 200, "Iop", 600, 200, 150, 180, 3000, 400, 1800, 11, 6),
        _make_profile(guild_id, 11, "bravo", "Bravo", 190, "Cra", 400, 250, 220, 160, 2500, 350, 1500, 10, 5),
    ]

    class DummyStore:
        async def _load_blob(self, gid: int):
            return {"profiles": [p for p in profiles if p["guild_id"] == gid]}

    profil_cog.store = DummyStore()

    saved_snapshot: dict = {}

    class DummySnapshotStore:
        def __init__(self, _bot):
            pass

        async def load(self, _guild_id: int):
            saved_snapshot["loaded"] = True
            return {"ranking": [{"slug": "bravo"}]}

        async def save(self, gid: int, ranking: list[dict]):
            saved_snapshot["saved"] = True
            saved_snapshot["guild_id"] = gid
            saved_snapshot["ranking"] = ranking

    monkeypatch.setattr("cogs.profil.LadderSnapshotStore", DummySnapshotStore)

    ctx = DummyContext(bot, guild_id=guild_id, author_id=10)
    try:
        await ProfilCog.ladder.callback(profil_cog, ctx, arg="")

        assert saved_snapshot["loaded"] is True
        assert saved_snapshot["saved"] is True
        assert saved_snapshot["guild_id"] == guild_id
        assert len(saved_snapshot["ranking"]) == 2

        assert len(ctx.replies) == 1
        embed = ctx.replies[0]["embed"]
        assert isinstance(embed, discord.Embed)
        assert embed.title == "Ladder — Classement guilde"
        assert embed.fields
        first_field = embed.fields[0]
        assert "Alpha" in first_field.value
        assert "Bravo" in first_field.value
    finally:
        await bot.close()
