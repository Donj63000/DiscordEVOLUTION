"""Intentions françaises et découpage, sans Discord ni API."""
import pytest

from utils.evo_intents import creation_values
from utils.evo_messages import split_messages, utf16_length


def create(text, **changes):
    args = dict(title="Crocabulia", when="demain 21h", description="", location="",
                capacity=None, duration=None)
    args.update(changes)
    return creation_values(text, **args)


@pytest.mark.parametrize("prefix", ["Crée", "Peux-tu créer", "Tu peux me créer",
                                  "Salut Evo, organise", "Pourrais-tu planifier"])
def test_direct_french_creation(prefix):
    result = create(f"{prefix} une sortie Crocabulia demain à 21h")
    assert result["titre"] == "Crocabulia"
    assert result["date"] == "demain 21h"
    assert result["capacite"] == 8
    assert result["duree"] == 180


@pytest.mark.parametrize("message", [
    "Ne crée pas une sortie Crocabulia demain à 21h",
    "Si possible crée une sortie Crocabulia demain à 21h",
    "Comment créer une sortie Crocabulia demain à 21h",
    "> Crée une sortie Crocabulia demain à 21h",
    "Crée une sortie Crocabulia demain à 21h pour <@123>",
    "Crée une sortie Crocabulia demain à 21h @everyone",
    "Crée une sortie Crocabulia demain à 21h ou vendredi 22h",
    "Crée une sortie Crocabulia sans horaire",
])
def test_no_write_for_ambiguous_conditional_or_quoted_request(message):
    with pytest.raises(ValueError):
        create(message)


@pytest.mark.parametrize("changes", [
    {"title": "Minotot"}, {"description": "Cadeaux offerts"}, {"location": "Bonta"},
    {"when": "vendredi 21h"}, {"capacity": 6}, {"duration": 60},
])
def test_model_cannot_invent_creation_fields(changes):
    with pytest.raises(ValueError):
        create("Crée une sortie Crocabulia demain à 21h", **changes)


@pytest.mark.parametrize("duration", ["pendant 2h30", "durée 2h30", "durée de 150 minutes"])
def test_explicit_capacity_and_duration(duration):
    result = create("Crée une sortie Crocabulia demain à 21h, 6 places, " + duration,
                    capacity=6, duration=150)
    assert result["capacite"] == 6 and result["duree"] == 150


@pytest.mark.parametrize("text", ["Simple.", "Mot " * 1800, "🙂" * 3500,
                                "Début\n```python\n" + "print('ok')\n" * 400 + "```\nFin",
                                "a" * 1889 + "```" + "b" * 5000 + "```"],
                         ids=["simple", "long_text", "emojis", "code_block", "code_boundary"])
def test_messages_fit_discord(text):
    parts = split_messages(text)
    assert all(0 < utf16_length(part) <= 1900 for part in parts)
    if "```" not in text:
        assert "".join(parts).replace(" ", "") == text.replace(" ", "").strip()
    else:
        assert all(part.count("```") % 2 == 0 for part in parts)


def test_citation_stays_clickable_at_boundary():
    url = "https://support.ankama.com/hc/fr/articles/123456-test-retro"
    text = "Texte " * 310 + f"\n[Source officielle]({url})\n" + "Fin " * 500
    parts = split_messages(text)
    assert sum(url in part for part in parts) == 1


def test_long_url_near_boundary_is_never_cut():
    url = "https://example.org/" + "a" * 320
    parts = split_messages("Texte " * 270 + f"[Article]({url})\n" + "Fin " * 300)
    assert any(f"[Article]({url})" in part for part in parts)
