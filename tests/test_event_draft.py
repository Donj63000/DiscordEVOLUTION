import os
import sys
import types
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# minimal stub for the optional dateparser dependency
fake_dp = types.ModuleType("dateparser")

def _parse(text, *_, **__):
    try:
        return datetime.datetime.strptime(text, "%d/%m/%Y %H:%M")
    except Exception:
        return None

fake_dp.parse = _parse
sys.modules.setdefault("dateparser", fake_dp)

# lightweight stub of discord to import EventDraft without dependency
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


fake_discord.Embed = _Embed
fake_discord.Interaction = type("Interaction", (), {})

ui_mod = types.ModuleType("discord.ui")
ui_mod.View = type("View", (), {"__init__": lambda self, *a, **k: None})
ui_mod.button = lambda *a, **k: (lambda f: f)
fake_discord.ui = ui_mod

fake_discord.ButtonStyle = types.SimpleNamespace(success=1, danger=2)
fake_discord.PermissionOverwrite = type("PermissionOverwrite", (), {})
fake_discord.Forbidden = type("Forbidden", (Exception,), {})
fake_discord.HTTPException = type("HTTPException", (Exception,), {"text": ""})

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

from event_conversation import EventDraft


def _base_data():
    return {
        "name": "Raid",
        "description": "desc",
        "start_time": "01/01/2030 10:00",
        "end_time": "01/01/2030 11:00",
    }


def test_from_json_default_dungeon_name():
    data = _base_data()
    draft = EventDraft.from_json(data)
    assert draft.dungeon_name == "Donjon"


def test_embed_contains_dungeon_name():
    data = _base_data()
    data["dungeon_name"] = "Citadelle"
    draft = EventDraft.from_json(data)
    prev = draft.to_preview_embed()
    ann = draft.to_announce_embed()
    assert any(f.name == "Donjon" and f.value == "Citadelle" for f in prev.fields)
    assert any(f.name == "Donjon" and f.value == "Citadelle" for f in ann.fields)
