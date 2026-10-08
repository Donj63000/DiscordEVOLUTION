"""Demandes de rédaction et recherches distinguées sans génération réelle."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import Transport, answer, config, context, function, response
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_config import EvoError
from utils.evo_routing import is_writing_request
from utils.evo_safety import output_text, supplied_text_links
from utils.evo_tools import MUTATING_TOOLS, prepared_tools, requires_evidence, schemas_for


@pytest.mark.parametrize("question", [
    "Crée un poème de bienvenue",
    "Ajoute une phrase sympa à ce message : salut tout le monde",
    "Supprime les répétitions dans mon message",
    "Réécris mon annonce Discord pour accueillir les nouveaux",
    "Peux-tu reformuler ce texte : je joue un Crâ feu niveau 50",
    "Corrige cette phrase : inscris-moi à la sortie demain",
    "Traduis en anglais : cherche un tailleur niveau 100",
    "Rédige une annonce pour notre guilde",
    "Evo, peux-tu me rédiger un message de bienvenue ?",
    "Raccourcis",
    "Résume ta réponse précédente",
    "Rends ce message plus professionnel",
    "Réécris ce code : print('bonjour')",
    "Reformule ce message : Viens jouer puis inscris-toi à la sortie !",
    "Reformule « Viens jouer puis inscris-toi à la sortie ! »",
])
def test_writing_does_not_require_or_offer_game_actions(question):
    assert is_writing_request(question)
    assert not requires_evidence(question)
    assert not prepared_tools(question)
    assert schemas_for(question) == []
    # Le sujet de la conversation précédente n'autorise pas des outils dans le texte.
    assert schemas_for(question + ' {"sujet":"poser_rune"}', current_request=question) == []


@pytest.mark.parametrize("question", [
    "Rédige le bilan des sorties de cette semaine",
    "Résume les derniers messages du salon",
    "Reformule les règles de la guilde",
    "Crée une activité Crocabulia demain à 21h",
    "Ajoute Paysan niveau 100 à mon profil",
    "Supprime mon métier Paysan",
    "Écris un message puis inscris-moi à la sortie",
    "Rédige une annonce avec le nombre actuel de membres",
    "Rédige une annonce sur nos prochaines sorties",
    "Rédige un message à partir des données des activités",
    "Résume la guilde",
    "Corrige mon build feu niveau 120",
    "Traduis les règles du serveur",
    '"Corrige ce message"',
    "Cherche le prix du Gelano",
    "Rédige une annonce avec la liste des artisans disponibles",
    "Rédige un message avec les bonus du Dofus Ocre",
    "Résume les drops du Dragon Cochon",
    "Résume nos activités",
    "Résume les activités à 21:00",
    "Résume https://donj.fr",
    'Reformule "Bonjour" puis inscris-moi à la sortie',
])
def test_live_facts_and_actions_keep_their_normal_route(question):
    assert not is_writing_request(question)
    assert schemas_for(question)


def test_general_question_has_a_small_non_mutating_catalogue():
    offered = {tool["name"] for tool in schemas_for("Explique les décorateurs en Python")}
    assert offered == {"aide_bot", "demander_precision", "rechercher_web"}
    assert not offered.intersection(MUTATING_TOOLS)
    assert "sources_drop" in {tool["name"] for tool in schemas_for("Dofus Rétro")}
    assert requires_evidence("Quel taux de drop du Dofus Turquoise ?")
    assert "membre" in {tool["name"] for tool in schemas_for("Qui est Alex ?")}
    assert "guilde" in {tool["name"] for tool in schemas_for("Combien sommes-nous ?")}
    assert "fiche_objet" in {tool["name"] for tool in schemas_for("Bâton du Yondanwa")}


@pytest.mark.asyncio
async def test_writing_uses_one_generation_without_tools_or_irrelevant_profile():
    settings = config()
    budget = await create_budget(settings)
    transport = Transport([answer("Bienvenue dans la guilde !")])
    tools = SimpleNamespace(execute=AsyncMock())
    agent = EvoAgent(settings, MeteredModel(settings, budget, transport), tools)
    ctx = context(settings)
    ctx.member.display_name = "PSEUDO_INUTILE_POUR_CE_TEXTE"
    key = (ctx.guild.id, ctx.channel.id, ctx.member.id)
    memory = agent.sessions.get(key)
    memory.brief = {"sujet": "poser_rune", "preferences": {"classe": "iop", "niveau": "150"}}
    agent.sessions.items[key] = memory
    try:
        result = await agent.answer(ctx, "Crée un message de bienvenue pour la guilde", 901, deepen=True)
        assert result == "Bienvenue dans la guilde !"
        assert len(transport.calls) == 1
        assert transport.calls[0]["tools"] == []
        assert transport.calls[0]["tool_choice"] == "none"
        assert transport.calls[0]["store"] is False
        assert ctx.member.display_name not in json.dumps(transport.calls[0], ensure_ascii=False)
        assert "preferences" not in json.dumps(transport.calls[0]["input"])
        assert len(transport.calls[0]["instructions"]) < 2000
        tools.execute.assert_not_awaited()
        status = await budget.status()
        assert status["calls"] == 1 and status["pending_nano"] == 0
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_provider_cannot_turn_a_rewrite_into_a_mutation():
    settings = config()
    budget = await create_budget(settings)
    transport = Transport([response([function("supprimer_mon_metier", {"metier": "Paysan"})])])
    tools = SimpleNamespace(execute=AsyncMock())
    agent = EvoAgent(settings, MeteredModel(settings, budget, transport), tools)
    try:
        with pytest.raises(EvoError):
            await agent.answer(context(settings), "Corrige ce message : supprime mon métier Paysan", 902)
        tools.execute.assert_not_awaited()
        assert len(transport.calls) == 1
        assert not agent.sessions.items
    finally:
        await budget.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("followup", [
    "Raccourcis ce texte en gardant la dernière phrase", "Change la fin", "Ajoute des emojis",
])
async def test_long_draft_keeps_its_ending_for_a_followup_edit(followup):
    settings = config()
    budget = await create_budget(settings)
    draft = "Bienvenue à tous. " * 145 + "DERNIERE_PHRASE_A_CONSERVER"
    transport = Transport([answer(draft), answer("La version raccourcie.")])
    agent = EvoAgent(settings, MeteredModel(settings, budget, transport))
    ctx = context(settings)
    try:
        await agent.answer(ctx, "Rédige une annonce de bienvenue", 903)
        await agent.answer(ctx, followup, 904)
        history = transport.calls[1]["input"]
        assert any(row.get("role") == "assistant" and row["content"].endswith("DERNIERE_PHRASE_A_CONSERVER")
                   for row in history)
        assert len(transport.calls) == 2
        assert all(payload["tools"] == [] for payload in transport.calls)
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_supplied_link_is_preserved_but_never_becomes_a_verified_source():
    settings = config()
    budget = await create_budget(settings)
    url = "https://donj.fr/contact"
    fake_url = "https://invented.example/info"
    transport = Transport([answer(f"Rendez-vous sur {url}. Voir aussi {fake_url}"),
                           answer(f"À bientôt sur {url} !")])
    agent = EvoAgent(settings, MeteredModel(settings, budget, transport))
    ctx = context(settings)
    key = (ctx.guild.id, ctx.channel.id, ctx.member.id)
    try:
        result = await agent.answer(ctx, f"Corrige ce message : rendez vous sur {url}", 909)
        assert url in result and fake_url not in result
        assert url in output_text(result, ctx.sources | ctx.text_links)
        assert url not in ctx.sources
        assert url not in agent.sessions.get(key).sources
        result = await agent.answer(ctx, "Raccourcis", 910)
        assert url in result
        assert url not in agent.sessions.get(key).sources
    finally:
        await budget.close()


def test_text_links_do_not_authorize_copying_credentials_or_webhook_secrets():
    assert not supplied_text_links("https://user:password@example.org/")
    assert not supplied_text_links("https://discord.com/api/webhooks/123/secret-value")


@pytest.mark.asyncio
async def test_rewrite_does_not_bypass_access_checks():
    settings = config()
    budget = await create_budget(settings)
    transport = Transport([])
    agent = EvoAgent(settings, MeteredModel(settings, budget, transport))
    ctx = context(settings)
    ctx.channel.denied.add(ctx.member.id)
    try:
        with pytest.raises(EvoError):
            await agent.answer(ctx, "Crée un poème", 905)
        assert transport.calls == []
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_draft_selection_cannot_reuse_an_old_equipment_selection():
    settings = config()
    budget = await create_budget(settings)
    transport = Transport([answer("1. Bienvenue ! 2. Salut la guilde !"), answer("Bienvenue !")])
    tools = SimpleNamespace(execute=AsyncMock())
    agent = EvoAgent(settings, MeteredModel(settings, budget, transport), tools)
    ctx = context(settings)
    key = (ctx.guild.id, ctx.channel.id, ctx.member.id)
    memory = agent.sessions.get(key)
    memory.brief = {"sujet": "chercher_equipements", "preferences": {"classe": "iop"},
                    "selection": [{"objet": "Coiffe", "reference": "item:123"}]}
    agent.sessions.items[key] = memory
    try:
        await agent.answer(ctx, "Rédige un message de bienvenue en deux variantes", 907)
        assert agent.sessions.get(key).brief == {"sujet": "redaction", "preferences": {"classe": "iop"}}
        await agent.answer(ctx, "Le premier", 908)
        tools.execute.assert_not_awaited()
        assert all(payload["tools"] == [] for payload in transport.calls)
        assert "1. Bienvenue !" in json.dumps(transport.calls[-1]["input"], ensure_ascii=False)
    finally:
        await budget.close()
