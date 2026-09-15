"""Tours de lecture bornés, cache sémantique et consentement revérifié sans réseau."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests_evo.helpers import context
from tests_evo.test_exo import shared
from utils.evo_agent import EvoAgent
from utils.evo_config import EvoError
from utils.evo_exo import revoke_session, share_session
from utils.evo_tools import EvoTools


def call(name, params, identifier="call_one"):
    return {"name": name, "arguments": json.dumps(params), "call_id": identifier}


def setup_round(ctx=None, executor=None):
    ctx = ctx or context()
    execute = AsyncMock(side_effect=executor, return_value={"verification": "confirmée"})
    agent = EvoAgent(ctx.config, None, SimpleNamespace(execute=execute))
    return ctx, agent, {"tools": 0, "mutation": False}, [], execute


@pytest.mark.asyncio
async def test_json_key_order_and_spacing_share_one_result_across_rounds():
    ctx, agent, state, evidence, execute = setup_round()
    first = call("sources_drop", {"objet": "item:739", "pp": 515, "pp_groupe": 3000}, "first")
    reordered = call("sources_drop", {"pp_groupe": 3000, "pp": 515, "objet": "item:739"}, "second")
    reordered["arguments"] = json.dumps(json.loads(reordered["arguments"]), separators=(",", ":"))
    first_output = await agent.tools_round(ctx, [first], {"sources_drop"}, evidence, state)
    second_output = await agent.tools_round(ctx, [reordered], {"sources_drop"}, evidence, state, readonly=True)

    execute.assert_awaited_once()
    assert state["tools"] == 1
    assert len(evidence) == 1
    assert first_output[0]["call_id"] == "first"
    assert second_output[0]["call_id"] == "second"
    assert first_output[0]["output"] == second_output[0]["output"]
    assert all(isinstance(result, dict) for result in state["tool_results"].values())


@pytest.mark.asyncio
async def test_array_order_and_changed_arguments_remain_distinct_requests():
    ctx, agent, state, evidence, execute = setup_round()
    await agent.tools_round(ctx, [call("comparer_objets", {"objets": ["item:1", "item:2"]})],
                            {"comparer_objets"}, evidence, state)
    await agent.tools_round(ctx, [call("comparer_objets", {"objets": ["item:2", "item:1"]})],
                            {"comparer_objets"}, evidence, state, readonly=True)
    assert execute.await_count == 2
    assert len(evidence) == 2


@pytest.mark.asyncio
async def test_global_tool_limit_allows_cached_results_but_only_five_executions():
    ctx, agent, state, evidence, execute = setup_round()
    await agent.tools_round(ctx, [call("fiche_objet", {"objet": f"item:{index}"}, str(index))
                                 for index in (1, 2, 3)], {"fiche_objet"}, evidence, state)
    result = await agent.tools_round(ctx, [call("fiche_objet", {"objet": f"item:{index}"}, str(index))
                                          for index in (4, 5, 6, 1)],
                                     {"fiche_objet"}, evidence, state, readonly=True)
    assert execute.await_count == state["tools"] == 5
    assert len(evidence) == 5
    assert "Limite d'outils" in json.loads(result[2]["output"])["erreur"]
    assert "erreur" not in json.loads(result[3]["output"])


@pytest.mark.asyncio
@pytest.mark.parametrize("name,params", [
    ("poser_rune", {"rune": "Ra Fo"}),
    ("inscrire_activite", {"activite": "1"}),
    ("desinscrire_activite", {"activite": "1"}),
    ("definir_mon_metier", {"metier": "Tailleur", "niveau": 100}),
    ("supprimer_mon_metier", {"metier": "Tailleur"}),
])
async def test_verification_cannot_execute_or_reuse_a_cached_mutation(name, params):
    ctx, agent, state, evidence, execute = setup_round()
    await agent.tools_round(ctx, [call(name, params)], {name}, evidence, state)
    count = len(evidence)
    output = await agent.tools_round(ctx, [call(name, params, "forbidden")],
                                     {name}, evidence, state, readonly=True)
    assert "uniquement les lectures" in json.loads(output[0]["output"])["erreur"]
    execute.assert_awaited_once()
    assert state["tools"] == 1
    assert len(evidence) == count


@pytest.mark.asyncio
async def test_cache_does_not_bypass_the_current_offered_catalogue():
    ctx, agent, state, evidence, execute = setup_round()
    await agent.tools_round(ctx, [call("guilde", {})], {"guilde"}, evidence, state)
    output = await agent.tools_round(ctx, [call("guilde", {}, "forbidden")],
                                     {"fiche_objet"}, evidence, state, readonly=True)
    assert "Outil non autorisé" in json.loads(output[0]["output"])["erreur"]
    execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ['{"nom":"moi","nom":"Alex"}', '{"nom":NaN}', '{"nom":"moi","extra":1}'])
async def test_invalid_json_is_not_normalized_into_an_executable_request(raw):
    ctx, agent, state, evidence, execute = setup_round()
    output = await agent.tools_round(ctx, [{"name": "membre", "arguments": raw, "call_id": "invalid"}],
                                     {"membre"}, evidence, state)
    assert "erreur" in json.loads(output[0]["output"])
    execute.assert_not_awaited()
    assert state["tools"] == 0
    assert not state["tool_results"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [True, False])
async def test_private_thread_membership_is_rechecked_before_cached_or_new_read(cached):
    from tests_evo.test_channel_access import thread_context

    ctx, _ = thread_context(private=True)
    ctx, agent, state, evidence, execute = setup_round(ctx)
    await agent.tools_round(ctx, [call("guilde", {})], {"guilde"}, evidence, state)
    ctx.channel.fetch_member.side_effect = TimeoutError()
    selected = call("guilde", {}) if cached else call("membre", {"nom": "moi"})
    with pytest.raises(EvoError, match="fil privé"):
        await agent.tools_round(ctx, [selected], {selected["name"]}, evidence, state, readonly=True)
    execute.assert_awaited_once()
    assert len(evidence) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "expire", "revision", "replace_grant"])
async def test_cached_exo_state_keeps_the_exact_original_consent(shared, change):
    ctx, cog, view = shared
    await share_session(ctx)
    tools = EvoTools()
    execute = tools.execute
    tools.execute = AsyncMock(side_effect=execute)
    agent = EvoAgent(ctx.config, None, tools)
    state, evidence = {"tools": 0, "mutation": False}, []
    await agent.tools_round(ctx, [call("ma_session_fm", {})], {"ma_session_fm"}, evidence, state)
    if change == "revoke":
        await revoke_session(ctx)
    elif change == "expire":
        next(iter(cog.evo_shares.values())).expires = 0
    elif change == "revision":
        view.session.revision += 1
    else:
        await share_session(ctx)
        ctx.before_publish = None
    with pytest.raises(EvoError):
        await agent.tools_round(ctx, [call("ma_session_fm", {}, "second")],
                                {"ma_session_fm"}, evidence, state, readonly=True)
    tools.execute.assert_awaited_once()
    assert len(evidence) == 1


@pytest.mark.asyncio
async def test_cancellation_drains_pending_reads_and_never_stores_tasks_in_cache():
    started, completed, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(name, raw, ctx, offered):
        if name == "guilde":
            completed.set()
            return {"nom": "Evolution"}
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    ctx, agent, state, evidence, _ = setup_round(executor=execute)
    task = asyncio.create_task(agent.tools_round(
        ctx, [call("guilde", {}), call("membre", {"nom": "moi"}, "pending")],
        {"guilde", "membre"}, evidence, state,
    ))
    try:
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(completed.wait(), 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert cancelled.is_set()
        assert len(state["tool_results"]) == 1
        assert all(isinstance(result, dict) for result in state["tool_results"].values())
        assert not evidence
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cached_consent_refusal_cancels_reads_already_started_in_this_round():
    started, cancelled = asyncio.Event(), asyncio.Event()
    revoked = False

    async def guard():
        if revoked:
            await asyncio.wait_for(started.wait(), 2)
            raise EvoError("Le consentement du résultat en cache a été révoqué.")

    async def execute(name, raw, ctx, offered):
        if name == "guilde":
            return {"nom": "Evolution"}
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    ctx, agent, state, evidence, _ = setup_round(executor=execute)
    ctx.before_publish = guard
    await agent.tools_round(ctx, [call("guilde", {})], {"guilde"}, evidence, state)
    ctx.before_publish = None
    revoked = True
    with pytest.raises(EvoError, match="révoqué"):
        await agent.tools_round(ctx, [call("membre", {"nom": "moi"}), call("guilde", {}, "cached")],
                                {"guilde", "membre"}, evidence, state, readonly=True)
    assert cancelled.is_set()
    assert len(evidence) == len(state["tool_results"]) == 1
    assert all(isinstance(result, dict) for result in state["tool_results"].values())
