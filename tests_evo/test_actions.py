"""Actions personnelles, reçus vérifiés et sauvegardes métiers sans réseau réel."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from activite import ActiviteCog, VALIDATED_ROLE_NAME
from job import JobCog, JobPersistenceError
from tests_evo.budget_helpers import FakeConsole
from tests_evo.helpers import context
from utils.evo_actions import join_activity, leave_activity, set_own_job, remove_own_job
from utils.evo_config import EvoError


@pytest.fixture
def actions():
    ctx = context()
    ctx.bot.user = ctx.guild.me
    ctx.request_text = ""
    ctx.trigger_id = 123
    ctx.before_mutation = AsyncMock()
    ctx.action_receipt = None
    console = FakeConsole(ctx.bot, ctx.guild)
    ctx.guild.text_channels.append(console)
    jobs = JobCog(ctx.bot)
    jobs.initialized = True
    jobs.save_data_local = Mock()
    jobs.get_console_channel = AsyncMock(return_value=console)
    ctx.bot.cogs["JobCog"] = jobs
    activities = ActiviteCog(ctx.bot)
    activities.initialized = True
    activities._source_guild_id = ctx.guild.id
    activities.save_data_local = AsyncMock()
    activities.dump_data_to_console = AsyncMock()
    activities._schedule_calendar_refresh = Mock()
    activities._sync_legacy_roles = AsyncMock()
    activities.sync_card = AsyncMock()
    activities._notify_members = AsyncMock()
    activities.activities_data = {"next_id": 2, "events": {"1": {
        "id": "1", "guild_id": ctx.guild.id, "titre": "Crocabulia", "creator_id": 3,
        "participants": [], "waitlist": [], "capacity": 8, "cancelled": False,
        "starts_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "channel_id": 30, "message_id": 88,
    }}}
    ctx.member.roles = [SimpleNamespace(name=VALIDATED_ROLE_NAME)]
    ctx.bot.cogs["ActiviteCog"] = activities
    return SimpleNamespace(ctx=ctx, jobs=jobs, console=console, activities=activities)


@pytest.mark.asyncio
async def test_job_mutation_is_saved_before_memory_and_receipt(actions):
    ctx, jobs, console = actions.ctx, actions.jobs, actions.console
    ctx.request_text = "Ajoute Bûcheron niveau 100 à mon profil."

    async def after_write():
        assert jobs.jobs_data == {}
        assert ctx.action_receipt is None
        ctx.before_mutation.assert_awaited_once()

    console.after_write = after_write
    receipt = await set_own_job(ctx, "Bûcheron", 100)

    assert receipt["action_effectuee"]
    assert ctx.action_receipt is receipt
    assert jobs.jobs_data[str(ctx.member.id)]["jobs"]["Bûcheron"] == 100
    assert str(3) not in jobs.jobs_data
    assert console.messages[0].pinned


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Mets Coca Bûcheron 100", "Qui peut ajouter mon métier ?",
                                 "Ajoute pas Bûcheron 100 à mon profil",
                                 "Ajoute Bûcheron 100 au profil de <@3> et au mien"])
async def test_information_and_third_party_requests_never_modify_jobs(actions, text):
    actions.ctx.request_text = text
    with pytest.raises(EvoError):
        await set_own_job(actions.ctx, "Bûcheron", 100)
    assert actions.console.sends == 0
    actions.ctx.before_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_unknown_name_requires_precision_without_reservation(actions):
    actions.ctx.request_text = "Ajoute Inventeur 100 à mon profil"
    result = await set_own_job(actions.ctx, "Inventeur", 100)
    assert not result["action_effectuee"]
    assert actions.console.sends == 0
    actions.ctx.before_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_failed_save_keeps_memory_and_receipt_then_restores(actions):
    ctx, jobs, console = actions.ctx, actions.jobs, actions.console
    await jobs.update_member_job(ctx.guild, ctx.member.id, "Val", "Mineur", 10)
    original = copy.deepcopy(jobs.jobs_data)
    ctx.request_text = "Mets mon Mineur au niveau 100"
    console.write_error = RuntimeError("write refused")
    with pytest.raises(EvoError):
        await set_own_job(ctx, "Mineur", 100)
    assert jobs.jobs_data == original
    assert jobs._remote_uncertain
    assert ctx.action_receipt is None
    console.write_error = None
    result = await set_own_job(ctx, "Mineur", 100)
    assert result["action_effectuee"]
    assert not jobs._remote_uncertain


@pytest.mark.asyncio
async def test_cancelled_confirmed_write_is_restored_and_request_not_replayed(actions):
    ctx, jobs, console = actions.ctx, actions.jobs, actions.console
    ctx.request_text = "Ajoute Mineur 100 à mon profil"
    written = asyncio.Event()

    async def hold_after_write():
        written.set()
        await asyncio.Future()

    console.after_write = hold_after_write
    task = asyncio.create_task(set_own_job(ctx, "Mineur", 100))
    await asyncio.wait_for(written.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert jobs.jobs_data == {}
    assert jobs._remote_uncertain
    assert ctx.action_receipt is None
    console.after_write = None
    result = await set_own_job(ctx, "Mineur", 100)
    assert not result["action_effectuee"]
    assert "déjà" in result["message"]
    assert console.sends == 1
    assert jobs.jobs_data[str(ctx.member.id)]["jobs"]["Mineur"] == 100


@pytest.mark.asyncio
async def test_shared_job_lock_prevents_lost_updates(actions):
    ctx, jobs = actions.ctx, actions.jobs
    await asyncio.gather(
        jobs.update_member_job(ctx.guild, ctx.member.id, "Val", "Mineur", 20),
        jobs.update_member_job(ctx.guild, 3, "Alex", "Paysan", 100),
    )
    assert jobs.jobs_data[str(ctx.member.id)]["jobs"]["Mineur"] == 20
    assert jobs.jobs_data["3"]["jobs"]["Paysan"] == 100


@pytest.mark.asyncio
async def test_remove_job_targets_owner_and_persists(actions):
    ctx, jobs = actions.ctx, actions.jobs
    await jobs.update_member_job(ctx.guild, ctx.member.id, "Val", "Mineur", 20)
    await jobs.update_member_job(ctx.guild, 3, "Alex", "Mineur", 100)
    ctx.request_text = "Supprime mon métier Mineur"
    result = await remove_own_job(ctx, "Mineur")
    assert result["action_effectuee"]
    assert jobs.jobs_data[str(ctx.member.id)]["jobs"] == {}
    assert jobs.jobs_data["3"]["jobs"]["Mineur"] == 100


@pytest.mark.asyncio
async def test_activity_ambiguity_does_not_mutate_or_reserve(actions):
    ctx, cog = actions.ctx, actions.activities
    ctx.request_text = "Inscris-moi au Crocabulia"
    cog.activities_data["events"]["2"] = {**cog.activities_data["events"]["1"], "id": "2"}
    result = await join_activity(ctx, "Crocabulia")
    assert not result["action_effectuee"]
    assert len(result["a_preciser"]) == 2
    cog.dump_data_to_console.assert_not_awaited()
    ctx.before_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_receipt_precedes_sync_and_duplicate_is_durable(actions):
    ctx, cog = actions.ctx, actions.activities
    ctx.request_text = "Inscris-moi au Crocabulia"

    async def sync(*args):
        assert ctx.action_receipt["action_effectuee"]
        assert ctx.member.id in cog.activities_data["events"]["1"]["participants"]

    cog.sync_membership = sync
    result = await join_activity(ctx, "Crocabulia")
    assert result["action_effectuee"]
    ctx.before_mutation.assert_awaited_once()
    with pytest.raises(EvoError, match="déjà"):
        await join_activity(ctx, "Crocabulia")
    cog.dump_data_to_console.assert_awaited_once()


@pytest.mark.asyncio
async def test_activity_waitlist_and_leave_use_existing_roster(actions):
    ctx, cog = actions.ctx, actions.activities
    event = cog.activities_data["events"]["1"]
    event.update(participants=[3], capacity=1)
    ctx.request_text = "Inscris-moi au Crocabulia"
    result = await join_activity(ctx, "1")
    assert result["liste_attente"]
    ctx.request_text = "Retire-moi de la sortie Crocabulia"
    ctx.trigger_id += 1
    result = await leave_activity(ctx, "1")
    assert result["action_effectuee"]
    assert cog.activities_data["events"]["1"]["waitlist"] == []
    assert cog.activities_data["events"]["1"]["participants"] == [3]


@pytest.mark.asyncio
async def test_activity_permission_is_rechecked_after_reservation(actions):
    ctx, cog = actions.ctx, actions.activities
    ctx.request_text = "Inscris-moi au Crocabulia"

    async def revoke():
        ctx.member.roles = []

    ctx.before_mutation = revoke
    with pytest.raises(EvoError, match="Rôle invalide"):
        await join_activity(ctx, "1")
    cog.dump_data_to_console.assert_not_awaited()
    assert cog.activities_data["events"]["1"]["participants"] == []


@pytest.mark.asyncio
async def test_activity_rechecks_replaced_member_cache_before_write(actions):
    ctx, cog = actions.ctx, actions.activities
    ctx.request_text = "Inscris-moi au Crocabulia"

    async def replace_member():
        replacement = copy.copy(ctx.member)
        replacement.roles = []
        ctx.guild.members = [replacement if member.id == replacement.id else member
                             for member in ctx.guild.members]

    ctx.before_mutation = replace_member
    with pytest.raises(EvoError, match="Rôle invalide"):
        await join_activity(ctx, "1")
    cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ['"Ajoute Mineur 100 à mon profil"',
                                 "'Ajoute Mineur 100 à mon profil'",
                                 "> Ajoute Mineur 100 à mon profil",
                                 "Ajoute Mineur 100 à mon profil si possible",
                                 "Ajoute Mineur 100 à mon profil, par exemple"])
async def test_quoted_and_conditional_requests_cannot_authorize_job_write(actions, text):
    actions.ctx.request_text = text
    with pytest.raises(EvoError):
        await set_own_job(actions.ctx, "Mineur", 100)
    actions.ctx.before_mutation.assert_not_awaited()
    assert actions.console.sends == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("job,level", [("Paysan", 100), ("Mineur", 50)])
async def test_job_parameters_must_match_the_users_own_words(actions, job, level):
    actions.ctx.request_text = "Ajoute Mineur niveau 100 à mon profil"
    with pytest.raises(EvoError):
        await set_own_job(actions.ctx, job, level)
    actions.ctx.before_mutation.assert_not_awaited()
    assert actions.console.sends == 0


@pytest.mark.asyncio
async def test_tool_cannot_pick_an_unrelated_activity_id(actions):
    ctx, cog = actions.ctx, actions.activities
    ctx.request_text = "Inscris-moi au Crocabulia"
    cog.activities_data["events"]["2"] = {**cog.activities_data["events"]["1"], "id": "2", "titre": "Meulou"}
    with pytest.raises(EvoError, match="ne correspond pas"):
        await join_activity(ctx, "2")
    cog.dump_data_to_console.assert_not_awaited()
    ctx.before_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_cannot_resolve_ambiguous_user_activity_by_itself(actions):
    ctx, cog = actions.ctx, actions.activities
    ctx.request_text = "Inscris-moi au Crocabulia"
    cog.activities_data["events"]["2"] = {**cog.activities_data["events"]["1"], "id": "2"}
    result = await join_activity(ctx, "1")
    assert not result["action_effectuee"]
    cog.dump_data_to_console.assert_not_awaited()
    ctx.before_mutation.assert_not_awaited()
