"""Tests isolés exécutables sans SDK ; mêmes scénarios disponibles avec discord.py."""
import importlib
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from tests_jobs.helpers import Console, isolated_job


@pytest.fixture(params=["isolated", "sdk"])
def runtime(request, monkeypatch, tmp_path):
    # Les répertoires de tests ne touchent jamais les données réelles du dépôt.
    for key in ("JOB_CONSOLE_HISTORY_LIMIT", "CONSOLE_HISTORY_LIMIT", "JOB_CONSOLE_SYNC_TTL",
                "CHANNEL_CONSOLE_ID", "CHANNEL_CONSOLE", "JOB_ALLOW_LOCAL_FALLBACK"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DISCORD_HISTORY_RETRIES", "0")
    if request.param == "sdk":
        wire = pytest.importorskip("discord", reason="SDK discord.py absent ; mode isolé disponible")
        module = importlib.import_module("job")
        globals_ = vars(module)
    else:
        module, wire = isolated_job()
        globals_ = module.globals
    monkeypatch.setitem(globals_, "DATA_FILE", str(tmp_path / "jobs_data.json"))
    monkeypatch.setitem(globals_, "JOB_ALLOW_LOCAL_FALLBACK", False)
    guild = NS(id=1, members=[], member_count=1, me=NS(id=900), filesize_limit=8 * 1024 * 1024)
    bot = NS(user=guild.me, guilds=[guild], get_all_members=lambda: iter(guild.members),
             wait_until_ready=AsyncMock())
    console = Console(bot, guild, wire)
    guild.text_channels = [console]
    guild.get_channel = lambda identifier: console if identifier == console.id else None
    cog = module.JobCog(bot)
    cog.get_console_channel = AsyncMock(return_value=console)
    cog.send_logo_embed = AsyncMock()
    return NS(module=module, wire=wire, globals=globals_, bot=bot, guild=guild,
              console=console, cog=cog, path=tmp_path / "jobs_data.json", mode=request.param)
