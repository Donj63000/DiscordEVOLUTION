"""Les nombres ci-dessous sont des pourcentages, pas des probabilités entre 0 et 1."""

from decimal import Decimal, localcontext

import pytest

from utils.drop_calculator import (
    DropRule, ProspectingSettings, format_percent, item_drop_rule,
    parse_rate, parse_settings, personal_rate, rate_bounds, source_count, threshold_met,
)
from utils.xixou_api import DropSource


@pytest.mark.parametrize("base,expected", [
    ("10", "43.5"), ("11", "47.85"), ("12", "52.2"),
    ("13", "56.55"), ("14", "60.9"), ("0.001", "0.00435"),
])
def test_personal_435_pp_uses_total_pp_without_adding_the_base_again(base, expected):
    assert personal_rate(Decimal(base), ProspectingSettings(435)) == Decimal(expected)


@pytest.mark.parametrize("pp,base,expected", [
    (100, "14", "14"), (120, "14", "16.8"), (0, "14", "0"),
    (435, "0", "0"), (435, "30", "100"), (10000, "100", "100"),
    (1, "0.000000000000000001", "0.00000000000000000001"),
])
def test_baseline_enutrof_zero_cap_and_smallest_supported_source(pp, base, expected):
    assert personal_rate(Decimal(base), ProspectingSettings(pp)) == Decimal(expected)


def test_arithmetic_and_formatting_do_not_depend_on_the_global_decimal_precision():
    with localcontext() as context:
        context.prec = 3
        result = personal_rate(Decimal("0.001"), ProspectingSettings(435))
        assert result == Decimal("0.00435")
        assert format_percent(result) == "0,00435 %"
        assert format_percent(Decimal("100.0000")) == "100 %"
        assert format_percent(Decimal("0.000")) == "0 %"


def test_group_pp_unlocks_the_threshold_but_never_multiplies_personal_rate():
    settings = ProspectingSettings(435, 2000)
    assert threshold_met(settings, "1000") is True
    assert personal_rate(Decimal("1"), settings) == Decimal("4.35")


@pytest.mark.parametrize("pp,group,required,expected", [
    (435, None, "100", True), (435, None, "435", True),
    (435, None, "1000", None), (435, 435, "1000", False),
    (435, 999, "1000", False), (435, 1000, "1000", True),
    (0, 0, "0", True), (435, None, "", None),
    (435, 1000, "inconnu", None), (435, 1000, "-1", None),
])
def test_group_threshold_has_three_distinct_states(pp, group, required, expected):
    assert threshold_met(ProspectingSettings(pp, group), required) is expected


@pytest.mark.parametrize("personal,group,expected", [
    ("435", "", ProspectingSettings(435)),
    (" 435 ", " 1000 ", ProspectingSettings(435, 1000)),
    ("00000", "000000", ProspectingSettings(0, 0)),
    ("10000", "100000", ProspectingSettings(10000, 100000)),
    ("", "", None), ("  ", " ", None),
])
def test_form_parsing_and_explicit_reset(personal, group, expected):
    assert parse_settings(personal, group) == expected


@pytest.mark.parametrize("personal,group", [
    ("", "1000"), ("435", "434"), ("435.5", ""), ("435,5", ""),
    ("-1", ""), ("+435", ""), ("1e3", ""), ("nan", ""), ("∞", ""),
    ("٤٣٥", ""), ("４３５", ""), ("435 PP", ""), ("10001", ""),
    ("123456", ""), ("435", "100001"), ("435", "-1"), ("435", "4.35"),
    ("435", "1 000"), ("435", "@everyone"), ("435", "0" * 500),
])
def test_invalid_form_is_not_silently_clamped_or_converted_to_zero(personal, group):
    with pytest.raises(ValueError):
        parse_settings(personal, group)


@pytest.mark.parametrize("personal,group", [
    (True, None), (435.0, None), (-1, None), (10001, None),
    (435, False), (435, 1000.0), (435, 434), (435, 100001),
])
def test_programmatic_settings_preserve_the_same_invariants(personal, group):
    with pytest.raises(ValueError):
        ProspectingSettings(personal, group)


@pytest.mark.parametrize("raw,expected", [
    ("10%", "10"), (" 0,001 % ", "0.001"), ("0", "0"),
    (0, "0"), (0.03, "0.03"), (Decimal("1.2"), "1.2"),
    ("1e-18%", "1e-18"), ("1E+2", "100"),
])
def test_source_rate_parsing(raw, expected):
    assert parse_rate(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [
    None, "", True, False, "-1%", "101%", "NaN", float("nan"),
    float("inf"), "Infinity", "1e-19", "1e-9999999", "1e999",
    "1/100", "1-2%", "１%", "0,1.2%", "0." + "1" * 80,
    "à vérifier", "5% (à vérifier)", {}, [],
])
def test_invalid_source_rate_is_missing_not_zero(raw):
    assert parse_rate(raw) is None


@pytest.mark.parametrize("raw", [Decimal("-1"), Decimal("101"), Decimal("NaN")])
def test_direct_calculation_rejects_invalid_source(raw):
    with pytest.raises(ValueError):
        personal_rate(raw, ProspectingSettings(435))


@pytest.mark.parametrize("raw,expected", [
    ("1000", 1000), (" 0 ", 0), (0, 0),
    ("∞", None), ("", None), (None, None), (True, None),
    ("1.5", None), ("1e3", None), ("-1", None), ("1" * 10, None),
])
def test_source_counts_are_never_mistaken_for_rates(raw, expected):
    assert source_count(raw) == expected


@pytest.mark.parametrize("aggregate", [
    "10% – 14%", "10–14 %", "10% — 14%", "10% à 14%", "10-14%",
])
def test_partial_rank_information_cannot_hide_wider_aggregate_bounds(aggregate):
    source = DropSource("Blop", rate=aggregate, level_rates=(("155", "11%"),))
    assert rate_bounds(source) == (Decimal(10), Decimal(14))


def test_bounds_preserve_scientific_notation_and_do_not_interpolate_missing_rates():
    assert rate_bounds(DropSource("Rare", rate="1e-3%")) == (Decimal("0.001"),) * 2
    assert rate_bounds(DropSource("Rare", rate="1e-3%-1e-2%")) == (
        Decimal("0.001"), Decimal("0.01"),
    )
    source = DropSource("Blop", rate="inconnu",
                        level_rates=(("150", "10%"), ("155", ""), ("160", "12%")))
    assert rate_bounds(source) == (Decimal(10), Decimal(12))
    assert rate_bounds(DropSource("Inconnu")) is None
    assert rate_bounds(DropSource("Inversé", rate="14% – 10%")) is None
    assert rate_bounds(DropSource("Ambigu", rate="1–2–3%")) is None


@pytest.mark.parametrize("category,name,rule", [
    ("Ressource", "Feuille de Blop Multicolore Royal", DropRule.STANDARD),
    ("Dofus", "Dofus Turquoise", DropRule.STANDARD),
    ("Anneau", "Gelano", DropRule.STANDARD),
    ("Paquet de cartes", "Paquet de cartes Bronze", DropRule.FIXED),
    ("Paquets de cartes", "Paquet de cartes Argent", DropRule.FIXED),
    ("Bouclier", "Bouclier Trophée du Dragon Cochon", DropRule.FIXED),
    ("BOUCLIERS", "  BOUCLIER   TROPHÉE du Bouftou ", DropRule.FIXED),
    ("Bouclier", "Bouclier du Bouftou", DropRule.STANDARD),
    ("Ressource", "Fragment de bouclier trophée", DropRule.STANDARD),
    ("Objet de quête", "Preuve", DropRule.QUEST),
    ("Objets de mission", "Preuve", DropRule.QUEST),
])
def test_exceptions_are_narrow_and_do_not_infer_rules_from_a_drop_rate(category, name, rule):
    assert item_drop_rule(category, name) is rule


@pytest.mark.parametrize("pp", [0, 100, 435, 10000])
def test_fixed_rate_never_receives_a_prospecting_multiplier(pp):
    assert personal_rate(Decimal("5"), ProspectingSettings(pp), fixed=True) == Decimal(5)


def test_huge_integer_sources_are_rejected_before_unbounded_text_conversion():
    value = 10 ** 10000
    assert parse_rate(value) is None
    assert source_count(value) is None
