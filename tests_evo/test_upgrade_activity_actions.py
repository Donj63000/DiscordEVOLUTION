"""Service partagé et adaptateurs IA ; sauvegardes et publication simulées."""
import asyncio
import copy
from unittest.mock import AsyncMock, Mock

import pytest

from tests_evo.test_actions import actions
from utils.evo_actions import create_activity, set_own_job, remove_own_job
from utils.evo_config import EvoError


def configure(actions, text="Crée une sortie Crocabulia demain à 21h, 6 places pendant 2h"):
    ctx, cog = actions.ctx, actions.activities
    ctx.request_text = text
    cog._resolve_organisation_channel = Mock(return_value=ctx.guild.get_channel(30))
    cog.sync_card = AsyncMock(return_value=False)
    cog._sync_legacy_roles = AsyncMock(return_value=False)
    return ctx, cog


async def create(ctx, **changes):
    args = dict(titre="Crocabulia", quand="demain 21h", description="", lieu="",
                capacite=6, duree_minutes=120)
    args.update(changes)
    return await create_activity(ctx, **args)


@pytest.mark.asyncio
async def test_creation_commits_before_receipt_and_publication(actions):
    ctx, cog = configure(actions)
    async def saved(*args, **kwargs):
        assert ctx.action_receipt is None
        assert "2" not in cog.activities_data["events"]
        ctx.before_mutation.assert_awaited_once()
        record = kwargs["payload"]["events"]["2"]
        assert record["participants"] == [ctx.member.id]
        assert record["creator_id"] == ctx.member.id
        assert record["capacity"] == 6 and record["duration_minutes"] == 120
    cog.dump_data_to_console.side_effect = saved
    receipt = await create(ctx)
    assert receipt["action_effectuee"] and receipt["publication_en_attente"]
    assert receipt["id"] == "2" and cog.activities_data["next_id"] == 3
    assert ctx.action_receipt is receipt


@pytest.mark.asyncio
async def test_publication_failure_does_not_hide_saved_creation(actions):
    ctx, cog = configure(actions)
    cog.sync_card.side_effect = RuntimeError("Discord unavailable")
    receipt = await create(ctx)
    assert receipt["action_effectuee"] and receipt["publication_en_attente"]
    assert "2" in cog.activities_data["events"]


@pytest.mark.asyncio
async def test_concurrent_replay_is_idempotent(actions):
    ctx, cog = configure(actions)
    first, second = await asyncio.gather(create(ctx), create(ctx))
    assert first["id"] == second["id"] == "2"
    assert sum(not item["deja_traite"] for item in (first, second)) == 1
    cog.dump_data_to_console.assert_awaited_once()
    ctx.before_mutation.assert_awaited_once()


@pytest.mark.asyncio
async def test_persistence_failure_has_no_false_receipt(actions):
    ctx, cog = configure(actions)
    initial = copy.deepcopy(cog.activities_data)
    cog.dump_data_to_console.side_effect = RuntimeError("write failed")
    with pytest.raises(RuntimeError):
        await create(ctx)
    assert ctx.action_receipt is None and cog.activities_data == initial
    cog.sync_card.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", ["role", "channel", "changed_during_reservation"])
async def test_creation_permissions_checked_before_and_after_reservation(actions, denied):
    ctx, cog = configure(actions)
    if denied == "role":
        ctx.member.roles = []
    elif denied == "channel":
        ctx.guild.get_channel(30).denied.add(ctx.member.id)
    else:
        async def lose_access():
            ctx.member.roles = []
        ctx.before_mutation.side_effect = lose_access
    with pytest.raises(EvoError):
        await create(ctx)
    cog.dump_data_to_console.assert_not_awaited()
    assert ctx.action_receipt is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Comment créer une sortie Crocabulia demain 21h, 6 places pendant 2h ?",
    "Ne crée pas une sortie Crocabulia demain 21h, 6 places pendant 2h",
    "Si on est assez, crée une sortie Crocabulia demain 21h, 6 places pendant 2h",
])
async def test_information_does_not_authorize_creation(actions, text):
    ctx, cog = configure(actions, text)
    with pytest.raises(EvoError):
        await create(ctx)
    ctx.before_mutation.assert_not_awaited()
    cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Peux-tu m'ajouter Tailleur 100 ?", "Tu peux me mettre Tailleur niveau 100 ?",
    "Pourrais-tu ajouter Tailleur 100 à mon profil ?",
])
async def test_natural_own_job_addition(actions, text):
    ctx = actions.ctx
    ctx.request_text = text
    receipt = await set_own_job(ctx, "Tailleur", 100)
    assert receipt["action_effectuee"]
    assert actions.jobs.jobs_data[str(ctx.member.id)]["jobs"]["Tailleur"] == 100


@pytest.mark.asyncio
async def test_natural_own_job_removal(actions):
    ctx = actions.ctx
    ctx.request_text = "Peux-tu m'ajouter Tailleur 100 ?"
    await set_own_job(ctx, "Tailleur", 100)
    ctx.trigger_id += 1
    ctx.request_text = "Peux-tu me retirer mon métier Tailleur ?"
    receipt = await remove_own_job(ctx, "Tailleur")
    assert receipt["action_effectuee"]
    assert "Tailleur" not in actions.jobs.jobs_data[str(ctx.member.id)]["jobs"]


@pytest.mark.asyncio
async def test_past_date_refused_by_shared_service(actions):
    ctx, cog = configure(actions, "Crée une sortie Crocabulia le 15/09/2000 à 21h")
    with pytest.raises(EvoError):
        await create(ctx, quand="15/09/2000 21h", capacite=None, duree_minutes=None)
    cog.dump_data_to_console.assert_not_awaited()
    ctx.before_mutation.assert_not_awaited()
