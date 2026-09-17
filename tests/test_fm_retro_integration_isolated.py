"""Transactions originales isolées par AST ; ne remplace pas une recette du SDK Discord."""
from __future__ import annotations

import ast
import asyncio
import copy
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils.exo_engine import D, Item, Rune, Rates, State, attempt
from utils.exo_session import Session, export_session
from utils.fm_retro_limits import HistoryBudget, history_size

ROOT = Path(__file__).resolve().parents[1]


def session_with_history(count=3):
    session = Session.create(Item("Test", "test", {"fo": (0, 50)}, "test"))
    session.rune = Rune("fo")
    session.sim = State({"fo": 10}, D(10000))
    for _ in range(count):
        attempt(session.item, session.sim, session.rune, Rates(0, 0), 129)
    return session


def original_commit():
    tree = ast.parse((ROOT / "exo.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ExoView")
    function = next(node for node in cls.body if getattr(node, "name", None) == "_commit")
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function
    ], type_ignores=[])
    env = {"copy": copy, "asyncio": asyncio, "history_size": history_size,
           "log": logging.getLogger(__name__)}
    exec(compile(ast.fix_missing_locations(module), str(ROOT / "exo.py"), "exec"), env)
    return env["_commit"]


def fake_view(session, budget, publish):
    view = SimpleNamespace(session=session, undo_session=None, retired=False, owner_id=1,
                           evo_uncertain=True, cog=SimpleNamespace(history_budget=budget),
                           rebuild=lambda: None, publish=publish)
    budget.reserve(id(view), history_size(session))
    return view


@pytest.mark.asyncio
async def test_original_commit_reserves_current_and_undo():
    async def publish(_interaction):
        return None
    budget = HistoryBudget()
    initial = session_with_history(3)
    candidate = session_with_history(5)
    view = fake_view(initial, budget, publish)
    await original_commit()(view, None, candidate, undo=True)
    assert view.session is candidate and view.undo_session == initial
    assert view.undo_session is not initial
    assert budget.used == history_size(initial) + history_size(candidate)
    assert candidate.revision == 1 and not view.evo_uncertain


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_original_commit_rolls_back_publication_failure_without_losing_budget(error):
    initial = session_with_history(4)
    budget = HistoryBudget(100000)
    async def publish(_interaction):
        # Un autre atelier consomme tout le budget libre pendant l'attente.
        budget.reserve(2, budget.limit - budget.used)
        raise error("Publication non confirmée")
    view = fake_view(initial, budget, publish)
    before = budget.allocations[id(view)]
    with pytest.raises(error):
        await original_commit()(view, None, session_with_history(1), undo=False)
    assert view.session is initial and view.undo_session is None
    assert budget.allocations[id(view)] == before
    assert budget.used == sum(budget.allocations.values())


@pytest.mark.asyncio
async def test_original_commit_admission_failure_does_not_mutate_or_publish():
    initial = session_with_history(1)
    budget = HistoryBudget(history_size(initial))
    called = False
    async def publish(_interaction):
        nonlocal called
        called = True
    view = fake_view(initial, budget, publish)
    candidate = session_with_history(2)
    with pytest.raises(ValueError):
        await original_commit()(view, None, candidate, undo=True)
    assert view.session is initial and candidate.revision == 0 and not called
    assert budget.used == history_size(initial)


@pytest.mark.asyncio
async def test_original_commit_does_not_resurrect_retired_reservation():
    budget = HistoryBudget()
    async def publish(_interaction):
        view.retired = True
        budget.release(id(view))
    view = fake_view(session_with_history(), budget, publish)
    await original_commit()(view, None, session_with_history(1))
    assert budget.used == 0 and not budget.allocations


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("fm_cli_under_test", ROOT / "tools/fm_retro_validate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_empty_holdout_is_explicit_exit_two(cli, tmp_path):
    target = tmp_path / "result.json"
    assert cli.main(["evaluate", "--output", str(target)]) == 2
    assert json.loads(target.read_text())["status"] == "insufficient_data"


def test_cli_does_not_overwrite_existing_evidence(cli, tmp_path):
    target = tmp_path / "evidence.json"
    target.write_text("DO NOT OVERWRITE", encoding="utf-8")
    assert cli.main(["evaluate", "--output", str(target)]) == 1
    assert target.read_text() == "DO NOT OVERWRITE"
    assert not list(tmp_path.glob(".fm-*"))


def test_cli_rejects_simulated_snapshot_as_observations(cli, tmp_path):
    snapshot, output = tmp_path / "snapshot.json", tmp_path / "corpus.json"
    snapshot.write_bytes(export_session(session_with_history()))
    code = cli.main([
        "collect", str(snapshot), "--session", "s1", "--split", "train",
        "--corpus-id", "capture-test", "--url", "https://synthetic.invalid/proof",
        "--server", "TEST", "--game-version", "1.29.1", "--date", "2026-09-17", "--profession-level", "100",
        "--output", str(output),
    ])
    assert code == 1 and not output.exists()
