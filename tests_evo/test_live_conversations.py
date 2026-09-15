"""Je reproduis les essais Discord avec sources figées et générations simulées."""
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import Transport, answer, config, context, entry, function, response
from utils.dofus_wiki import WikiDetail
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_config import EvoConfig, EvoError
from utils.evo_equipment import Equipment
from utils.evo_memory import update_brief
from utils.evo_tools import EvoTools, equipment_constraints, prepared_tools, schemas_for
from utils.xixou_api import DropSource, ItemEnrichment


def catalogue_context(entries):
    wiki = NS(client=NS(items=AsyncMock(return_value=tuple(entries))))
    return context(cogs={"DofusWikiCog": wiki})


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Combien de membres sur le discord ?", "Quel est le nombre de personnes sur le serveur ?",
])
async def test_discord_member_count_uses_real_data_and_one_writer(question):
    ctx = context()
    ctx.guild.member_count = 456
    budget = await create_budget(ctx.config)
    transport = Transport([answer("Le serveur compte 456 membres, bots compris.")])
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport))
        rendered = await agent.answer(ctx, question, 800)
        assert "456" in rendered
        assert len(transport.calls) == 1
        assert '"nombre_membres_discord":456' in json.dumps(transport.calls[0], ensure_ascii=False).replace('\\"', '"')
        assert transport.calls[0]["tools"] == []
        assert "guilde" in {row["name"] for row in schemas_for(question)}
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_human_and_bot_counts_require_a_complete_member_cache():
    ctx = context()
    tools = EvoTools()
    assert "nombre_humains" not in await tools.do_guilde(ctx)
    ctx.guild.chunked = True
    result = await tools.do_guilde(ctx)
    assert result["nombre_humains"] == 2
    assert result["nombre_bots"] == 1
    ctx.guild.member_count = 500
    assert "nombre_humains" not in await tools.do_guilde(ctx)


@pytest.mark.asyncio
async def test_job_directory_can_be_enabled_without_other_members_private_profiles():
    ctx = context(config(public_job_data=True))
    ctx.bot.cogs["JobCog"] = NS(initialized=True, jobs_data={
        "2": {"jobs": {"Tailleur": 99}}, "3": {"jobs": {"Tailleur": 100}},
        "987": {"jobs": {"Tailleur": 100}},
    })
    ctx.bot.cogs["ProfilCog"] = NS(store=NS(get_by_owner=AsyncMock()))
    tools = EvoTools()
    result = await tools.do_artisans(ctx, "tailleur", 100)
    assert result["artisans"] == [{"membre": "Alex", "metier": "Tailleur", "niveau": 100}]
    profile = await tools.do_membre(ctx, "Alex")
    assert "profil_declare" not in profile
    ctx.bot.cogs["ProfilCog"].store.get_by_owner.assert_not_awaited()
    ctx.bot.guilds.append(NS(id=7))
    with pytest.raises(EvoError):
        await tools.do_artisans(ctx, "tailleur", 100)


def test_job_access_is_an_explicit_independent_setting():
    env = {"OPENAI_API_KEY": "unit-test-placeholder", "EVO_PUBLIC_JOB_DATA": "1"}
    with patch.dict("os.environ", env, clear=True):
        settings = EvoConfig.from_env(guilds=[NS(id=1)])
    assert settings.public_job_data
    assert not settings.public_member_data
    with patch.dict("os.environ", {"OPENAI_API_KEY": "unit-test-placeholder"}, clear=True):
        assert not EvoConfig.from_env(guilds=[NS(id=1)]).public_job_data


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["le Dofus Turquoise", "Dofus Turquoises", "item:739"])
async def test_article_or_plural_does_not_lose_an_exact_item(query):
    turquoise = entry("739", "Dofus Turquoise", "Dofus", 6)
    ctx = catalogue_context([turquoise, entry("6444", "Amulette Turquoise", "Amulette", 63)])
    selected, ambiguity = await EvoTools().resolve(ctx, query)
    assert selected == turquoise
    assert ambiguity is None


@pytest.mark.asyncio
async def test_real_ambiguity_and_misspellings_still_require_a_choice():
    ctx = catalogue_context([
        entry("739", "Dofus Turquoise", "Dofus", 6),
        entry("6444", "Amulette Turquoise", "Amulette", 63),
    ])
    for query in ("turquoise", "Dofus Turqouise"):
        selected, ambiguity = await EvoTools().resolve(ctx, query)
        assert selected is None
        assert ambiguity["a_preciser"]


@pytest.mark.asyncio
async def test_piou_feather_family_uses_six_verified_variants_not_tofu_or_breeders():
    colours = ["Bleu", "Jaune", "Rose", "Rouge", "Vert", "Violet"]
    feathers = [entry(str(6897 + i), "Plume de Piou " + colour, "Plume", 1)
                for i, colour in enumerate(colours)]
    ctx = catalogue_context(feathers + [
        entry("301", "Plumes de Tofu", "Plume", 4),
        entry("7623", "Caresseur en Plume de Piou Bleu", "Objet d'élevage", 1),
    ])
    tools = EvoTools()

    async def detail(context, selected):
        colour = selected.name.rsplit(" ", 1)[1]
        return WikiDetail(selected, {}, None, False), ItemEnrichment(drops=(
            DropSource("Piou " + colour, "20 %", (), "100", "1", ("Astrub", "Bonta")),
        ))

    tools.detail = AsyncMock(side_effect=detail)
    result = await tools.execute("sources_drop", json.dumps({
        "objet": "Plumes de Piou", "pp": None, "pp_groupe": None,
    }), ctx, {"sources_drop"})
    assert len(result["variantes"]) == 6
    assert {row["reference"] for row in result["variantes"]} == {row.token for row in feathers}
    assert result["zones_communes"] == [
        {"zone": "Astrub", "variantes": 6}, {"zone": "Bonta", "variantes": 6},
    ]
    assert "Tofu" not in json.dumps(result)
    assert "Caresseur" not in json.dumps(result)
    assert tools.detail.await_count == 6


@pytest.mark.parametrize("question,expected", [
    ("cape pour cra lv 200", (1, 200, "")),
    ("cape entre 180 et 199", (180, 199, "")),
    ("cape niveau minimum 190", (190, 200, "")),
    ("cape exactement niveau 200", (200, 200, "")),
    ("cape dont le nom contient terre", (1, 200, "terre")),
])
def test_character_level_is_a_ceiling_but_explicit_filters_are_respected(question, expected):
    ctx = context()
    ctx.request_text = question
    ctx.conversation_brief = {"preferences": {"niveau": "200"}}
    assert equipment_constraints(ctx, 200, 200, "terre") == expected


def test_class_and_element_are_not_invented_name_filters():
    ctx = context()
    ctx.request_text = "Cape pour mon crâ terre"
    assert equipment_constraints(ctx, 1, 200, "cra terre") == (1, 200, "")
    assert equipment_constraints(ctx, 1, 200, "Voile d'encre") == (1, 200, "Voile d'encre")


@pytest.mark.parametrize("question", [
    "Combien de membres du serveur sont paysans 100 ?",
    "Combien de membres du serveur sont inscrits vendredi ?",
    "Combien de membres sur le discord et qui est tailleur ?",
])
def test_qualified_member_questions_keep_tool_planning(question):
    assert prepared_tools(question) == []


@pytest.mark.parametrize("question", [
    "Cape de 40 à 60 force", "Cape avec exactement 10 PA", "Cape avec au moins 30 sagesse",
])
def test_stat_requirements_are_not_equipment_levels(question):
    ctx = context()
    ctx.request_text = question
    assert equipment_constraints(ctx, 1, 200, "") == (1, 200, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Des coiffes avec force entre 40 et 60", "Des coiffes entre 40 et 60 points de Force",
    "Des coiffes entre 40 et 60 d'agilité", "Des coiffes entre 40 et 60 en Force",
    "Des coiffes avec force minimum 40", "Des coiffes avec au moins 40 points de Force",
    "Des coiffes avec force exactement 50", "Des coiffes avec exactement 50 points de Force",
])
async def test_equipment_tool_preserves_level_range_when_statistic_bounds_are_requested(question):
    high = entry("190", "Coiffe Haut Niveau", "Chapeau", 190)
    low = entry("50", "Coiffe Bas Niveau", "Chapeau", 50)
    ctx = catalogue_context([high, low])
    ctx.request_text = question
    previous = update_brief({}, "Des coiffes entre 180 et 199", [], "D'accord.")
    ctx.conversation_brief = update_brief(previous, question, [], "")
    tools = EvoTools()
    tools.equipment.get = AsyncMock(return_value=(
        tuple(Equipment(item, {"fo": (40, 60)}, (), (), (), ()) for item in (high, low)),
        {"objets_indexes": 2},
    ))

    result = await tools.execute("chercher_equipements", json.dumps({
        "type_objet": "coiffes", "niveau_min": 180, "niveau_max": 199,
        "priorites": ["force"], "sans_malus": [], "nom_contient": "",
    }), ctx, {"chercher_equipements"})

    assert result["correspondances"] == 1
    assert [item["reference"] for item in result["resultats"]] == ["item:190"]
    assert ctx.conversation_brief["preferences"]["niveau_min_equipement"] == 180
    assert ctx.conversation_brief["preferences"]["niveau_max_equipement"] == 199


@pytest.mark.asyncio
async def test_family_drop_preserves_unknown_group_threshold():
    entries = [entry("20", "Plume de Piou Rouge", "Plume", 1),
               entry("21", "Plume de Piou Bleu", "Plume", 1)]
    tools = EvoTools()
    tools.detail = AsyncMock(side_effect=lambda ctx, selected: (
        WikiDetail(selected, {}, None, False),
        ItemEnrichment(drops=(DropSource("Piou Test", "20 %", (), "500", "1", ("Zone",)),)),
    ))
    result = await tools.do_sources_drop(catalogue_context(entries), "Plumes de Piou", 435, None)
    for variant in result["variantes"]:
        assert "regle" in variant
        assert variant["sources_drop"][0]["conditionnel"] is True
        assert variant["sources_drop"][0]["seuil_atteint"] is None


def test_family_compaction_never_loses_a_colour_or_threshold_guard():
    source = {"monstre": "Piou", "taux_base": "20 %", "taux_personnel": ["87 %", "87 %"],
              "conditionnel": True, "seuil_atteint": None,
              "zones": ["Zone " + str(i) + " très étendue " * 10 for i in range(16)]}
    value = {"famille": "Plumes de Piou", "nombre_variantes": 6, "variantes": [
        {"objet": "Plume de Piou " + str(i), "reference": f"item:{i + 1}",
         "source": "https://wiki.moon-bot.io/items/piou/", "nombre_sources": 2,
         "sources_drop": [dict(source), dict(source)]} for i in range(6)
    ]}
    encoded = EvoTools().encode_result("sources_drop", value)
    assert len(encoded["variantes"]) == 6
    assert encoded["nombre_variantes"] == 6
    for variant in encoded["variantes"]:
        for retained in variant["sources_drop"]:
            assert retained["conditionnel"] is True
            assert retained["seuil_atteint"] is None
    assert "demander une couleur" in encoded["detail_sources"]


@pytest.mark.asyncio
async def test_cape_for_level_200_finds_level_191_despite_bad_model_filters():
    voile = entry("8876", "Voile d'encre", "Cape", 191)
    ctx = catalogue_context([voile])
    tools = EvoTools()
    tools.equipment.get = AsyncMock(return_value=(
        (Equipment(voile, {"fo": (51, 70), "do": (6, 10), "po": (1, 1)}, (), (), (), ()),),
        {"objets_indexes": 1},
    ))
    budget = await create_budget(ctx.config)
    transport = Transport([
        response([function("chercher_equipements", {
            "type_objet": "capes", "niveau_min": 200, "niveau_max": 200,
            "priorites": ["force", "dommages", "portee"], "sans_malus": [], "nom_contient": "cra terre",
        })]), answer("Le Voile d'encre, niveau 191, est équipable par ton crâ 200."),
    ])
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport), tools)
        agent.sessions.save((1, 10, 2), "Quel item terre pour un crâ ?", "Quel type et niveau ?", [], set())
        await agent.answer(ctx, "cape pour cra lv 200", 801)
        outputs = [item for item in transport.calls[1]["input"] if item.get("type") == "function_call_output"]
        facts = json.loads(outputs[0]["output"])
        assert facts["correspondances"] == 1
        assert facts["resultats"][0]["reference"] == "item:8876"
        assert facts["resultats"][0]["niveau"] == 191
        assert len(transport.calls) == 2
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_specialist_receives_current_character_level():
    ctx = context()
    budget = await create_budget(ctx.config)
    transport = Transport([
        response([function("guilde", {})]), answer("Note spécialiste."), answer("Réponse finale."),
    ])
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport))
        agent.sessions.save((1, 10, 2), "Je suis un crâ lv120", "D'accord.", [], set())
        await agent.answer(ctx, "Approfondis pour un crâ lv200 dans la guilde", 802)
        specialist_data = json.loads(transport.calls[1]["input"][0]["content"])
        assert specialist_data["preferences"]["niveau"] == "200"
        assert len(transport.calls) == 3
    finally:
        await budget.close()
