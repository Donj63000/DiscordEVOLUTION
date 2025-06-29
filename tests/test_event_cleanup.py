import os
import sys
import types
import asyncio
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# stub dateparser.parse used in event_conversation
fake_dp = types.ModuleType("dateparser")


def _parse(text, *_, **__):
    try:
        return datetime.datetime.strptime(text, "%d/%m/%Y %H:%M")
    except Exception:
        return None


fake_dp.parse = _parse
sys.modules.setdefault("dateparser", fake_dp)


fake_discord = types.ModuleType("discord")


class _Embed:
    def __init__(self, title=None, description=None, colour=None):
        self.title = title
        self.description = description
        self.colour = colour
        self.fields = []

    def add_field(self, name, value, inline=True):
        self.fields.append(types.SimpleNamespace(name=name, value=value, inline=inline))

    def set_footer(self, *, text=None):
        self.footer = text


class _Message:
    def __init__(self, mid=1):
        self.id = mid


class _Role:
    def __init__(self, rid=1):
        self.id = rid
        self.deleted = False

    async def delete(self, *, reason=None):  # pragma: no cover - simple flag
        self.deleted = True


class _PrivChannel:
    def __init__(self, gid, cid=10):
        self.id = cid
        self.guild = gid
        self.deleted = False

    async def fetch_message(self, mid):  # pragma: no cover - unused
        return _Message(mid)

    async def delete(self, *, reason=None):
        self.deleted = True


class _AnnounceChannel(_PrivChannel):
    pass


class _Guild:
    def __init__(self, role, channel):
        self._role = role
        self._channel = channel

    def get_role(self, rid):
        return self._role if rid == self._role.id else None

    def get_channel(self, cid):
        return self._channel if cid == self._channel.id else None


fake_discord.Embed = _Embed
fake_discord.Message = _Message
fake_discord.HTTPException = type("HTTPException", (Exception,), {})
fake_discord.Forbidden = type("Forbidden", (Exception,), {})

ui_mod = types.ModuleType("discord.ui")
ui_mod.View = type("View", (), {"__init__": lambda self, *a, **k: None})
ui_mod.button = lambda *a, **k: (lambda f: f)
fake_discord.ui = ui_mod

fake_discord.ButtonStyle = types.SimpleNamespace(success=1, danger=2)
fake_discord.PermissionOverwrite = type("PermissionOverwrite", (), {})

ext_mod = types.ModuleType("discord.ext")
commands_mod = types.ModuleType("discord.ext.commands")
commands_mod.Cog = object
commands_mod.command = lambda *a, **k: (lambda f: f)
commands_mod.has_role = lambda *a, **k: (lambda f: f)
commands_mod.Bot = object
tasks_mod = types.ModuleType("discord.ext.tasks")
tasks_mod.loop = lambda *a, **k: (lambda f: f)
ext_mod.commands = commands_mod
ext_mod.tasks = tasks_mod

fake_discord.ext = ext_mod
fake_discord.utils = types.SimpleNamespace(get=lambda *a, **k: None, utcnow=lambda: datetime.datetime.utcnow())

sys.modules.setdefault("discord", fake_discord)
sys.modules.setdefault("discord.ext", ext_mod)
sys.modules.setdefault("discord.ext.commands", commands_mod)
sys.modules.setdefault("discord.ext.tasks", tasks_mod)

from event_conversation import EventConversationCog


class _Bot:
    def __init__(self, guild, ann_chan, loop):
        self._guild = guild
        self._ann_chan = ann_chan
        self.loop = loop
        self.views = []

    async def fetch_channel(self, cid):  # pragma: no cover - minimal stub
        return self._ann_chan

    def add_view(self, view, *, message_id=None):
        self.views.append((view, message_id))


def test_restore_view_cleanup_on_restart():
    role = _Role(42)
    guild = _Guild(role, _PrivChannel(None, cid=99))
    ann_chan = _AnnounceChannel(guild, cid=55)
    ann_chan.guild = guild

    loop = asyncio.new_event_loop()
    bot = _Bot(guild, ann_chan, loop)
    cog = EventConversationCog(bot)

    rec = {
        "channel_id": ann_chan.id,
        "message_id": 1,
        "role_id": role.id,
        "event_id": 7,
        "event_channel_id": guild._channel.id,
        "ends_at": (datetime.datetime.utcnow() - datetime.timedelta(minutes=1)).isoformat(),
    }

    loop.run_until_complete(cog._restore_view(rec))
    loop.run_until_complete(asyncio.sleep(0))

    assert role.deleted
    assert guild._channel.deleted
