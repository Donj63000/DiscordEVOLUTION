"""Je reproduis les suivis réels Turquoise, PP et choix exacts sans appel payant."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import Transport, answer, config, context
from utils.evo_agent import EvoAgent, MeteredModel, Sessions
from utils.evo_memory import followup_tools, update_brief


def drop_evidence(*, reference="item:739", name="Dofus Turquoise", pp=None, group=None):
    return [{
        "outil": "sources_drop",
        "parametres": json.dumps({"objet": reference, "pp": pp, "pp_groupe": group}),
        "resultat": {
            "objet": name, "reference": reference, "pp_personnelle": pp, "pp_groupe": group,
            "sources_drop": [{"monstre": "Chêne Mou"}, {"monstre": "Dragon Cochon"}],
        },
    }]


def turquoise_brief(*, pp=None, group=None):
    return update_brief({}, "Dofus Turquoise, CM ou DC ?", drop_evidence(pp=pp, group=group),
                        "Le Dofus Turquoise est renseigné sur le Chêne Mou et le Dragon Cochon.")


@pytest.mark.parametrize("question", [
    "Avec515PP et groupe3000PP sur CM", "Avec 515 PP et groupe 3000 PP sur CM",
    "Avec 515 PP personnelles et 3000 PP de groupe sur le Chêne Mou",
    "Et avec 515 PP et 3000 PP groupe sur CM ?",
    "Avec 515 de PP j'ai combien de chance de le drop sur le CM, avec un groupe à 3000PP de groupe",
])
def test_pp_and_group_followup_uses_verified_turquoise_reference(question):
    brief = turquoise_brief()
    original = copy.deepcopy(brief)
    assert followup_tools(brief, question) == [
        ("sources_drop", {"objet": "item:739", "pp": 515, "pp_groupe": 3000}),
    ]
    assert brief == original
    current = update_brief(brief, question, [], "")
    assert current["sources_drop"] == {"objet": "item:739", "pp": 515, "pp_groupe": 3000}
    assert current["drop_context"]["monstres_demandes"] == ["Chêne Mou"]


def test_natural_confirmation_preserves_pp_and_monster_without_fuzzy_choice():
    brief = turquoise_brief(pp=515, group=3000)
    brief = update_brief(brief, "Avec 515 PP et groupe 3000 PP sur CM", [], "")
    question = "Oui le dofus turquoise sur le chêne mou"
    assert followup_tools(brief, question) == [
        ("sources_drop", {"objet": "item:739", "pp": 515, "pp_groupe": 3000}),
    ]
    current = update_brief(brief, question, [], "")
    assert current["drop_context"]["objet"] == "Dofus Turquoise"
    assert current["drop_context"]["monstres_demandes"] == ["Chêne Mou"]


def test_confirming_an_offered_exact_candidate_keeps_declared_constraints():
    evidence = [{
        "outil": "sources_drop",
        "parametres": {"objet": "turquoise", "pp": 515, "pp_groupe": 3000},
        "resultat": {"a_preciser": [
            {"nom": "Amulette Turquoise", "reference": "item:123"},
            {"nom": "Dofus Turquoise", "reference": "item:739"},
        ]},
    }]
    brief = update_brief({}, "Turquoise avec 515 PP et groupe 3000 PP sur CM", evidence,
                         "Dofus Turquoise ou Amulette Turquoise ?")
    assert "sources_drop" not in brief
    assert followup_tools(brief, "Oui le dofus turquoise sur le chêne mou") == [
        ("sources_drop", {"objet": "item:739", "pp": 515, "pp_groupe": 3000}),
    ]
    for text in ("Oui", "Le turquoise", "Le dofus turqoise", "Le premier", "Tu choisis"):
        assert followup_tools(brief, text) == []
    confirmed = update_brief(brief, "Oui le dofus turquoise sur le chêne mou",
                             drop_evidence(pp=515, group=3000), "Voilà pour le Chêne Mou.")
    assert "choix_en_attente" not in confirmed
    assert confirmed["sources_drop"]["objet"] == "item:739"
    assert confirmed["drop_context"]["monstres_demandes"] == ["Chêne Mou"]


@pytest.mark.parametrize("question", [
    "Oui l'amulette turquoise sur le chêne mou", "Avec 515 PP pour une amulette turquoise",
    "Et les plumes de Piou avec 515 PP", "Non, pas le Dofus Turquoise",
    "Le Dofus Turquoise ou l'Amulette Turquoise", 'Il a dit "Dofus Turquoise"',
    "Oui le Dofus Turqoise", "Avec 515 PP sur le tofu", "Avec 515 PP et 600 PP",
    "Avec 10001 PP", "Avec 515 PP et groupe 100001 PP",
])
def test_a_subject_change_or_uncertain_identity_is_not_replaced_by_turquoise(question):
    assert followup_tools(turquoise_brief(), question) == []


@pytest.mark.parametrize("tool", ["fiche_objet", "recette", "chercher_equipements", "monstre"])
def test_another_verified_subject_disables_implicit_old_drop_reference(tool):
    brief = turquoise_brief(pp=515, group=3000)
    switched = update_brief(brief, "Maintenant un autre sujet", [{
        "outil": tool, "parametres": {"objet": "item:123"},
        "resultat": {"objet": "Autre objet", "reference": "item:123"},
    }], "Voici cet autre sujet.")
    assert followup_tools(switched, "Avec 600 PP sur CM") == []


def test_new_drop_object_replaces_old_reference_and_monster_constraints():
    brief = update_brief(turquoise_brief(pp=515, group=3000), "Sur le Chêne Mou", [], "")
    changed = update_brief(brief, "La plume de Tofu", [
        {"outil": "sources_drop", "parametres": {"objet": "plume de Tofu", "pp": None, "pp_groupe": None},
         "resultat": {"objet": "Plume de Tofu", "reference": "item:167", "pp_personnelle": None,
                      "pp_groupe": None, "sources_drop": [{"monstre": "Tofu"}]}},
    ], "Voici la plume.")
    assert changed["drop_context"]["monstres_demandes"] == []
    assert followup_tools(changed, "Avec 600 PP sur le Tofu") == [
        ("sources_drop", {"objet": "item:167", "pp": 600, "pp_groupe": None}),
    ]


def test_pp_group_and_monster_can_change_independently():
    brief = update_brief(turquoise_brief(pp=515, group=3000), "Sur CM", [], "")
    assert followup_tools(brief, "Et avec 600 PP") == [
        ("sources_drop", {"objet": "item:739", "pp": 600, "pp_groupe": 3000}),
    ]
    assert followup_tools(brief, "Et groupe 4000 PP sur DC") == [
        ("sources_drop", {"objet": "item:739", "pp": 515, "pp_groupe": 4000}),
    ]
    current = update_brief(brief, "Sur DC", [], "")
    assert current["drop_context"]["monstres_demandes"] == ["Dragon Cochon"]


def test_parallel_monster_result_does_not_discard_same_turn_drop_context():
    evidence = drop_evidence(pp=515, group=3000)
    evidence.append({"outil": "monstre", "parametres": {"nom": "Chêne Mou"},
                     "resultat": {"monstre": "Chêne Mou"}})
    brief = update_brief({}, "Turquoise avec 515 PP sur CM", evidence, "Voici.")
    assert brief["sujet"] == "sources_drop"
    assert followup_tools(brief, "Et avec 600 PP")[0][1]["objet"] == "item:739"


def test_live_drop_context_remains_small_and_expires_per_member_and_channel():
    now = [0.0]
    sessions = Sessions(config(), clock=lambda: now[0])
    key = (1, 10, 2)
    sessions.save(key, "Dofus Turquoise sur CM", "Voici le Dofus Turquoise.",
                  drop_evidence(pp=515, group=3000), set())
    for i in range(4):
        memory = sessions.save(key, "Merci", f"D'accord {i}", [], set())
    assert len(json.dumps(memory.brief)) < 1500
    assert followup_tools(memory.brief, "Avec 600 PP sur CM")[0][1]["objet"] == "item:739"
    assert followup_tools(sessions.get((1, 20, 2)).brief, "Avec 600 PP sur CM") == []
    assert followup_tools(sessions.get((1, 10, 3)).brief, "Avec 600 PP sur CM") == []
    now[0] = 900
    assert followup_tools(sessions.get(key).brief, "Avec 600 PP sur CM") == []


def test_lv_character_constraint_is_remembered():
    brief = update_brief({}, "Je suis Cra terre lv160", [], "Quel objectif ?")
    assert brief["preferences"] == {"classe": "cra", "element": "terre", "niveau": "160"}


def test_verified_resource_family_supports_exact_colour_but_not_approximation():
    evidence = [{
        "outil": "sources_drop", "parametres": {"objet": "plumes de piou", "pp": 515, "pp_groupe": 3000},
        "resultat": {"famille": "Plumes de Piou", "variantes": [
            {"objet": "Plume de Piou Rouge", "reference": "item:1", "sources_drop": [{"monstre": "Piou Rouge"}]},
            {"objet": "Plume de Piou Vert", "reference": "item:2", "sources_drop": [{"monstre": "Piou Vert"}]},
        ]},
    }]
    brief = update_brief({}, "Plumes de Piou", evidence, "Plume de Piou Rouge et Plume de Piou Vert.")
    for question in ("La rouge", "Oui la plume de piou rouge"):
        assert followup_tools(brief, question) == [
            ("sources_drop", {"objet": "item:1", "pp": 515, "pp_groupe": 3000}),
        ]
    assert followup_tools(brief, "La rouje") == []
    assert followup_tools(brief, "Une amulette rouge") == []
    assert [row["reference"] for row in brief["selection"]] == ["item:1", "item:2"]


def test_new_pending_subject_does_not_reuse_previous_monster_or_pp():
    brief = update_brief(turquoise_brief(pp=515, group=3000), "Sur CM", [], "")
    pending = update_brief(brief, "Quelle plume de Piou ?", [{
        "outil": "sources_drop", "parametres": {"objet": "plume de piou", "pp": None, "pp_groupe": None},
        "resultat": {"a_preciser": [{"nom": "Plume de Piou Rouge", "reference": "item:1"}]}},
    ], "Précise la couleur.")
    current = update_brief(pending, "Oui la Plume de Piou Rouge", [], "")
    assert current["sources_drop"] == {"objet": "item:1", "pp": None, "pp_groupe": None}
    assert "monstres_demandes" not in current["drop_context"]


def test_explicit_equipment_range_survives_followup_and_new_player_level_clears_it():
    brief = update_brief({}, "Je suis Cra terre lv199", [], "D'accord.")
    brief = update_brief(brief, "Des coiffes entre 180 et 199", [], "Deux choix.")
    brief = update_brief(brief, "Avec de la portée", [], "Voici.")
    assert brief["preferences"]["niveau_min_equipement"] == 180
    assert brief["preferences"]["niveau_max_equipement"] == 199
    assert brief["preferences"]["niveau"] == "199"
    changed = update_brief(brief, "Pour mon Cra lv160", [], "D'accord.")
    assert changed["preferences"]["niveau"] == "160"
    assert "niveau_min_equipement" not in changed["preferences"]
    assert "niveau_max_equipement" not in changed["preferences"]


def test_unrelated_number_range_does_not_become_equipment_constraint():
    brief = update_brief({}, "Entre 180 et 199 PP", [], "D'accord.")
    assert "niveau_min_equipement" not in brief.get("preferences", {})


@pytest.mark.parametrize("question", [
    "Des coiffes entre 40 et 60 Force", "Des coiffes de 40 à 60 Force",
    "Des coiffes avec force entre 40 et 60", "Des coiffes exactement 10 PA",
    "Des coiffes entre 40 et 60 PP", "Des coiffes entre 1 et 2 PO",
    "Des coiffes entre 40 et 60 points de Force", "Des coiffes entre 40 et 60 d'agilité",
])
def test_statistic_bounds_do_not_override_equipment_level_range(question):
    brief = update_brief({}, "Des coiffes entre 180 et 199", [], "D'accord.")
    current = update_brief(brief, question, [], "D'accord.")
    assert current["preferences"]["niveau_min_equipement"] == 180
    assert current["preferences"]["niveau_max_equipement"] == 199
    assert "niveau" not in current["preferences"]


@pytest.mark.asyncio
async def test_exact_live_followup_has_current_constraints_and_only_one_ia_writer():
    settings = config()
    budget = await create_budget(settings)
    transport = Transport([answer("Voici le taux du Turquoise sur le Chêne Mou avec ta PP.")])
    ctx = context(settings)
    received = []

    async def execute(name, raw, tool_context, offered):
        received.append((name, json.loads(raw)))
        assert tool_context.conversation_brief["sources_drop"]["pp"] == 515
        assert tool_context.conversation_brief["drop_context"]["monstres_demandes"] == ["Chêne Mou"]
        return drop_evidence(pp=515, group=3000)[0]["resultat"]

    agent = EvoAgent(settings, MeteredModel(settings, budget, transport),
                     SimpleNamespace(execute=AsyncMock(side_effect=execute)))
    key = (ctx.guild.id, ctx.channel.id, ctx.member.id)
    agent.sessions.save(key, "Dofus Turquoise CM/DC", "Chêne Mou ou Dragon Cochon.",
                        drop_evidence(), set())
    question = "Avec 515 de PP j'ai combien de chance de le drop sur le CM, avec un groupe à 3000PP de groupe"
    try:
        result = await agent.answer(ctx, question, 501)
        assert "Turquoise" in result
        assert received == [("sources_drop", {"objet": "item:739", "pp": 515, "pp_groupe": 3000})]
        assert len(transport.calls) == 1
        assert transport.calls[0]["tool_choice"] == "auto"
        assert (await budget.status())["calls"] == 1
        assert (await budget.status())["pending_nano"] == 0
        assert agent.sessions.get(key).brief["drop_context"]["monstres_demandes"] == ["Chêne Mou"]
    finally:
        await budget.close()
