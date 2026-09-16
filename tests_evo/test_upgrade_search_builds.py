"""Recherche, budget et stuffs ; transport et données de jeu synthétiques."""
import copy
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import jobs_cog, config, context, entry, Transport, response, function, answer
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_budget import quote, validate_snapshot, WEB_SEARCH_NANO_PER_CALL
from utils.evo_builds import suggest, summarize
from utils.evo_config import EvoError
from utils.evo_equipment import Equipment
from utils.evo_search import citation_url, search_result, search_tool
from utils.evo_tools import EvoTools, schemas_for, requires_evidence

SOURCE = "https://support.ankama.com/hc/fr/articles/123456-Dofus-Retro"


def searched(url=SOURCE, text="Information publique vérifiée."):
    return response([
        {"type": "web_search_call", "id": "ws_1", "status": "completed",
         "action": {"type": "search", "query": "Dofus Retro"}},
        {"type": "message", "content": [{"type": "output_text", "text": text,
         "annotations": [{"type": "url_citation", "url": url, "title": "Source Rétro",
                          "start_index": 0, "end_index": len(text)}]}]},
    ])


@pytest.mark.parametrize("url", ["http://example.org", "https://127.0.0.1/x",
    "https://example.org/?token=private", "https://user:password@example.org",
    "https://example.org:8443", "https://machine.local", "https://example.org/a b",
    "https://discord.com/api/webhooks/123/abc"])
def test_unsafe_citations_refused(url):
    assert citation_url(url) == ""


def test_only_provider_citations_become_sources():
    data = search_result(searched(), "dofus_retro")
    assert data["sources"][0]["url"] == SOURCE
    data = searched()
    data["output"][1]["content"][0]["annotations"] = []
    with pytest.raises(EvoError, match="source"):
        search_result(data, "dofus_retro")


def test_retro_scope_enforced_on_citations():
    with pytest.raises(EvoError):
        search_result(searched("https://example.org/"), "dofus_retro")
    assert search_result(searched("https://example.org/"), "general")["sources"]


def test_search_tool_strict_limits_and_no_fake_numeric_token_cap():
    spec = search_tool("dofus_retro")
    assert spec["type"] == "web_search"
    assert spec["search_context_size"] == "low"
    assert "allowed_domains" in spec["filters"]
    assert "return_token_budget" not in spec


@pytest.mark.asyncio
async def test_web_fee_is_durable_and_old_ledger_remains_valid():
    settings = config(request_nano=40_000_000)
    budget = await create_budget(settings)
    try:
        maximum = quote(17000, 3000, 1)
        reservation = await budget.reserve("web:1", "1:2", maximum)
        await budget.mark_submitted(reservation)
        await budget.settle(reservation, 800, 60, web_calls=1)
        assert quote(800, 60, 1) - quote(800, 60) == WEB_SEARCH_NANO_PER_CALL * 115 // 100
        state = copy.deepcopy(budget._state)
        validate_snapshot(state, settings.guild_id)
        assert state["reservations"][reservation]["web_calls"] == 1
        # Restart/reload also validates the new optional fee field.
        budget.invalidate()
        await budget.open()
        assert (await budget.status())["used_nano"] == quote(800, 60, 1)
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_real_agent_web_round_trip_and_no_discord_history_in_search():
    settings = config(web_search_enabled=True, request_nano=40_000_000, max_calls=3)
    budget = await create_budget(settings)
    transport = Transport([
        response([function("rechercher_web", {"requete": "Dofus Retro nouveautés",
                                             "perimetre": "dofus_retro"})]),
        searched(),
        answer("Voici l'information trouvée."),
    ])
    ctx = context(settings)
    try:
        result = await EvoAgent(settings, MeteredModel(settings, budget, transport)).answer(
            ctx, "Cherche sur internet les nouveautés Dofus Rétro.", 500,
        )
        assert SOURCE in result  # deterministic citation fallback
        assert len(transport.calls) == 3
        hosted = transport.calls[1]
        assert hosted["max_tool_calls"] == 1 and hosted["parallel_tool_calls"] is False
        assert ctx.member.display_name not in json.dumps(hosted, ensure_ascii=False)
        assert "Evolution Test" not in json.dumps(hosted, ensure_ascii=False)
        assert ctx.web_search is None
        assert sum(row.get("web_calls", 0) for row in budget._state["reservations"].values()) == 1
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_general_stable_answer_does_not_require_fake_game_tool():
    settings = config()
    budget = await create_budget(settings)
    transport = Transport([answer("Une variable garde une valeur dans un programme.")])
    try:
        result = await EvoAgent(settings, MeteredModel(settings, budget, transport)).answer(
            context(settings), "Explique une variable en programmation.", 501,
        )
        assert "variable" in result
        assert transport.calls[0]["tool_choice"] == "auto"
        assert all(tool.get("name") != "rechercher_web" for tool in transport.calls[0]["tools"])
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_discord_snowflake_not_sent_to_search():
    ctx = context(config(web_search_enabled=True))
    ctx.web_search = AsyncMock()
    with pytest.raises(EvoError):
        await EvoTools().do_rechercher_web(ctx, "profil 123456789012345678", "general")
    ctx.web_search.assert_not_awaited()


def test_duplicate_call_identifiers_rejected_before_execution():
    with pytest.raises(EvoError, match="dupliqu"):
        EvoAgent.response_calls(response([function("guilde", {}), function("aide_bot", {})]))


@pytest.mark.parametrize("question, expected", [
    ("Des tailleurs 100 ?", "artisans"), ("Des boulangers 100 ?", "artisans"),
    ("Quels mobs combattre ?", "monstre"), ("Propose un stuff feu niveau 120", "proposer_stuff"),
    ("Crée une activité demain 21h", "creer_activite"),
])
def test_intent_routes(question, expected):
    assert expected in {spec["name"] for spec in schemas_for(question)}
    assert requires_evidence(question)


def item(number, category, stats, *, level=100, unknown=()):
    return Equipment(entry(str(number), f"Objet {number}", category, level), stats,
                     unknown, (), (), ())


def test_build_sums_negative_effects_and_keeps_two_distinct_rings():
    rows = [
        item(1, "Chapeau", {"ine": (20, 30), "fo": (-20, -10)}),
        item(2, "Anneau", {"ine": (10, 15)}), item(3, "Anneau", {"ine": (5, 10)}),
        item(4, "Cape", {"ine": (1000, 2000)}, level=101),
    ]
    result = suggest(rows, level=100, priorities=["intelligence", "sagesse"], no_malus=[])
    assert result["nombre_pieces"] == 3
    assert result["somme_jets_interpretes"]["Intelligence"] == [35, 55]
    assert result["somme_jets_interpretes"]["Force"] == [-20, -10]
    assert len({row["reference"] for row in result["pieces"]}) == 3
    assert result["equipabilite_confirmee"] is False


def test_analysis_flags_unknown_effects_instead_of_asserting_full_totals():
    result = summarize([item(1, "Cape", {"vi": (20, 30)}, unknown=("effet inconnu",))], level=100)
    assert result["somme_partielle"] is True


@pytest.mark.parametrize("rows", [
    [item(1, "Cape", {"vi": (1, 2)}), item(2, "Cape", {"vi": (1, 2)})],
    [item(1, "Cape", {"vi": (1, 2)}, level=101)],
    [item(1, "Cape", {"vi": (1, 2)}), item(1, "Cape", {"vi": (1, 2)})],
])
def test_analysis_does_not_sum_invalid_lists(rows):
    with pytest.raises(EvoError):
        summarize(rows, level=100)


def test_suggestion_preserves_all_eight_slots():
    rows = [item(i, category, {"ine": (10, 20)})
            for i, category in enumerate(["Chapeau", "Cape", "Amulette", "Ceinture",
                                          "Botte", "Anneau", "Anneau", "Bâton"], 1)]
    value = suggest(rows, level=100, priorities=["intelligence"], no_malus=[])
    encoded = EvoTools().encode_result("proposer_stuff", value)
    assert len(encoded["pieces"]) == 8
    assert encoded["emplacements_manquants"] == {}


@pytest.mark.asyncio
async def test_plural_artisan_query_is_exact_and_reports_unverified_members():
    ctx = context(config(public_job_data=True))
    ctx.bot.cogs["JobCog"] = jobs_cog({
        "2": {"jobs": {"Tailleur": 100}}, "3": {"jobs": {"Tailleur": 80}},
        "777": {"jobs": {"Tailleur": 100}},
    })
    result = await EvoTools().do_artisans(ctx, "tailleurs", 100)
    assert result["total"] == 1
    assert result["verification_complete"] is False
    assert result["declarations_non_verifiees"] == 1


@pytest.mark.asyncio
async def test_two_successive_read_rounds_keep_one_writer_reservation():
    settings = config(max_calls=5, max_tools=8, request_nano=40_000_000)
    budget = await create_budget(settings)
    tools = EvoTools()
    tools.do_monstre = AsyncMock(side_effect=[
        {"monstre": "Boss test", "page": 1}, {"monstre": "Boss test", "page": 2},
        {"monstre": "Boss test", "page": 3},
    ])
    transport = Transport([
        response([function("monstre", {"nom": "Boss test", "page": 1}, "one")]),
        response([function("monstre", {"nom": "Boss test", "page": 2}, "two")]),
        response([function("monstre", {"nom": "Boss test", "page": 3}, "three")]),
        answer("Voici les informations des trois pages."),
    ])
    try:
        result = await EvoAgent(settings, MeteredModel(settings, budget, transport), tools).answer(
            context(settings), "Détaille les drops du monstre Boss test.", 900,
        )
        assert tools.do_monstre.await_count == 3
        assert "trois pages" in result and len(transport.calls) == 4
        assert (await budget.status())["pending_nano"] == 0
    finally:
        await budget.close()


def test_environment_accepts_explicit_web_budget_and_longer_answers(monkeypatch):
    from utils.evo_config import EvoConfig
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    monkeypatch.setenv("EVO_WEB_SEARCH_ENABLED", "1")
    monkeypatch.setenv("EVO_REQUEST_USD", "0.03")
    monkeypatch.setenv("EVO_MAX_OUTPUT_TOKENS", "1000")
    monkeypatch.setenv("EVO_MAX_MODEL_CALLS", "5")
    monkeypatch.setenv("EVO_MAX_TOOL_CALLS", "10")
    settings = EvoConfig.from_env(guilds=[context().guild])
    assert settings.web_search_enabled and settings.max_output == 1000
    assert settings.max_calls == 5 and settings.max_tools == 10
    monkeypatch.setenv("EVO_REQUEST_USD", "0.015")
    settings = EvoConfig.from_env(guilds=[context().guild])
    assert settings.request_nano == 15_000_000  # Aucun consentement budgétaire implicite.
    assert "EVO_REQUEST_USD" in settings.web_search_unavailable_reason()
