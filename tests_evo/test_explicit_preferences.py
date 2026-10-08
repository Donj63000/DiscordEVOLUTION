"""La mémoire suit les contraintes déclarées, pas les mots d'un texte à expliquer."""
import copy

import pytest

from utils.evo_memory import followup_tools, update_brief


@pytest.fixture
def remembered():
    return {
        "preferences": {"classe": "iop", "element": "terre", "niveau": "150", "usage": "pvm"},
        "sujet": "chercher_equipements",
    }


@pytest.mark.parametrize("question", [
    "Peux-tu expliquer les dégâts feu d'un monstre ?",
    "Explique les avantages d'un Cra air niveau 200",
    "Compare les avantages d'un Iop terre et d'un Cra air",
    "Je ne veux pas feu, je reste terre",
    "Ne pars pas sur un Cra, je joue un Iop",
    "Je joue pas Cra",
    "Je serais Cra feu si je changeais de personnage",
    "Si je joue Cra feu niveau 180, quel stuff me faudrait-il ?",
    "Si je reroll, je suis Cra feu niveau 180",
    "Je joue Iop ou Cra",
    "Je joue Iop terre et Cra air",
    "Je joue Iop terre, et mon ami joue Cra feu niveau 180",
    "Mon ami dit je suis Cra feu niveau 180",
    'Un ami dit "Je suis Cra feu niveau 180"',
    '"Je suis Cra feu niveau 180"',
    "'Je suis Cra feu niveau 180'",
    "“Je suis Cra feu niveau 180”",
    "‘Je suis Cra feu niveau 180’",
    "« Je suis Cra feu niveau 180 »",
    "> Je suis Cra feu niveau 180",
    "```text\nJe suis Cra feu niveau 180\n```",
    "`Je suis Cra feu niveau 180`",
    "Corrige ce message : je joue un Cra feu niveau 50",
    "Réécris mon annonce Discord : je joue un Cra feu niveau 50",
    "Traduis : Je suis Cra feu niveau 50",
    "Voici le message de mon ami :\nJe suis Cra feu niveau 50",
    "999",
    "Niveau 999",
    "Niveau 40 PP",
])
def test_unrelated_quoted_negated_or_ambiguous_text_does_not_overwrite_profile(remembered, question):
    original = copy.deepcopy(remembered)
    assert update_brief(remembered, question, [], "")["preferences"] == original["preferences"]
    assert remembered == original


@pytest.mark.parametrize(("question", "expected"), [
    ("Je suis Cra terre lv160", {"classe": "cra", "element": "terre", "niveau": "160"}),
    ("Je joue un Iop air niveau 150 en PvM avec 10 PA et 6 PM",
     {"classe": "iop", "element": "air", "niveau": "150", "usage": "pvm", "pa": "10", "pm": "6"}),
    ("Pour mon Cra lv160", {"classe": "cra", "niveau": "160"}),
    ("Mon personnage est un Cra terre lv160", {"classe": "cra", "element": "terre", "niveau": "160"}),
    ("Cra terre 150", {"classe": "cra", "element": "terre", "niveau": "150"}),
    ("cape pour cra lv 200", {"classe": "cra", "niveau": "200"}),
    ("Approfondis pour un crâ lv200 dans la guilde", {"classe": "cra", "niveau": "200"}),
    ("Trouve-moi une coiffe terre niveau 120", {"element": "terre", "niveau": "120"}),
    ("Niveau 160", {"niveau": "160"}),
    ("Terre", {"element": "terre"}),
    ("PVP", {"usage": "pvp"}),
    ("Avec 10 PA et 6 PM", {"pa": "10", "pm": "6"}),
    ("Iop terre 10 PA", {"classe": "iop", "element": "terre", "pa": "10"}),
])
def test_explicit_profile_and_short_constraints_are_remembered(question, expected):
    assert update_brief({}, question, [], "").get("preferences", {}) == expected


def test_current_level_replaces_past_level_and_clears_old_equipment_range(remembered):
    remembered["preferences"].update(niveau_min_equipement=130, niveau_max_equipement=150)
    current = update_brief(remembered, "J'étais niveau 150 mais maintenant niveau 160", [], "")
    assert current["preferences"] == {
        "classe": "iop", "element": "terre", "niveau": "160", "usage": "pvm",
    }


def test_real_declaration_outside_a_quote_is_kept(remembered):
    current = update_brief(remembered, 'Je suis Iop terre niveau 160. Il dit "Cra feu niveau 50".', [], "")
    assert current["preferences"]["niveau"] == "160"
    assert current["preferences"]["classe"] == "iop"
    assert current["preferences"]["element"] == "terre"


def test_explicit_change_of_character_does_not_invent_its_level_or_element(remembered):
    current = update_brief(remembered, "Je joue maintenant un Cra", [], "")
    assert current["preferences"] == {"classe": "cra", "usage": "pvm"}


@pytest.mark.parametrize("question", [
    "Des coiffes entre 180 et 199",
    "Trouve-moi des coiffes niveaux 180-199",
])
def test_equipment_range_stays_distinct_from_character_level(remembered, question):
    current = update_brief(remembered, question, [], "")
    assert current["preferences"]["niveau"] == "150"
    assert current["preferences"]["niveau_min_equipement"] == 180
    assert current["preferences"]["niveau_max_equipement"] == 199


@pytest.mark.parametrize("question", [
    "Trouve des coiffes entre 40 et 60 Force",
    "Des coiffes entre 40 et 60 points de Force",
    "Trouve des coiffes avec force entre 40 et 60",
    "Des coiffes exactement 10 PA",
])
def test_statistic_constraints_do_not_replace_equipment_range(remembered, question):
    previous = update_brief(remembered, "Des coiffes entre 180 et 199", [], "")
    current = update_brief(previous, question, [], "")
    assert current["preferences"]["niveau_min_equipement"] == 180
    assert current["preferences"]["niveau_max_equipement"] == 199
    assert current["preferences"]["niveau"] == "150"


def test_writing_does_not_break_verified_drop_followup(remembered):
    previous = update_brief(remembered, "Turquoise avec 515 PP sur CM", [{
        "outil": "sources_drop",
        "parametres": {"objet": "item:739", "pp": 515, "pp_groupe": None},
        "resultat": {"objet": "Dofus Turquoise", "reference": "item:739",
                     "pp_personnelle": 515, "sources_drop": [{"monstre": "Chêne Mou"}]},
    }], "Voici les données du Dofus Turquoise sur le Chêne Mou.")
    current = update_brief(previous, "Corrige : je suis Cra feu niveau 50", [], "Je suis Crâ feu niveau 50.")
    assert current["preferences"] == previous["preferences"]
    assert followup_tools(current, "Avec 600 PP sur CM") == [
        ("sources_drop", {"objet": "item:739", "pp": 600, "pp_groupe": None}),
    ]
