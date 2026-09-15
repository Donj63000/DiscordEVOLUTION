"""Partage consenti et pose unique sur un vrai atelier, sans interaction simulee."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from exo import ExoCog, ExoView
from tests_evo.helpers import Channel, config, context
from utils.evo_config import EvoError
from utils.evo_exo import (
    SHARE_TTL, apply_shared_rune, clear_member_shares, clear_shares,
    read_shared_session, requested_rune, revoke_session, share_session,
)
from utils.exo_data import demo_item
from utils.exo_engine import Rune, STATS
from utils.exo_session import Session


@pytest_asyncio.fixture
async def shared():
    ctx = context(config(channel_ids=frozenset()))
    cog = ExoCog(ctx.bot)
    ctx.bot.cogs["ExoCog"] = cog
    view = ExoView(cog, ctx.member.id, ctx.guild.id, Session.create(demo_item()))
    view.session.seed = 1
    message = SimpleNamespace(flags=SimpleNamespace(ephemeral=True), attachments=[])
    message.edit = AsyncMock(return_value=message)
    view.message = message
    cog.views[view.key] = view
    ctx.request_text = "Pose une Ga Pme sur ma simulation."
    ctx.trigger_id = "1234"
    ctx.action_receipt = None
    ctx.before_mutation = None
    yield ctx, cog, view
    for current in list(cog.views.values()):
        current.stop()


@pytest.mark.asyncio
async def test_read_requires_explicit_share_and_returns_only_minimal_state(shared):
    ctx, _, view = shared
    view.session.prices = {"pm:0": 987654}
    with pytest.raises(EvoError, match="partagé"):
        await read_shared_session(ctx)
    confirmation = await share_session(ctx)
    assert "objet" not in confirmation
    result = await read_shared_session(ctx)
    assert result["objet"] == view.session.item.name
    assert result["mode"] == "simulation"
    assert result["jets"] == {"PA": 1}
    assert {"seed", "prices", "budget", "journal", "observations", "export"}.isdisjoint(result)
    assert "987654" not in str(result)
    view.message.edit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "guild", "channel", "view", "revision", "expired", "permission"])
async def test_share_is_bound_to_owner_guild_channel_view_revision_and_expiry(shared, monkeypatch, change):
    ctx, cog, view = shared
    await share_session(ctx)
    if change == "owner":
        ctx.member = ctx.guild.get_member(3)
    elif change == "guild":
        view.guild_id += 1
    elif change == "channel":
        ctx.channel = ctx.guild.get_channel(30)
    elif change == "view":
        replacement = ExoView(cog, ctx.member.id, ctx.guild.id, Session.create(demo_item()))
        replacement.message = view.message
        cog.views[view.key] = replacement
        view.stop()
    elif change == "revision":
        view.session.revision += 1
    elif change == "expired":
        grant = next(iter(cog.evo_shares.values()))
        monkeypatch.setattr("utils.evo_exo.time.monotonic", lambda: grant.expires)
    else:
        ctx.channel.denied.add(ctx.member.id)
    with pytest.raises(EvoError):
        await read_shared_session(ctx)
    view.message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_timeout_and_replacement_revoke_share(shared):
    ctx, cog, view = shared
    await share_session(ctx)
    await view.on_timeout()
    assert not cog.evo_shares
    with pytest.raises(EvoError):
        await read_shared_session(ctx)


@pytest.mark.asyncio
async def test_sharing_another_channel_revokes_previous_channel(shared):
    ctx, _, _ = shared
    await share_session(ctx)
    ctx.channel = ctx.guild.get_channel(30)
    await share_session(ctx)
    assert (await read_shared_session(ctx))["revision"] == 0
    ctx.channel = ctx.guild.get_channel(10)
    with pytest.raises(EvoError):
        await read_shared_session(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", ["command", "member", "global", "unload"])
async def test_revocation_removes_access_immediately(shared, revoke):
    ctx, cog, _ = shared
    await share_session(ctx)
    if revoke == "command":
        assert await revoke_session(ctx) == {"partage": False}
    elif revoke == "member":
        clear_member_shares(ctx.bot, ctx.guild.id, ctx.member.id)
    elif revoke == "global":
        clear_shares(ctx.bot)
    else:
        await cog.cog_unload()
    assert not cog.evo_shares
    with pytest.raises(EvoError):
        await read_shared_session(ctx)


@pytest.mark.asyncio
async def test_missing_or_public_private_panel_prevents_share_and_commit(shared):
    ctx, _, view = shared
    view.message.flags.ephemeral = False
    with pytest.raises(EvoError):
        await share_session(ctx)
    view.message.edit.assert_not_awaited()
    view.message = None
    with pytest.raises(EvoError):
        await share_session(ctx)


@pytest.mark.parametrize("text", [
    "Ne pose pas de Ga Pme", "Dois-je poser une Ga Pme ?", "Explique la Ga Pme",
    "Pose deux Ga Pme", "Pose 10 Ga Pme", "Pose une Ga Pme puis une Ga Pa",
    "Pose une Ga Pme sur la simulation de Alex", "Si c'est utile pose une Ga Pme",
    "Il a dit : pose une Ga Pme", '"pose une Ga Pme"', "pose une Ga Pme; recommence",
    "pose une Ga Pme et explique", "pose une Ga Pme\npose une Ga Pme",
])
def test_only_direct_single_rune_requests_are_authorized(text):
    assert requested_rune(text) is None


def test_every_supported_rune_has_unambiguous_direct_request():
    for key, stat in STATS.items():
        for tier in range(len(stat.gains)):
            rune = Rune(key, tier)
            assert requested_rune(f"Peux-tu poser une {rune.name} sur ma simulation stp ?") == rune


@pytest.mark.asyncio
async def test_rune_commits_once_and_private_message_precedes_receipt(shared):
    ctx, cog, view = shared
    await share_session(ctx)
    original = copy.deepcopy(view.session)
    events = []

    async def reserve():
        events.append("reserve")
        assert view.session == original

    async def edit(**kwargs):
        events.append("private")
        assert ctx.action_receipt is None
        assert kwargs["view"] is view
        assert kwargs["allowed_mentions"].everyone is False
        assert view.session.sim.attempts == 1
        return view.message

    ctx.before_mutation = reserve
    view.message.edit.side_effect = edit
    result = await apply_shared_rune(ctx, "Ga Pme")
    assert events == ["reserve", "private"]
    assert result["action_effectuee"] is True
    assert result == ctx.action_receipt
    assert view.session.sim.attempts == 1
    assert view.session.observed == original.observed
    assert view.undo_session == original
    assert view.session.revision == 1
    assert next(iter(cog.evo_shares.values())).revision == 1
    assert (await read_shared_session(ctx))["revision"] == 1
    with pytest.raises(EvoError, match="déjà"):
        await apply_shared_rune(ctx, "Ga Pme")
    ctx.action_receipt = None
    await revoke_session(ctx)
    await share_session(ctx)
    with pytest.raises(EvoError, match="déjà"):
        await apply_shared_rune(ctx, "Ga Pme")
    assert view.session.sim.attempts == 1


@pytest.mark.asyncio
async def test_tool_cannot_choose_another_rune_or_generate_its_own_consent(shared):
    ctx, _, view = shared
    await share_session(ctx)
    with pytest.raises(EvoError):
        await apply_shared_rune(ctx, "Ga Pa")
    ctx.request_text = "Que penses-tu d'une Ga Pme ?"
    with pytest.raises(EvoError):
        await apply_shared_rune(ctx, "Ga Pme")
    ctx.request_text = "Pose une Ga Pme"
    ctx.trigger_id = ""
    with pytest.raises(EvoError):
        await apply_shared_rune(ctx, "Ga Pme")
    assert view.session.sim.attempts == 0
    view.message.edit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["observation", "search"])
async def test_observation_and_search_panels_cannot_be_mutated(shared, mode):
    ctx, _, view = shared
    if mode == "observation":
        view.session.mode = mode
    else:
        view.search_entries = [object()]
    await share_session(ctx)
    with pytest.raises(EvoError):
        await apply_shared_rune(ctx, "Ga Pme")
    assert view.session.sim.attempts == 0
    view.message.edit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("private secret"), asyncio.CancelledError(), None])
async def test_unconfirmed_private_publication_rolls_back_revokes_and_never_replays(shared, failure):
    ctx, cog, view = shared
    await share_session(ctx)
    original = copy.deepcopy(view.session)
    if failure is None:
        view.message.edit.return_value = None
    else:
        view.message.edit.side_effect = failure
    with pytest.raises((EvoError, asyncio.CancelledError)) as caught:
        await apply_shared_rune(ctx, "Ga Pme")
    assert "private secret" not in str(caught.value)
    assert view.session == original
    assert view.undo_session is None
    assert ctx.action_receipt is None
    assert not cog.evo_shares
    assert view.evo_uncertain
    with pytest.raises(EvoError, match="réactualisé"):
        await share_session(ctx)
    view.message.edit.side_effect = None
    view.message.edit.return_value = view.message
    async with view.lock:
        await view.commit_private(copy.deepcopy(view.session), revision=view.session.revision)
    assert not view.evo_uncertain
    await share_session(ctx)
    with pytest.raises(EvoError, match="déjà"):
        await apply_shared_rune(ctx, "Ga Pme")
    assert view.message.edit.await_count == 2


@pytest.mark.asyncio
async def test_permission_and_share_rechecked_after_budget_reservation(shared):
    ctx, _, view = shared
    await share_session(ctx)

    async def revoke():
        clear_member_shares(ctx.bot, ctx.guild.id, ctx.member.id)

    ctx.before_mutation = revoke
    with pytest.raises(EvoError):
        await apply_shared_rune(ctx, "Ga Pme")
    assert view.session.sim.attempts == 0
    view.message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_during_private_edit_retains_only_technical_receipt(shared):
    ctx, _, view = shared
    await share_session(ctx)

    async def edit(**kwargs):
        clear_member_shares(ctx.bot, ctx.guild.id, ctx.member.id)
        return view.message

    view.message.edit.side_effect = edit
    with pytest.raises(EvoError):
        await apply_shared_rune(ctx, "Ga Pme")
    assert view.session.sim.attempts == 1
    assert ctx.action_receipt == {
        "action_effectuee": True, "action": "poser_rune", "panneau_prive_actualise": True,
    }


@pytest.mark.asyncio
async def test_concurrent_duplicate_serializes_and_commits_once(shared):
    ctx, _, view = shared
    await share_session(ctx)
    results = await asyncio.gather(
        apply_shared_rune(ctx, "Ga Pme"), apply_shared_rune(ctx, "Ga Pme"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, EvoError) for result in results) == 1
    assert view.session.sim.attempts == 1
    view.message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_revision_checked_after_waiting_for_view_lock(shared):
    ctx, _, view = shared
    await share_session(ctx)
    async with view.lock:
        pending = asyncio.create_task(apply_shared_rune(ctx, "Ga Pme"))
        await asyncio.sleep(0)
        view.session.revision += 1
    with pytest.raises(EvoError):
        await pending
    assert view.session.sim.attempts == 0
    view.message.edit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "revision", "reshare", "permission", "expiry"])
async def test_publication_callback_rechecks_original_consent(shared, monkeypatch, change):
    ctx, cog, view = shared
    await share_session(ctx)
    await read_shared_session(ctx)
    check = ctx.before_publish
    check()
    if change == "revoke":
        await revoke_session(ctx)
    elif change == "revision":
        view.session.revision += 1
    elif change == "reshare":
        await share_session(ctx)
    elif change == "permission":
        ctx.channel.denied.add(ctx.member.id)
    else:
        grant = next(iter(cog.evo_shares.values()))
        monkeypatch.setattr("utils.evo_exo.time.monotonic", lambda: grant.expires)
    with pytest.raises(EvoError):
        check()


@pytest.mark.asyncio
async def test_action_refreshes_guard_but_later_revocation_redacts_receipt(shared):
    ctx, _, view = shared
    await share_session(ctx)
    await read_shared_session(ctx)
    before = ctx.before_publish
    await apply_shared_rune(ctx, "Ga Pme")
    assert ctx.before_publish is not before
    ctx.before_publish()
    assert ctx.action_receipt["revision"] == view.session.revision
    await revoke_session(ctx)
    with pytest.raises(EvoError):
        ctx.before_publish()
    assert "revision" not in ctx.action_receipt
    assert "resultat" not in ctx.action_receipt
    assert ctx.action_receipt["action_effectuee"] is True


@pytest.mark.asyncio
async def test_private_channel_share_is_explicit_and_isolated_from_other_members_and_channels(shared):
    ctx, cog, view = shared
    ctx.channel = ctx.guild.get_channel(20)
    assert not ctx.channel.permissions_for(ctx.guild.default_role).view_channel
    with pytest.raises(EvoError, match="partagé"):
        await read_shared_session(ctx)
    confirmation = await share_session(ctx)
    assert confirmation["salon_id"] == str(ctx.channel.id)
    assert "objet" not in confirmation
    state = await read_shared_session(ctx)
    assert state["jets"] == {"PA": 1}
    ctx.before_publish()

    other_member = copy.copy(ctx)
    other_member.member = ctx.guild.get_member(3)
    other_view = ExoView(cog, other_member.member.id, ctx.guild.id, Session.create(demo_item()))
    other_view.message = SimpleNamespace(flags=SimpleNamespace(ephemeral=True))
    cog.views[other_view.key] = other_view
    with pytest.raises(EvoError, match="partagé") as caught:
        await read_shared_session(other_member)
    assert view.session.item.name not in str(caught.value)

    other_private = Channel(ctx.guild, 40, "autre-staff", False)
    for channel in (ctx.guild.get_channel(10), other_private):
        elsewhere = copy.copy(ctx)
        elsewhere.channel = channel
        with pytest.raises(EvoError, match="partagé"):
            await read_shared_session(elsewhere)
    assert (await read_shared_session(ctx))["jets"] == state["jets"]
    view.message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_rune_action_works_in_accessible_private_channel(shared):
    ctx, _, view = shared
    ctx.channel = ctx.guild.get_channel(20)
    await share_session(ctx)
    result = await apply_shared_rune(ctx, "Ga Pme")
    assert result["action_effectuee"] is True
    assert view.session.sim.attempts == 1
    assert view.message.flags.ephemeral
    view.message.edit.assert_awaited_once()
    ctx.before_publish()


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["member", "bot"])
async def test_private_share_refuses_read_action_and_publication_after_access_revocation(shared, subject):
    ctx, _, view = shared
    ctx.channel = ctx.guild.get_channel(20)
    await share_session(ctx)
    await read_shared_session(ctx)
    check = ctx.before_publish
    identifier = ctx.member.id if subject == "member" else ctx.guild.me.id
    ctx.channel.denied.add(identifier)
    with pytest.raises(EvoError):
        await read_shared_session(ctx)
    with pytest.raises(EvoError):
        await apply_shared_rune(ctx, "Ga Pme")
    with pytest.raises(EvoError):
        check()
    assert view.session.sim.attempts == 0
    view.message.edit.assert_not_awaited()
