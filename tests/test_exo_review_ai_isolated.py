"""Tests des corps Python originaux isoles par AST, sans SDK Discord ni reseau.

Ces tests ne remplacent pas tests_evo/test_exo_review.py : le contexte et le
panneau sont des doublures, pas des objets Discord. Aucune copie du moteur,
du parseur d'arguments, du partage ou de tools_round n'est reimplementee.
"""
import ast
import asyncio
import copy
from dataclasses import dataclass
import inspect
import json
import logging
import math
from pathlib import Path
import re
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest

from utils.dofus_wiki import search_key
from utils.exo_advice import RUNE_NAMES, fm_guide, requested_rune, session_advice
from utils.exo_data import demo_item
from utils.exo_engine import DISCLAIMER, STATS, Item, normalized, weight_text
from utils.exo_feedback import batch_text
from utils.exo_session import Session
from utils.exo_workshop import simulate_batch

ROOT = Path(__file__).resolve().parents[1]


def load_original(monkeypatch, name, path, names=None, env=None, method=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        defined = (
            [node.name] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            else [target.id for target in getattr(node, "targets", []) if isinstance(target, ast.Name)]
        )
        if names is not None and not set(defined).intersection(names):
            continue
        if method and isinstance(node, ast.ClassDef):
            node.body = [child for child in node.body if getattr(child, "name", None) == method]
        nodes.append(node)
    nodes.insert(0, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0))
    module = ModuleType(name)
    monkeypatch.setitem(sys.modules, name, module)
    module.__dict__.update(env or {})
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(ROOT / path), "exec"), module.__dict__)
    return module


@pytest.fixture
def modules(monkeypatch):
    error = load_original(monkeypatch, "_exo_review_error", "utils/evo_config.py", {"EvoError"}).EvoError
    safety = load_original(
        monkeypatch, "utils.evo_safety", "utils/evo_safety.py",
        {"_SECRET", "clean", "compact", "json_text", "bounded_json", "_pairs", "parse_arguments", "validate"},
        {"re": re, "json": json, "math": math, "EvoError": error},
    )
    stat_names = load_original(
        monkeypatch, "_exo_review_stats", "utils/evo_equipment.py", {"STAT_NAMES"},
    ).STAT_NAMES
    tools = load_original(
        monkeypatch, "utils.evo_tools", "utils/evo_tools.py",
        {"text", "number", "choice", "array", "tool", "TOOLS", "BY_NAME", "MUTATING_TOOLS",
         "schemas_for", "requires_evidence"},
        {"STAT_NAMES": stat_names, "search_key": search_key, "re": re,
         "requested_rune": requested_rune, "log": logging.getLogger(__name__)},
    )
    bridge = load_original(monkeypatch, "_exo_review_bridge", "utils/evo_exo.py", env={
        "asyncio": asyncio, "copy": copy, "dataclass": dataclass, "logging": logging, "time": time,
        "EvoError": error, "clean": safety.clean, "DISCLAIMER": DISCLAIMER, "STATS": STATS,
        "normalized": normalized, "weight_text": weight_text, "batch_text": batch_text,
        "simulate_batch": simulate_batch, "RUNE_NAMES": RUNE_NAMES,
        "requested_rune": requested_rune, "session_advice": session_advice,
    })
    agent_module = load_original(
        monkeypatch, "_exo_review_agent", "utils/evo_agent.py", {"EvoAgent"},
        {"asyncio": asyncio, "json": json, "inspect": inspect, "EvoError": error,
         "MUTATING_TOOLS": tools.MUTATING_TOOLS, "bounded_json": safety.bounded_json,
         "json_text": safety.json_text, "log": logging.getLogger(__name__)},
        method="tools_round",
    )
    return SimpleNamespace(error=error, safety=safety, tools=tools, bridge=bridge, Agent=agent_module.EvoAgent)


class PrivateViewDouble:
    """Seule la transaction Discord est doublee ; la pose utilise simulate_batch."""
    def __init__(self, cog):
        self.session = Session.create(demo_item())
        self.session.seed = 1
        self.cog = cog
        self.owner_id, self.guild_id = 2, 1
        self.active, self.evo_uncertain = True, False
        self.message = SimpleNamespace(flags=SimpleNamespace(ephemeral=True))
        self.lock = asyncio.Lock()
        self.evo_requests, self.search_entries = set(), []
        self.commits = 0

    async def commit_private(self, candidate, *, revision):
        assert self.lock.locked()
        assert revision == self.session.revision
        candidate.revision = revision + 1
        self.session = candidate
        self.commits += 1


async def setup_shared(modules):
    cog = SimpleNamespace(closed=False, views={}, evo_shares={})
    bot = SimpleNamespace(get_cog=lambda name: cog if name == "ExoCog" else None)

    async def ensure_access():
        ctx.check()

    ctx = SimpleNamespace(
        bot=bot, guild=SimpleNamespace(id=1), member=SimpleNamespace(id=2),
        channel=SimpleNamespace(id=3), check=lambda: None, ensure_access=ensure_access,
        before_publish=None, before_mutation=None, action_receipt=None,
        request_text="Pose une Ga Pme", trigger_id="test-unique",
    )
    view = PrivateViewDouble(cog)
    cog.views[(1, 2)] = view
    await modules.bridge.share_session(ctx)
    executions = []

    async def execute(name, raw, current, offered):
        executions.append(name)
        if name == "ma_session_fm":
            return await modules.bridge.read_shared_session(current)
        if name == "poser_rune":
            return await modules.bridge.apply_shared_rune(current, json.loads(raw)["rune"])
        raise AssertionError(name)

    agent = modules.Agent()
    agent.config = SimpleNamespace(max_tools=12)
    agent.tools = SimpleNamespace(execute=execute)
    return ctx, cog, view, agent, {"tools": 0, "mutation": False}, [], executions


def call(name, identifier, **params):
    return {"name": name, "arguments": json.dumps(params), "call_id": identifier}


@pytest.mark.parametrize("alias", list(RUNE_NAMES))
def test_every_authorized_alias_routes_from_current_message_even_with_json_memory(modules, alias):
    question = f"Pose une {alias} sur mon objet"
    routing = question + ' {"sujet":"ancien objet","priorites":["force"]}'
    offered = {row["name"] for row in modules.tools.schemas_for(routing, current_request=question)}
    assert {"poser_rune", "ma_session_fm", "guide_fm"} <= offered
    assert modules.tools.requires_evidence(question)


def test_current_request_is_actually_passed_to_router_by_answer():
    tree = ast.parse((ROOT / "utils/evo_agent.py").read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "schemas_for"
        and any(key.arg == "current_request" and isinstance(key.value, ast.Name)
                and key.value.id == "question" for key in node.keywords)
        for node in ast.walk(tree)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("separate_rounds", [False, True])
async def test_read_pose_read_preserves_before_and_returns_latest_after(modules, separate_rounds):
    ctx, _, view, agent, state, evidence, executed = await setup_shared(modules)
    calls = [call("ma_session_fm", "before"), call("poser_rune", "pose", rune="Ga Pme"),
             call("ma_session_fm", "after")]
    offered = {"ma_session_fm", "poser_rune"}
    if separate_rounds:
        outputs = []
        for index, one in enumerate(calls):
            outputs.extend(await agent.tools_round(ctx, [one], offered, evidence, state, readonly=index == 2))
    else:
        outputs = await agent.tools_round(ctx, calls, offered, evidence, state)
    rows = [json.loads(row["output"]) for row in outputs]
    assert [row["revision"] for row in rows] == [0, 1, 1]
    assert rows[0]["jets"] == {"PA": 1}
    assert rows[2]["jets"]["PA"] == 0
    assert rows[1]["resultat"] == "EC"
    assert ctx.action_receipt["revision"] == 1
    assert ctx.action_receipt["resultat"] == "EC"
    assert view.session.sim.attempts == view.commits == 1
    assert executed == ["ma_session_fm", "poser_rune", "ma_session_fm"]
    assert state["tools"] == 3
    assert len(evidence) == 3
    assert evidence[0]["resultat"]["revision"] == 0
    assert evidence[-1]["resultat"]["revision"] == 1
    ctx.before_publish()


@pytest.mark.asyncio
async def test_identical_reads_share_cache_until_a_mutation(modules):
    ctx, _, view, agent, state, evidence, executed = await setup_shared(modules)
    offered = {"ma_session_fm", "poser_rune"}
    calls = [call("ma_session_fm", "a"), call("ma_session_fm", "b"),
             call("poser_rune", "pose", rune="Ga Pme"), call("ma_session_fm", "c"),
             call("ma_session_fm", "d")]
    outputs = await agent.tools_round(ctx, calls, offered, evidence, state)
    assert [json.loads(row["output"])["revision"] for row in outputs] == [0, 0, 1, 1, 1]
    assert len(executed) == state["tools"] == len(evidence) == 3
    assert view.commits == 1


@pytest.mark.asyncio
async def test_duplicate_mutation_uses_receipt_and_readonly_cannot_replay_it(modules):
    ctx, _, view, agent, state, evidence, executed = await setup_shared(modules)
    offered = {"ma_session_fm", "poser_rune"}
    outputs = await agent.tools_round(ctx, [
        call("poser_rune", "first", rune="Ga Pme"), call("ma_session_fm", "read"),
        call("poser_rune", "duplicate", rune="Ga Pme"),
        call("poser_rune", "another", rune="Ga Pa"),
    ], offered, evidence, state)
    assert outputs[0]["output"] == outputs[2]["output"]
    assert "Une seule modification" in json.loads(outputs[3]["output"])["erreur"]
    refused = await agent.tools_round(
        ctx, [call("poser_rune", "readonly", rune="Ga Pme")], offered, evidence, state, readonly=True,
    )
    assert "uniquement les lectures" in json.loads(refused[0]["output"])["erreur"]
    assert executed.count("poser_rune") == view.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["revoke", "revision", "expire", "replace_grant"])
@pytest.mark.parametrize("after_pose", [False, True])
async def test_cache_generation_never_bypasses_real_consent_changes(modules, changed, after_pose):
    ctx, cog, view, agent, state, evidence, _ = await setup_shared(modules)
    offered = {"ma_session_fm", "poser_rune"}
    calls = [call("ma_session_fm", "before")]
    if after_pose:
        calls += [call("poser_rune", "pose", rune="Ga Pme"), call("ma_session_fm", "after")]
    await agent.tools_round(ctx, calls, offered, evidence, state)
    receipt = copy.deepcopy(ctx.action_receipt)
    if changed == "revoke":
        await modules.bridge.revoke_session(ctx)
    elif changed == "revision":
        view.session.revision += 1
    elif changed == "expire":
        next(iter(cog.evo_shares.values())).expires = 0
    else:
        await modules.bridge.share_session(ctx)
        ctx.before_publish = None
    with pytest.raises(modules.error):
        await agent.tools_round(ctx, [call("ma_session_fm", "late")], offered, evidence, state, readonly=True)
    assert view.commits == int(after_pose)
    if receipt:
        assert ctx.action_receipt["action_effectuee"]
        assert "jets" not in ctx.action_receipt


@pytest.mark.asyncio
async def test_tool_budget_does_not_return_an_old_read_when_new_generation_is_exhausted(modules):
    ctx, _, view, agent, state, evidence, executed = await setup_shared(modules)
    agent.config.max_tools = 2
    outputs = await agent.tools_round(ctx, [
        call("ma_session_fm", "before"), call("poser_rune", "pose", rune="Ga Pme"),
        call("ma_session_fm", "after"),
    ], {"ma_session_fm", "poser_rune"}, evidence, state)
    last = json.loads(outputs[-1]["output"])
    assert "Limite d'outils" in last["erreur"]
    assert "revision" not in last
    assert len(executed) == 2
    assert view.commits == 1


@pytest.mark.asyncio
async def test_mutation_waits_for_pending_reads_and_cancellation_drains_tasks(modules):
    ctx, _, _, agent, state, evidence, _ = await setup_shared(modules)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def execute(name, raw, current, offered):
        assert name == "ma_session_fm", "A mutation must not overtake its pending read."
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    agent.tools.execute = execute
    task = asyncio.create_task(agent.tools_round(
        ctx, [call("ma_session_fm", "before"), call("poser_rune", "pose", rune="Ga Pme")],
        {"ma_session_fm", "poser_rune"}, evidence, state,
    ))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() and cancelled.is_set()
        assert not state["mutation"]
        assert not evidence
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("all_stats", [False, True])
def test_advice_contract_fits_the_actual_tool_json_limit(modules, all_stats):
    item = (Item("Toutes les lignes", "fixture", {key: (0, 99) for key in STATS}, "fixture")
            if all_stats else demo_item())
    snapshot = modules.bridge._snapshot(Session.create(item))
    rendered = json.loads(modules.safety.bounded_json(snapshot, 5200))
    assert "erreur" not in rendered
    assert rendered["rune"]["poids_total"] == snapshot["rune"]["poids_total"]
    guide = json.loads(modules.safety.bounded_json(fm_guide("Guide général"), 5200))
    assert "erreur" not in guide
    assert len(guide["runes"]) == 9
