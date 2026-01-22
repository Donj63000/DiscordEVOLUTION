import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import discord

import music
from music import MusicCog, STAFF_ROLE_NAME


class DummyBot:
    def __init__(self, loop, guild):
        self.loop = loop
        self._guild = guild

    def get_guild(self, guild_id):
        if self._guild and self._guild.id == guild_id:
            return self._guild
        return None


class DummySetupBot:
    def __init__(self):
        self.added = []

    async def add_cog(self, cog):
        self.added.append(cog)


class FakeVoiceClient:
    def __init__(self, guild, channel):
        self.guild = guild
        self.channel = channel
        self._playing = False
        self.play_calls = []
        self.disconnected = False
        self.guild.voice_client = self

    def is_connected(self):
        return not self.disconnected

    def is_playing(self):
        return self._playing

    def play(self, source, after=None):
        self._playing = True
        self.play_calls.append(source)
        self._after = after

    def stop(self):
        self._playing = False

    async def move_to(self, channel):
        self.channel = channel

    async def disconnect(self):
        self.disconnected = True
        self.guild.voice_client = None


class FakeVoiceChannel:
    def __init__(self, guild, channel_id=10):
        self.guild = guild
        self.id = channel_id
        self.mention = "#vocal"

    async def connect(self):
        return FakeVoiceClient(self.guild, self)


class FakeContext:
    def __init__(self, guild, author):
        self.guild = guild
        self.author = author
        self.sent = []

    async def send(self, *, embed=None, content=None):
        self.sent.append(SimpleNamespace(embed=embed, content=content))
        return self.sent[-1]


def make_author(*, staff=True, channel=None):
    role_name = STAFF_ROLE_NAME if staff else "Invité"
    roles = [SimpleNamespace(name=role_name)]
    voice_state = SimpleNamespace(channel=channel) if channel else None
    return SimpleNamespace(display_name="Tester", roles=roles, voice=voice_state)


def make_cog(loop, guild):
    bot = DummyBot(loop, guild)
    cog = MusicCog(bot)
    cog._ytdl = object()  # Force activation even si yt_dlp absent durant les tests
    return cog


@pytest.mark.asyncio
async def test_musique_requires_staff_role(monkeypatch):
    loop = asyncio.get_running_loop()
    guild = SimpleNamespace(id=1, voice_client=None)
    channel = FakeVoiceChannel(guild)
    author = make_author(staff=False, channel=channel)
    ctx = FakeContext(guild, author)
    cog = make_cog(loop, guild)

    await MusicCog.musique_command.callback(cog, ctx, query="https://example.com")

    assert ctx.sent[-1].embed.title == "Accès refusé"


@pytest.mark.asyncio
async def test_musique_requires_voice_channel(monkeypatch):
    loop = asyncio.get_running_loop()
    guild = SimpleNamespace(id=2, voice_client=None)
    author = make_author(staff=True, channel=None)
    ctx = FakeContext(guild, author)
    cog = make_cog(loop, guild)

    await MusicCog.musique_command.callback(cog, ctx, query="https://example.com")

    assert ctx.sent[-1].embed.title == "Salon vocal requis"


@pytest.mark.asyncio
async def test_musique_adds_track_and_starts_playback(monkeypatch):
    loop = asyncio.get_running_loop()
    guild = SimpleNamespace(id=3, voice_client=None)
    channel = FakeVoiceChannel(guild)
    author = make_author(staff=True, channel=channel)
    ctx = FakeContext(guild, author)
    cog = make_cog(loop, guild)

    tracks = [{"title": "Track", "stream_url": "http://stream", "webpage": "https://example.com"}]
    extractor = AsyncMock(return_value=list(tracks))
    monkeypatch.setattr(MusicCog, "_extract_tracks", extractor)
    monkeypatch.setattr(discord, "FFmpegPCMAudio", lambda url, **kwargs: f"audio:{url}")

    await MusicCog.musique_command.callback(cog, ctx, query="https://example.com")

    voice_client = guild.voice_client
    assert voice_client is not None
    assert voice_client.play_calls
    assert cog.queues[guild.id] == deque()
    assert ctx.sent[-1].embed.title == "Musique ajoutée"


@pytest.mark.asyncio
async def test_musique_stop_clears_queue_and_disconnects(monkeypatch):
    loop = asyncio.get_running_loop()
    guild = SimpleNamespace(id=4, voice_client=None)
    channel = FakeVoiceChannel(guild)
    author = make_author(staff=True, channel=channel)
    ctx = FakeContext(guild, author)
    cog = make_cog(loop, guild)

    voice_client = await channel.connect()
    cog.queues[guild.id] = deque([{"title": "Track", "stream_url": "url"}])
    voice_client.play("dummy")

    await MusicCog.musique_command.callback(cog, ctx, query="stop")

    assert guild.voice_client is None
    assert guild.id not in cog.queues or not cog.queues[guild.id]
    assert ctx.sent[-1].embed.title == "Lecture arrêtée"

@pytest.mark.asyncio
async def test_musique_lazy_imports_ytdlp(monkeypatch):
    loop = asyncio.get_running_loop()
    guild = SimpleNamespace(id=5, voice_client=None)
    bot = DummyBot(loop, guild)

    class DummyModule:
        class DummyClient:
            def extract_info(self, *_, **__):
                return {}

        def YoutubeDL(self, *_):
            return self.DummyClient()

    monkeypatch.setattr(music, "yt_dlp", None, raising=False)
    monkeypatch.setattr(music.importlib, "import_module", lambda name: DummyModule())

    cog = MusicCog(bot)
    assert cog._ytdl is None
    assert cog._ensure_ytdl() is True
    assert cog._ytdl is not None


@pytest.mark.asyncio
async def test_setup_skips_when_pynacl_missing(monkeypatch):
    monkeypatch.setattr(music, "_pynacl_available", lambda: False)
    bot = DummySetupBot()
    await music.setup(bot)
    assert not bot.added


@pytest.mark.asyncio
async def test_setup_adds_cog_when_pynacl_available(monkeypatch):
    monkeypatch.setattr(music, "_pynacl_available", lambda: True)
    bot = DummySetupBot()
    await music.setup(bot)
    assert bot.added
