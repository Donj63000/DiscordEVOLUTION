"""Regressions utilisant les vrais modules Discord/agent, sans connexion reseau."""
import copy
import json
from unittest.mock import AsyncMock

import pytest

from exo import ExoModal
from tests_evo.test_exo import shared
from utils.evo_agent import EvoAgent
from utils.evo_config import EvoError
from utils.evo_exo import read_shared_session, revoke_session, share_session
from utils.evo_tools import EvoTools, requires_evidence, schemas_for
from utils.exo_advice import RUNE_NAMES
from utils.exo_engine import Rune


def call(name, identifier, **params):
    return {"name": name, "arguments": json.dumps(params), "call_id": identifier}


@pytest.mark.parametrize("alias", list(RUNE_NAMES))
def test_native_router_offers_fm_for_the_current_request_despite_augmented_history(alias):
    question = f"Pose une {alias} sur mon objet"
    routing = question + ' {"sujet":"équipement","priorites":["force"]}'
    offered = {row["name"] for row in schemas_for(routing, current_request=question)}
    assert {"poser_rune", "ma_session_fm", "guide_fm"} <= offered
    assert requires_evidence(question)


@pytest.mark.asyncio
@pytest.mark.parametrize("separate_rounds", [False, True])
async def test_native_read_pose_read_observes_exactly_one_committed_rune(shared, separate_rounds):
    ctx, _, view = shared
    await share_session(ctx)
    tools = EvoTools()
    tools.execute = AsyncMock(side_effect=tools.execute)
    agent = EvoAgent(ctx.config, None, tools)
    state, evidence = {"tools": 0, "mutation": False}, []
    offered = {"ma_session_fm", "poser_rune"}
    calls = [call("ma_session_fm", "before"), call("poser_rune", "pose", rune="Ga Pme"),
             call("ma_session_fm", "after")]
    if separate_rounds:
        outputs = []
        for index, one in enumerate(calls):
            outputs.extend(await agent.tools_round(ctx, [one], offered, evidence, state, readonly=index == 2))
    else:
        outputs = await agent.tools_round(ctx, calls, offered, evidence, state)
    rows = [json.loads(row["output"]) for row in outputs]
    assert [row["revision"] for row in rows] == [0, 1, 1]
    assert rows[0]["jets"]["PA"] == 1
    assert rows[-1]["jets"]["PA"] == 0
    assert rows[-1]["jets"] == (await read_shared_session(ctx))["jets"]
    assert view.session.sim.attempts == 1
    assert tools.execute.await_count == 3
    view.message.edit.assert_awaited_once()
    assert ctx.action_receipt["revision"] == 1

    await revoke_session(ctx)
    with pytest.raises(EvoError):
        await agent.tools_round(ctx, [call("ma_session_fm", "revoked")], offered, evidence, state, readonly=True)
    assert ctx.action_receipt["action_effectuee"]
    assert "revision" not in ctx.action_receipt
    view.message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_modal_rejects_an_unrelated_impossible_line_atomically(shared):
    _, _, view = shared
    candidate = copy.deepcopy(view.session)
    before = copy.deepcopy(candidate)
    modal = ExoModal(view, "jets")
    try:
        with pytest.raises(ValueError, match="PA=2.*101"):
            modal.apply(candidate, {"jets": "pa=2", "sink": "0", "goal": "pm=1;pa=1", "seed": "1"})
        assert candidate == before
        assert view.session == before
        view.message.edit.assert_not_awaited()
    finally:
        view.modals.discard(modal)
        modal.stop()


@pytest.mark.asyncio
async def test_native_modal_keeps_out_of_profile_observations_explicit(shared):
    _, _, view = shared
    candidate = copy.deepcopy(view.session)
    candidate.mode = "observation"
    modal = ExoModal(view, "jets")
    try:
        modal.apply(candidate, {"jets": "pa=2", "sink": "?", "goal": "pm=1;pa=1", "seed": "1"})
        assert candidate.observed.jets == {"pa": 2}
        assert candidate.sim.jets == {"pa": 1}
        assert "hors profil" in candidate.notice
    finally:
        view.modals.discard(modal)
        modal.stop()


@pytest.mark.asyncio
async def test_native_guide_and_session_expose_total_rune_weight_without_private_fields(shared):
    ctx, _, view = shared
    view.session.rune = Rune("fo", 2)
    view.session.prices["fo:2"] = 987654
    await share_session(ctx)
    tools = EvoTools()
    guide = await tools.do_guide_fm(ctx, "Quel est le poids d'une Ra Fo ?")
    assert guide["runes"][0]["poids_par_point"] == "1"
    assert guide["runes"][0]["poids_total"] == "10"
    snapshot = await tools.do_ma_session_fm(ctx)
    assert snapshot["rune"]["poids_total"] == "10"
    assert snapshot["taux_prochaine_pose"]["source"]
    assert snapshot["referentiel"]["statut"] == "pedagogique_non_calibre"
    assert {"seed", "prices", "budget", "journal", "observations", "export"}.isdisjoint(snapshot)
    assert "987654" not in str(snapshot)
