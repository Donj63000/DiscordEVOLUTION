"""La courte attente Evo garde le salon, les autorisations et les limites communes."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from evo import EvoCog
from tests_evo.helpers import Member, config, context
from utils.evo_agent import Sessions
from utils.evo_config import EvoError
from utils.evo_safety import ToolContext


@pytest_asyncio.fixture
async def queue(monkeypatch):
    monkeypatch.setenv("EVO_ENABLED", "1")
    ctx = context(config(channel_ids=frozenset({10, 30}), cooldown=0))
    ctx.bot.user = ctx.guild.me
    ctx.bot.ensure_evo_leadership = AsyncMock()
    cog = EvoCog(ctx.bot)
    cog.config = ctx.config
    agent = SimpleNamespace(sessions=Sessions(ctx.config), model=SimpleNamespace(close=AsyncMock()))
    cog.agent = agent
    cog.budget = SimpleNamespace(check_ready=AsyncMock(), invalidate=Mock(), close=AsyncMock())
    requests = {}
    tasks = []
    running = set()
    peak = 0

    async def answer(current, question, trigger_id, **kwargs):
        nonlocal peak
        assert current.member.id not in running
        running.add(current.member.id)
        peak = max(peak, len(running))
        assert peak <= 2
        request = requests[trigger_id]
        request.entered.set()
        try:
            await request.release.wait()
            return "Réponse terminée."
        finally:
            running.remove(current.member.id)

    agent.answer = AsyncMock(side_effect=answer)

    def start(trigger_id, question=None, *, member_id=2, channel_id=10):
        current = ToolContext(ctx.bot, ctx.guild, ctx.guild.get_channel(channel_id),
                              ctx.guild.get_member(member_id), cog.config)
        request = SimpleNamespace(entered=asyncio.Event(), release=asyncio.Event(),
                                  acknowledged=asyncio.Event(), messages=[], ctx=current)

        async def send(content):
            request.messages.append(content)
            if "Ta question est en attente" in content:
                request.acknowledged.set()
            return SimpleNamespace(id=trigger_id + 1000)

        request.send = send
        requests[trigger_id] = request
        request.task = asyncio.create_task(cog._run(current, question or f"Question {trigger_id}", trigger_id, send))
        tasks.append(request.task)
        return request

    yield SimpleNamespace(ctx=ctx, cog=cog, agent=agent, start=start)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def observed(event):
    await asyncio.wait_for(event.wait(), 2)


async def finished(*requests):
    await asyncio.wait_for(asyncio.gather(*(request.task for request in requests)), 2)


@pytest.mark.asyncio
async def test_queue_has_one_waiting_question_per_member_and_rejects_duplicates(queue):
    active = queue.start(1, "Question active")
    await observed(active.entered)
    duplicate = queue.start(2, " QUESTION   ACTIVE ")
    await finished(duplicate)
    assert "déjà cette même question" in duplicate.messages[0]
    waiting = queue.start(3, "Question suivante")
    await observed(waiting.acknowledged)
    repeated = queue.start(4, "question suivante")
    refused = queue.start(5, "Troisième question")
    await finished(repeated, refused)
    assert "déjà en attente" in repeated.messages[0]
    assert "déjà une question en attente" in refused.messages[0]
    duplicate_delivery = AsyncMock()
    await queue.cog._run(waiting.ctx, "Question suivante", 3, duplicate_delivery)
    duplicate_delivery.assert_not_awaited()
    assert queue.agent.answer.await_count == 1
    active.release.set()
    await observed(waiting.entered)
    waiting.release.set()
    await finished(active, waiting)
    assert queue.agent.answer.await_count == 2
    assert not queue.cog._waiting


@pytest.mark.asyncio
async def test_two_members_share_two_active_slots_and_two_waiting_slots(queue):
    first, second = queue.start(10), queue.start(11, member_id=3)
    await observed(first.entered)
    await observed(second.entered)
    first_wait, second_wait = queue.start(12), queue.start(13, member_id=3)
    await observed(first_wait.acknowledged)
    await observed(second_wait.acknowledged)
    queue.ctx.guild.members.append(Member(4, "Autre membre"))
    refused = queue.start(14, member_id=4)
    await finished(refused)
    assert "deux camarades" in refused.messages[0]
    assert len(queue.cog._waiting) == 2
    assert queue.cog.budget.check_ready.await_count == 2
    first.release.set()
    await observed(first_wait.entered)
    assert not second_wait.entered.is_set()
    second.release.set()
    await observed(second_wait.entered)
    first_wait.release.set()
    second_wait.release.set()
    await finished(first, second, first_wait, second_wait)
    assert queue.cog.budget.check_ready.await_count == 4
    assert not queue.cog._active
    assert not queue.cog._waiting


@pytest.mark.asyncio
async def test_waiting_during_cooldown_cannot_grow_the_global_queue(queue):
    queue.cog.config = config(cooldown=3600)
    first, second = queue.start(15), queue.start(16, member_id=3)
    await observed(first.entered)
    await observed(second.entered)
    first_wait, second_wait = queue.start(17), queue.start(18, member_id=3)
    await observed(first_wait.acknowledged)
    await observed(second_wait.acknowledged)
    first.release.set()
    await finished(first)
    queue.ctx.guild.members.append(Member(4, "Autre membre"))
    third = queue.start(19, member_id=4)
    await observed(third.entered)
    refused = queue.start(190, member_id=4)
    await finished(refused)
    assert "file d'attente est pleine" in refused.messages[0]
    assert len(queue.cog._waiting) == 2
    assert queue.agent.answer.await_count == 3


@pytest.mark.asyncio
async def test_waiting_request_observes_the_shared_cooldown(queue):
    queue.cog.config = config(cooldown=0.08)
    active = queue.start(20)
    await observed(active.entered)
    waiting = queue.start(21)
    await observed(waiting.acknowledged)
    active.release.set()
    await finished(active)
    await asyncio.sleep(0.01)
    assert not waiting.entered.is_set()
    await observed(waiting.entered)
    waiting.release.set()
    await finished(waiting)
    assert queue.agent.answer.await_count == 2


@pytest.mark.asyncio
async def test_waiting_expiration_never_calls_the_model_and_frees_the_waiting_slot(queue, monkeypatch):
    monkeypatch.setattr("evo.REQUEST_WAIT_SECONDS", 0.03)
    active = queue.start(30)
    await observed(active.entered)
    waiting = queue.start(31)
    await observed(waiting.acknowledged)
    await finished(waiting)
    assert "n'a pas été lancée" in waiting.messages[-1]
    assert queue.agent.answer.await_count == 1
    assert not queue.cog._waiting
    active.release.set()
    await finished(active)


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["permission", "membership", "leadership", "budget", "disabled"])
async def test_waiting_request_rechecks_access_and_readiness_before_any_generation(queue, monkeypatch, revocation):
    active = queue.start(40)
    await observed(active.entered)
    waiting = queue.start(41, channel_id=30)
    await observed(waiting.acknowledged)
    if revocation == "permission":
        waiting.ctx.channel.denied.add(queue.ctx.member.id)
    elif revocation == "membership":
        queue.ctx.guild.members.remove(queue.ctx.member)
    elif revocation == "leadership":
        queue.ctx.bot.ensure_evo_leadership.side_effect = EvoError("Evo attend le prochain leader.")
    elif revocation == "budget":
        queue.cog.budget.check_ready.side_effect = EvoError("Le compteur de budget est indisponible.")
    else:
        monkeypatch.setenv("EVO_ENABLED", "0")
    active.release.set()
    await finished(active, waiting)
    assert queue.agent.answer.await_count == 1
    assert waiting.messages[-1] != "Réponse terminée."
    assert not queue.cog._active
    assert not queue.cog._waiting


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["forget", "suspend", "unload"])
async def test_forget_suspension_and_unload_cancel_active_and_waiting_together(queue, operation):
    active = queue.start(50)
    await observed(active.entered)
    waiting = queue.start(51)
    await observed(waiting.acknowledged)
    if operation == "forget":
        interaction = SimpleNamespace(guild=queue.ctx.guild, user=queue.ctx.member,
                                      response=SimpleNamespace(defer=AsyncMock()),
                                      edit_original_response=AsyncMock())
        await queue.cog.forget.callback(queue.cog, interaction)
    elif operation == "suspend":
        await queue.cog.suspend()
    else:
        await queue.cog.cog_unload()
    assert active.task.cancelled()
    assert waiting.task.cancelled()
    assert "annulée" in waiting.messages[-1]
    assert queue.agent.answer.await_count == 1
    assert not queue.cog._active
    assert not queue.cog._waiting


@pytest.mark.asyncio
async def test_cancelling_waiting_request_does_not_cancel_the_running_answer(queue):
    active = queue.start(60)
    await observed(active.entered)
    waiting = queue.start(61)
    await observed(waiting.acknowledged)
    waiting.task.cancel()
    await asyncio.gather(waiting.task, return_exceptions=True)
    assert not active.task.done()
    assert not queue.cog._waiting
    active.release.set()
    await finished(active)
    assert active.messages == ["Réponse terminée."]


@pytest.mark.asyncio
async def test_leadership_loss_when_leaving_queue_suspends_without_starting_another_generation(queue):
    checks = 0

    async def ensure_leadership():
        nonlocal checks
        checks += 1
        if checks == 3:
            await queue.cog.suspend()
            raise EvoError("Une autre instance a repris le bot.")

    queue.ctx.bot.ensure_evo_leadership.side_effect = ensure_leadership
    active = queue.start(70)
    await observed(active.entered)
    waiting = queue.start(71)
    await observed(waiting.acknowledged)
    active.release.set()
    await finished(active, waiting)

    assert checks == 3
    assert queue.agent.answer.await_count == 1
    assert queue.cog.budget.check_ready.await_count == 1
    queue.cog.budget.invalidate.assert_called_once()
    assert "autre instance" in waiting.messages[-1]
    assert not waiting.task.cancelled()
    assert not queue.cog._active
    assert not queue.cog._waiting
