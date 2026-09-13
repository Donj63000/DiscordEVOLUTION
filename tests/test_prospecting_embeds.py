"""Le résultat doit garder les sources, les limites du modèle et les niveaux lisibles."""

from dataclasses import replace

import pytest

from utils.drop_calculator import ProspectingSettings
from utils.wiki_embeds import drop_pages, enriched_item_sections
from utils.xixou_api import DropSource, ItemEnrichment
from test_xixou_embeds import assert_valid_embed, detail_for, text_of


def blop_source(**changes):
    source = DropSource(
        "Blop Multicolore Royal", rate="10% – 14%", pp="100", maximum="∞",
        level_rates=tuple((str(150 + 5 * index), f"{10 + index}%") for index in range(5)),
        zones=("Donjon des Blops",),
    )
    return replace(source, **changes)


def test_personal_rates_keep_every_base_level_and_do_not_mutate_shared_enrichment():
    detail = detail_for(name="Feuille de Blop Multicolore Royal", category="Ressource")
    source = blop_source()
    enrichment = ItemEnrichment(drops=(source,), dates=("2026-09-01",))
    original = enriched_item_sections(detail, enrichment)
    snapshot = [page.embed.to_dict() for page in original["drops"]]
    calculated = drop_pages(detail, enrichment, ProspectingSettings(435))
    text = text_of(calculated)
    assert "**Taux de base :** 10% à 14%" in text
    assert "**Avec 435 PP (jet individuel) :** 43,5 % à 60,9 %" in text
    for level, base, result in [
        (150, 10, "43,5"), (155, 11, "47,85"), (160, 12, "52,2"),
        (165, 13, "56,55"), (170, 14, "60,9"),
    ]:
        assert f"Niveau {level} : {base}% → {result} %" in text
    assert "seuil)" not in text and "100 PP · atteint" in text
    assert "hors challenges, étoiles et bonus serveur" in text
    assert "Donjon des Blops" in text and "2026-09-01" in text
    assert "jeuxonline.info/article/14796/prospection" in text
    assert [page.embed.to_dict() for page in original["drops"]] == snapshot
    assert enrichment.drops == (source,)
    for page in calculated:
        assert_valid_embed(page.embed)
        assert "Page " in page.embed.footer.text


@pytest.mark.parametrize("group,rate,message", [
    (None, "43,5 % à 60,9 % (sous réserve du seuil)", "groupe inconnu"),
    (435, "0 %", "non atteint"),
    (999, "0 %", "non atteint"),
    (1000, "43,5 % à 60,9 %", "atteint"),
])
def test_unknown_group_is_not_solo_and_known_locked_group_is_zero(group, rate, message):
    source = blop_source(pp="1000")
    pages = drop_pages(detail_for(), ItemEnrichment(drops=(source,)),
                       ProspectingSettings(435, group))
    text = text_of(pages)
    assert f"**Avec 435 PP (jet individuel) :** {rate}" in text
    assert message in text
    assert "**Taux de base :** 10% à 14%" in text
    if group in (435, 999):
        assert "Niveau 150 : 10% → 0 %" in text
        assert "Niveau 170 : 14% → 0 %" in text


@pytest.mark.parametrize("maximum,expected", [
    ("1", "quota commun"), ("8", "quota commun"), ("", "quota non renseigné"),
    ("0", "quantité maximale nulle"),
])
def test_limited_or_unknown_loot_is_never_presented_as_a_guaranteed_receipt(maximum, expected):
    source = blop_source(maximum=maximum)
    text = text_of(drop_pages(detail_for(), ItemEnrichment(drops=(source,)),
                              ProspectingSettings(435)))
    assert expected in text
    if maximum in ("1", "8"):
        assert "pas ta probabilité finale" in text
    if maximum == "0":
        assert "**Avec 435 PP (jet individuel) :** 0 %" in text


def test_tiny_rate_missing_rate_zero_and_missing_threshold_remain_distinct():
    sources = (
        DropSource("Rare", rate="0.001%", pp="100", maximum="∞"),
        DropSource("Zéro", rate="0%", pp="0", maximum="∞"),
        DropSource("Absent", pp="100", maximum="∞"),
        DropSource("Seuil inconnu", rate="10%", maximum="∞"),
    )
    text = text_of(drop_pages(detail_for(), ItemEnrichment(drops=sources),
                              ProspectingSettings(435)))
    assert "0,00435 %" in text and "Non calculable : taux de base" in text
    assert "**Avec 435 PP (jet individuel) :** 0 %" in text
    assert "43,5 % (sous réserve du seuil)" in text
    assert "Non renseigné ; résultat conditionnel" in text


def test_incomplete_and_suspect_levels_stay_explicit():
    source = blop_source(level_rates=(("155", "11%"), ("160 (à vérifier)", "12%"),
                                      ("165", "inconnu")))
    text = text_of(drop_pages(detail_for(), ItemEnrichment(drops=(source,)),
                              ProspectingSettings(435)))
    assert "43,5 % à 60,9 %" in text
    assert "à vérifier" in text and "12% → 52,2 %" in text
    assert "inconnu → Non calculable" in text
    assert "Niveau 150" not in text


@pytest.mark.parametrize("category,name", [
    ("Paquet de cartes", "Paquet de cartes Bronze"),
    ("Bouclier", "Bouclier Trophée du Blop Royal"),
])
def test_documented_fixed_drops_are_not_multiplied(category, name):
    detail = detail_for(category=category, name=name)
    source = DropSource("Boss", rate="5%", pp="0", maximum="∞",
                        level_rates=(("100", "5%"),))
    text = text_of(drop_pages(detail, ItemEnrichment(drops=(source,)),
                              ProspectingSettings(435)))
    assert "**Avec 435 PP (taux fixe) :** 5 %" in text
    assert "Niveau 100 : 5% → 5 %" in text
    assert "n'est pas influencé" in text
    assert "21,75" not in text and "min(100" not in text


def test_quest_conditions_do_not_receive_a_fictitious_standard_probability():
    text = text_of(drop_pages(
        detail_for(category="Objet de quête"),
        ItemEnrichment(drops=(blop_source(),)), ProspectingSettings(435),
    ))
    assert "indisponible pour un objet de quête" in text
    assert "Niveau 150 : 10%" in text
    assert "→" not in text and "43,5" not in text and "min(100" not in text


def test_rate_caps_at_100_without_clamping_source_data():
    source = blop_source(rate="30%", level_rates=(("170", "30%"),))
    text = text_of(drop_pages(detail_for(), ItemEnrichment(drops=(source,)),
                              ProspectingSettings(435)))
    assert "**Taux de base :** 30%" in text
    assert "**Avec 435 PP (jet individuel) :** 100 %" in text
    assert "Niveau 170 : 30% → 100 %" in text


def test_long_unicode_sources_paginate_losslessly_with_safe_mentions_and_attribution():
    sources = tuple(
        blop_source(name=f"Monstre {index}", zones=("😀" * 1200 + f" fin-zone-{index}",),
                    level_rates=tuple((str(level), "0.001%") for level in range(60)),
                    maximum="1")
        for index in range(9)
    )
    enrichment = ItemEnrichment(drops=sources, dates=("2026-09-01",), stale=True)
    pages = drop_pages(detail_for(name="@everyone 😀" * 20), enrichment,
                       ProspectingSettings(435, 1000))
    text = text_of(pages)
    assert len(pages) > 9 and "@everyone" not in text
    for index in range(9):
        assert f"Monstre {index}" in text and f"fin-zone-{index}" in text
    for index, page in enumerate(pages, 1):
        assert_valid_embed(page.embed)
        assert "actualisation indisponible" in page.embed.fields[-1].value
        assert page.embed.footer.text.endswith(f"Page {index}/{len(pages)}")
    assert text.count("→ 0,00435 %") == 9 * 60


def test_reset_recreates_exact_default_drop_pages():
    detail, enrichment = detail_for(), ItemEnrichment(drops=(blop_source(),))
    expected = [page.embed.to_dict() for page in enriched_item_sections(detail, enrichment)["drops"]]
    drop_pages(detail, enrichment, ProspectingSettings(435))
    assert [page.embed.to_dict() for page in drop_pages(detail, enrichment)] == expected


def test_empty_drops_do_not_refer_to_an_unavailable_calculator_button():
    pages = drop_pages(detail_for(), ItemEnrichment())
    assert "Drops non renseignés" in text_of(pages)
    assert "Prospection personnalisée" not in pages[0].embed.description
