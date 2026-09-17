"""Régression du redémarrage, de la découverte et des écritures métier."""
import asyncio
import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from tests_jobs.helpers import Attachment, Message, encoded, header, http_error

DATA = {
    "101": {"name": "Artisan-Test", "jobs": {"Mineur": 100}},
    "ancienne-fiche": {"name": "Fiche conservée", "jobs": {"Tailleur": 100}},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("form,suffix", [
    ("attachment", ""), ("attachment", " (fichier)"), ("inline", ""),
])
async def test_read_and_mutation_use_same_payload(runtime, form, suffix):
    rt = runtime
    message = rt.console.add(DATA, form=form, suffix=suffix)
    first = await rt.cog.read_jobs_snapshot(rt.guild)
    first["101"]["jobs"]["Mineur"] = 1
    assert rt.cog.jobs_data == DATA
    await rt.cog._restore_jobs_for_mutation(rt.guild)
    assert rt.cog.jobs_data == DATA
    assert rt.cog.console_message_id == message.id
    assert rt.console.sends == rt.console.edits == 0
    assert not rt.path.exists()


@pytest.mark.asyncio
async def test_real_large_writer_can_be_reread_after_restart(runtime):
    rt = runtime
    large = {str(index): {"name": f"Artisan-Test-{index}", "jobs": {"Mineur": 90}}
             for index in range(101, 151)}
    rt.console.add(large)
    result = await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test-101", "Mineur", 100)
    assert result["modifie"] is True
    expected = copy.deepcopy(large)
    expected["101"]["jobs"]["Mineur"] = 100
    saved = rt.console.messages[-1]
    assert saved.attachments and "(fichier)" in saved.content
    assert json.loads(rt.path.read_text()) == expected
    # Simuler exactement la variante déployée sans annotation « fichier ».
    saved.content = saved.content.replace(" (fichier)", "")
    fresh = rt.module.JobCog(rt.bot)
    fresh.get_console_channel = AsyncMock(return_value=rt.console)
    assert await fresh.read_jobs_snapshot(rt.guild) == expected
    assert rt.console.edits == 1 and rt.console.sends == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("noise_count", [0, 201, 601, 1101])
async def test_unpinned_old_snapshot_is_found_beyond_recent_window(runtime, noise_count):
    rt = runtime
    rt.cog._console_history_limit = 100
    source = rt.console.add(DATA, identifier=1)
    for identifier in range(2, noise_count + 2):
        rt.console.messages.append(Message(rt.console, identifier, "rapport interne"))
    assert await rt.cog.read_jobs_snapshot(rt.guild) == DATA
    assert rt.cog.console_message_id == source.id
    assert source.pinned
    assert rt.console.sends == rt.console.edits == 0
    assert all(limit == 100 for limit, _ in rt.console.history_calls)


@pytest.mark.asyncio
async def test_pins_are_paginated_with_new_sdk_api(runtime):
    rt = runtime
    source = rt.console.add(DATA, identifier=1, pinned=True)
    pins = [Message(rt.console, index, "autre snapshot", pinned=True) for index in range(2, 81)]
    pins.append(source)
    limits = []

    def paginated_pins(*, limit=50):
        limits.append(limit)
        async def iterate():
            for message in pins if limit is None else pins[:limit]:
                yield message
        return iterate()

    rt.console.pins = paginated_pins
    rt.cog._console_history_limit = 0
    assert await rt.cog.read_jobs_snapshot(rt.guild) == DATA
    assert limits == [None]


@pytest.mark.asyncio
async def test_newer_unpinned_snapshot_wins_over_an_old_pin(runtime):
    rt = runtime
    rt.cog._console_history_limit = 100
    rt.console.add({"101": {"jobs": {"Mineur": 1}}}, identifier=1, pinned=True)
    fresh = rt.console.add(DATA, identifier=150)
    for identifier in range(151, 180):
        rt.console.messages.append(Message(rt.console, identifier, "rapport"))
    assert await rt.cog.read_jobs_snapshot(rt.guild) == DATA
    assert rt.cog.console_message_id == fresh.id


@pytest.mark.asyncio
async def test_newest_corrupt_snapshot_blocks_fallback_and_mutation(runtime):
    rt = runtime
    rt.console.add(DATA, identifier=10, pinned=True)
    corrupt = Message(rt.console, 20, "===BOTJOBS===", [Attachment(b"{")])
    rt.console.messages.append(corrupt)
    with pytest.raises(rt.module.JobPersistenceError, match="illisible"):
        await rt.cog.read_jobs_snapshot(rt.guild)
    with pytest.raises(rt.module.JobPersistenceError):
        await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 80)
    assert rt.console.edits == rt.console.sends == 0
    assert not rt.path.exists()


@pytest.mark.asyncio
async def test_current_message_deleted_can_be_rediscovered_without_reset(runtime):
    rt = runtime
    old = rt.console.add(DATA, identifier=10)
    await rt.cog.read_jobs_snapshot(rt.guild)
    old.deleted = True
    fresh = rt.console.add(DATA, identifier=20)
    rt.cog._console_sync_ttl = 0
    assert await rt.cog.read_jobs_snapshot(rt.guild) == DATA
    assert rt.cog.console_message_id == fresh.id
    assert rt.console.edits == rt.console.sends == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["history_error", "pins_error", "fetch_error"])
async def test_discord_failure_does_not_replace_loaded_registry(runtime, field):
    rt = runtime
    rt.console.add(DATA)
    await rt.cog.read_jobs_snapshot(rt.guild)
    rt.cog._console_sync_ttl = 0
    setattr(rt.console, field, http_error(rt.wire, "Forbidden", 403, 50013))
    with pytest.raises(rt.module.JobPersistenceError):
        await rt.cog.read_jobs_snapshot(rt.guild)
    assert rt.cog.jobs_data == DATA
    with pytest.raises(rt.module.JobPersistenceError):
        await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 80)
    assert rt.console.edits == rt.console.sends == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", ["viewable", "readable"])
async def test_missing_read_permission_is_not_a_new_empty_registry(runtime, permission):
    rt = runtime
    setattr(rt.console, permission, False)
    with pytest.raises(rt.module.JobPersistenceError, match="historique"):
        await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 100)
    assert rt.console.sends == 0 and not rt.path.exists()


@pytest.mark.asyncio
async def test_snapshot_disappeared_does_not_overwrite_local_backup(runtime):
    rt = runtime
    source = rt.console.add(DATA)
    await rt.cog.read_jobs_snapshot(rt.guild)
    rt.path.write_bytes(encoded(DATA))
    source.deleted = True
    with pytest.raises(rt.module.JobPersistenceError, match="disparu"):
        await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 80)
    assert rt.path.read_bytes() == encoded(DATA)
    assert rt.cog.jobs_data == DATA
    assert rt.console.sends == 0


@pytest.mark.asyncio
async def test_local_backup_blocks_empty_bootstrap_even_when_fallback_disabled(runtime):
    rt = runtime
    rt.path.write_bytes(encoded(DATA))
    with pytest.raises(rt.module.JobPersistenceError, match="copie locale"):
        await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 80)
    assert rt.path.read_bytes() == encoded(DATA) and rt.console.sends == 0


@pytest.mark.asyncio
async def test_first_registration_is_allowed_on_truly_new_installation(runtime):
    rt = runtime
    result = await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 100)
    assert result["modifie"] is True
    assert rt.console.sends == 1
    assert await rt.cog.read_jobs_snapshot(rt.guild) == {
        "101": {"name": "Artisan-Test", "jobs": {"Mineur": 100}},
    }


@pytest.mark.asyncio
async def test_empty_remote_snapshot_is_not_overwritten_by_stale_local_copy(runtime, monkeypatch):
    rt = runtime
    rt.console.add({})
    rt.path.write_bytes(encoded(DATA))
    monkeypatch.setitem(rt.globals, "JOB_ALLOW_LOCAL_FALLBACK", True)
    await rt.cog.initialize_data()
    assert rt.cog.jobs_data == {}
    assert await rt.cog.read_jobs_snapshot(rt.guild) == {}
    assert rt.path.read_bytes() == encoded(DATA)
    assert rt.console.sends == rt.console.edits == 0


@pytest.mark.asyncio
async def test_native_search_displays_restored_professions(runtime):
    rt = runtime
    rt.console.add(DATA)
    ctx = NS(guild=rt.guild, author=NS(id=101, display_name="Artisan-Test", roles=[]),
             channel=NS(id=77))
    await rt.cog._execute_job_command(ctx, "Mineur")
    embed = rt.cog.send_logo_embed.await_args.args[1]
    assert embed.title.startswith("Résultats")
    assert "Artisan-Test" in embed.fields[0].value and "100" in embed.fields[0].value


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [("me",), ("liste",), ("Mineur",), ("del", "Mineur")])
async def test_native_paths_never_announce_no_jobs_when_source_missing(runtime, args):
    rt = runtime
    ctx = NS(guild=rt.guild, author=NS(id=101, display_name="Artisan-Test", roles=[]),
             channel=NS(id=77))
    await rt.cog._execute_job_command(ctx, *args)
    assert rt.cog.send_logo_embed.await_args.args[1].title == "Annuaire indisponible"
    assert rt.console.sends == rt.console.edits == 0


@pytest.mark.asyncio
async def test_pin_permission_failure_does_not_invalidate_confirmed_write(runtime):
    rt = runtime
    rt.console.add(DATA)
    rt.console.pin_error = http_error(rt.wire, "Forbidden", 403, 50013)
    result = await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 80)
    assert result["modifie"] and not rt.cog._remote_uncertain
    assert json.loads(rt.path.read_text())["101"]["jobs"]["Mineur"] == 80


@pytest.mark.asyncio
async def test_unconfirmed_write_leaves_memory_and_local_backup_unchanged(runtime):
    rt = runtime
    rt.console.add(DATA)
    rt.path.write_bytes(encoded(DATA))
    rt.console.ignore_edits = True
    with pytest.raises(rt.module.JobPersistenceError, match="incertaine"):
        await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 80)
    assert rt.cog.jobs_data == DATA and rt.cog._remote_uncertain
    assert rt.path.read_bytes() == encoded(DATA)


@pytest.mark.asyncio
async def test_cancelled_commit_is_reread_before_retry_without_double_write(runtime):
    rt = runtime
    rt.console.add(DATA)
    rt.path.write_bytes(encoded(DATA))
    entered = asyncio.Event()

    async def after_write():
        entered.set()
        await asyncio.Event().wait()

    rt.console.after_write = after_write
    pending = asyncio.create_task(rt.cog.update_member_job(
        rt.guild, 101, "Artisan-Test", "Mineur", 80,
    ))
    await asyncio.wait_for(entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert rt.cog._remote_uncertain and rt.path.read_bytes() == encoded(DATA)
    rt.console.after_write = AsyncMock()
    assert (await rt.cog.read_jobs_snapshot(rt.guild))["101"]["jobs"]["Mineur"] == 80
    result = await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 80)
    assert not result["modifie"] and rt.console.edits == 1


@pytest.mark.asyncio
async def test_concurrent_updates_preserve_both_changes(runtime):
    rt = runtime
    rt.console.add(DATA)
    await asyncio.gather(
        rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 80),
        rt.cog.update_member_job(rt.guild, 102, "Autre artisan", "Paysan", 100),
    )
    restored = await rt.cog.read_jobs_snapshot(rt.guild)
    assert restored["101"]["jobs"]["Mineur"] == 80
    assert restored["102"]["jobs"]["Paysan"] == 100
    assert restored["ancienne-fiche"] == DATA["ancienne-fiche"]


@pytest.mark.asyncio
async def test_history_disabled_does_not_allow_a_new_snapshot(runtime):
    rt = runtime
    rt.cog._console_history_limit = 0
    with pytest.raises(rt.module.JobPersistenceError, match="désactivé"):
        await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 100)
    assert rt.console.sends == 0


@pytest.mark.asyncio
async def test_broken_history_cursor_fails_without_writing(runtime):
    rt = runtime
    rt.cog._console_history_limit = 2
    rt.console.ignore_before = True
    for identifier in range(1, 5):
        rt.console.messages.append(Message(rt.console, identifier, "rapport"))
    with pytest.raises(rt.module.JobPersistenceError, match="pagination"):
        await rt.cog.update_member_job(rt.guild, 101, "Artisan-Test", "Mineur", 100)
    assert len(rt.console.history_calls) == 2
    assert rt.console.sends == 0


@pytest.mark.asyncio
async def test_read_deadline_is_reported_as_unavailable_not_empty(runtime):
    rt = runtime
    rt.cog._discover_jobs_snapshot = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(rt.module.JobPersistenceError, match="30 secondes"):
        await rt.cog.read_jobs_snapshot(rt.guild)
    assert rt.console.sends == 0


@pytest.mark.asyncio
async def test_initialization_preserves_legacy_key_despite_matching_cached_name(runtime):
    rt = runtime
    rt.console.add(DATA)
    rt.guild.members = [NS(id=105, name="ancienne-fiche", display_name="Fiche conservée")]
    await rt.cog.initialize_data()
    assert rt.cog.jobs_data == DATA
    assert not rt.path.exists() and rt.console.edits == 0


@pytest.mark.asyncio
async def test_atomic_local_write_keeps_previous_file_if_replace_fails(runtime, monkeypatch):
    rt = runtime
    rt.path.write_bytes(encoded(DATA))
    rt.cog.jobs_data = {"101": {"jobs": {"Paysan": 10}}}
    def fail_replace(*args):
        raise OSError("disque indisponible")
    monkeypatch.setattr(rt.globals["os"], "replace", fail_replace)
    rt.cog.save_data_local()
    assert rt.path.read_bytes() == encoded(DATA)
    assert not list(rt.path.parent.glob(".jobs-*.json"))


@pytest.mark.asyncio
async def test_file_only_legacy_backup_is_preserved_by_clear(runtime):
    rt = runtime
    old = rt.console.add(DATA)
    old.content = ""
    rt.console.messages.append(Message(rt.console, 20, "rapport à nettoyer"))
    ctx = NS(guild=rt.guild, author=NS(id=101, roles=[]))
    if rt.mode == "sdk":
        await rt.cog.clear_console_command.callback(rt.cog, ctx, "console", "CONFIRMER")
    else:
        await rt.cog.clear_console_command(ctx, "console", "CONFIRMER")
    assert not old.deleted and rt.console.messages[-1].deleted


@pytest.mark.asyncio
async def test_ancient_pin_does_not_require_rescanning_all_history(runtime):
    rt = runtime
    rt.cog._console_history_limit = 100
    rt.console.add(DATA, identifier=1, pinned=True)
    for identifier in range(2, 1502):
        rt.console.messages.append(Message(rt.console, identifier, "rapport"))
    assert await rt.cog.read_jobs_snapshot(rt.guild) == DATA
    assert len(rt.console.history_calls) == 1


@pytest.mark.asyncio
async def test_in_place_update_remains_newer_than_a_later_created_old_copy(runtime):
    from datetime import datetime, timezone
    rt = runtime
    active = rt.console.add(DATA, identifier=10, pinned=True)
    old = rt.console.add({"101": {"jobs": {"Mineur": 1}}}, identifier=20, pinned=True)
    active.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    active.edited_at = datetime(2026, 9, 17, tzinfo=timezone.utc)
    old.created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert await rt.cog.read_jobs_snapshot(rt.guild) == DATA
    await rt.cog._restore_jobs_for_mutation(rt.guild)
    assert rt.cog.jobs_data == DATA and rt.cog.console_message_id == active.id
