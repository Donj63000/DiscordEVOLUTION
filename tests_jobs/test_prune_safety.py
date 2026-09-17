"""Aucune absence de cache ou erreur de recensement ne vaut preuve de départ."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from tests_jobs.helpers import http_error

DATA = {
    "101": {"name": "Présent", "jobs": {"Mineur": 100}},
    "102": {"name": "Parti", "jobs": {"Tailleur": 100}},
    "ancienne-fiche": {"name": "Ancien nom", "jobs": {"Paysan": 100}},
}


def census(guild, identifiers, *, error=None):
    async def fetch_members(*, limit):
        assert limit is None
        for identifier in identifiers:
            yield NS(id=identifier)
        if error is not None:
            raise error
    guild.fetch_members = fetch_members


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["forbidden", "http", "os", "timeout", "intent"])
async def test_partial_members_never_cause_deletions(runtime, kind):
    rt = runtime
    source = rt.console.add(DATA)
    rt.guild.member_count = 3
    errors = {
        "forbidden": http_error(rt.wire, "Forbidden", 403, 50013),
        "http": http_error(rt.wire),
        "os": OSError("panne réseau"),
        "timeout": TimeoutError(),
        "intent": rt.wire.ClientException("intention membres désactivée"),
    }
    census(rt.guild, [900], error=errors[kind])
    with pytest.raises(rt.module.JobPersistenceError, match="incomplète"):
        await rt.cog.prune_jobs()
    assert await rt.cog._snapshot_reader.extract_payload(source) == DATA
    assert rt.console.edits == rt.console.sends == 0
    assert not rt.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [None, 0, 3])
async def test_incomplete_or_unknown_member_count_blocks_prune(runtime, count):
    rt = runtime
    rt.console.add(DATA)
    rt.guild.member_count = count
    census(rt.guild, [900])
    with pytest.raises(rt.module.JobPersistenceError, match="incomplet"):
        await rt.cog.prune_jobs()
    assert rt.console.edits == rt.console.sends == 0


@pytest.mark.asyncio
async def test_complete_census_removes_only_absent_ids_not_legacy_names(runtime):
    rt = runtime
    rt.console.add(DATA)
    rt.guild.member_count = 2
    census(rt.guild, [900, 101])
    assert await rt.cog.prune_jobs() == 1
    assert set(rt.cog.jobs_data) == {"101", "ancienne-fiche"}
    assert rt.console.edits == 1
    assert rt.cog.jobs_data["ancienne-fiche"] == DATA["ancienne-fiche"]


@pytest.mark.asyncio
async def test_newly_joined_cached_member_is_not_pruned(runtime):
    rt = runtime
    rt.console.add(DATA)
    rt.guild.member_count = 2
    rt.guild.members = [NS(id=102)]
    census(rt.guild, [900, 101])
    assert await rt.cog.prune_jobs() == 0
    assert rt.console.edits == 0


@pytest.mark.asyncio
async def test_member_remove_does_not_delete_legacy_homonym(runtime):
    rt = runtime
    rt.console.add(DATA)
    await rt.cog.on_member_remove(NS(id=999, display_name="Ancien nom", guild=rt.guild))
    assert rt.cog.jobs_data == DATA and rt.console.edits == 0


@pytest.mark.asyncio
async def test_cog_load_never_prunes_before_discord_ready(runtime):
    rt = runtime
    rt.cog.initialize_data = AsyncMock()
    rt.cog.prune_jobs = AsyncMock()
    rt.cog.auto_prune = NS(is_running=lambda: False, start=Mock())
    await rt.cog.cog_load()
    rt.cog.initialize_data.assert_awaited_once()
    rt.cog.prune_jobs.assert_not_awaited()
    rt.cog.auto_prune.start.assert_called_once()


@pytest.mark.asyncio
async def test_background_initialization_waits_for_ready(runtime):
    rt = runtime
    ready = asyncio.Event()
    rt.bot.wait_until_ready = ready.wait
    rt.cog.initialize_data = AsyncMock()
    task = asyncio.create_task(rt.cog.before_auto_prune())
    await asyncio.sleep(0)
    rt.cog.initialize_data.assert_not_awaited()
    ready.set()
    await asyncio.wait_for(task, 1)
    rt.cog.initialize_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_prune_survives_temporary_directory_failure(runtime):
    rt = runtime
    rt.cog.prune_jobs = AsyncMock(side_effect=rt.module.JobPersistenceError("panne temporaire"))
    await rt.cog.auto_prune()
    rt.cog.prune_jobs.assert_awaited_once()


@pytest.mark.asyncio
async def test_multiguild_partial_census_does_not_delete_shared_registry(runtime):
    rt = runtime
    rt.console.add(DATA)
    rt.guild.member_count = 2
    census(rt.guild, [900, 101])
    other = NS(id=2, members=[], member_count=2)
    census(other, [900], error=OSError("serveur inaccessible"))
    rt.bot.guilds.append(other)
    with pytest.raises(rt.module.JobPersistenceError):
        await rt.cog.prune_jobs()
    assert rt.console.edits == rt.console.sends == 0
