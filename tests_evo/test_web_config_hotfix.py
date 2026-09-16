"""Une option Web mal dimensionnée n'arrête ni Evo ni ses outils métier."""
import json
import os
from unittest.mock import patch
from types import SimpleNamespace as NS

import pytest

from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import config, context, Transport, answer, function, response
from tests_evo.test_upgrade_search_builds import searched
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_config import EvoConfig, EvoError
from utils.evo_search import search_tool


@pytest.mark.parametrize("amount,calls", [("0.015", "3"), ("0.03", "2"), ("0.015", "2")])
def test_web_misconfiguration_preserves_configuration_and_budget(amount, calls, caplog):
    env = {
        "OPENAI_API_KEY": "unit-test-placeholder", "EVO_WEB_SEARCH_ENABLED": "1",
        "EVO_REQUEST_USD": amount, "EVO_MAX_MODEL_CALLS": calls,
    }
    with patch.dict(os.environ, env, clear=True):
        settings = EvoConfig.from_env(guilds=[NS(id=1)])
    assert settings.web_search_enabled is True  # Le choix Staff est conservé, pas réécrit.
    assert settings.request_nano == (15_000_000 if amount == "0.015" else 30_000_000)
    assert settings.max_calls == int(calls)
    assert settings.web_search_unavailable_reason() is not None
    assert "autres fonctions conservées" in caplog.text
    assert "unit-test-placeholder" not in caplog.text


@pytest.mark.parametrize("settings,expected", [
    ({"web_search_enabled": False}, "désactivée"),
    ({"web_search_enabled": True, "request_nano": 15_000_000}, "EVO_REQUEST_USD"),
    ({"web_search_enabled": True, "request_nano": 30_000_000, "max_calls": 2}, "3 générations"),
    ({"web_search_enabled": True, "request_nano": 30_000_000, "max_calls": 3}, None),
])
def test_web_availability_is_explicit(settings, expected):
    reason = config(**settings).web_search_unavailable_reason()
    assert reason is None if expected is None else expected in reason


@pytest.mark.parametrize("env", [
    {"EVO_REQUEST_USD": "invalid"},
    {"EVO_REQUEST_USD": "NaN"},
    {"EVO_REQUEST_USD": "0.03", "EVO_DAILY_USD": "0.02"},
    {"EVO_WEB_SEARCH_ENABLED": "maybe"},
    {"EVO_MAX_MODEL_CALLS": "1"},
])
def test_really_invalid_or_inconsistent_settings_remain_errors(env):
    with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-placeholder", **env}, clear=True):
        with pytest.raises(EvoError):
            EvoConfig.from_env(guilds=[NS(id=1)])


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [
    config(web_search_enabled=True, request_nano=15_000_000),
    config(web_search_enabled=True, request_nano=30_000_000, max_calls=2),
])
async def test_local_tools_still_work_without_offering_unavailable_web(settings):
    budget = await create_budget(settings)
    transport = Transport([
        response([function("guilde", {})]),
        answer("Le serveur compte trois membres Discord."),
    ])
    try:
        ctx = context(settings)
        reply = await EvoAgent(settings, MeteredModel(settings, budget, transport)).answer(
            ctx, "Combien de membres compte notre guilde ?", 801,
        )
        assert "trois" in reply
        assert len(transport.calls) == 2
        assert all(tool.get("name") != "rechercher_web"
                   for payload in transport.calls for tool in payload["tools"])
        assert "Recherche Internet indisponible" in json.dumps(transport.calls[0], ensure_ascii=False)
        assert ctx.web_search is None
        assert all(row.get("web_calls", 0) == 0 for row in budget._state["reservations"].values())
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_provider_rejects_hosted_search_with_insufficient_static_budget_before_counting():
    settings = config(web_search_enabled=True, request_nano=15_000_000)
    budget = await create_budget(settings)
    transport = Transport()
    model = MeteredModel(settings, budget, transport)
    try:
        agent = EvoAgent(settings, model)
        payload = agent.payload(context(settings), [], tools=[search_tool("general")])
        payload.update(max_tool_calls=1, parallel_tool_calls=False, tool_choice="required")
        with pytest.raises(EvoError, match="Recherche Web"):
            await model.generate(payload, "web-denied", "1:2", settings.request_nano)
        assert transport.calls == [] and transport.count_calls == []
        assert (await budget.status())["used_nano"] == 0
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_deep_mode_uses_its_own_generation_limit_without_raising_normal_limit():
    settings = config(web_search_enabled=True, request_nano=40_000_000, max_calls=2, deep_max_calls=3)
    assert settings.web_search_unavailable_reason() is not None
    assert settings.web_search_unavailable_reason(call_limit=3) is None
    budget = await create_budget(settings)
    transport = Transport([
        response([function("rechercher_web", {"requete": "Dofus Retro nouveautés", "perimetre": "dofus_retro"})]),
        searched(),
        answer("Information issue de la recherche."),
    ])
    try:
        await EvoAgent(settings, MeteredModel(settings, budget, transport)).answer(
            context(settings), "Cherche sur Internet les nouveautés de Dofus Rétro.", 802, deepen=True,
        )
        assert any(tool.get("type") == "web_search" for tool in transport.calls[1]["tools"])
        assert len(transport.calls) == 3
        assert settings.max_calls == 2
    finally:
        await budget.close()
