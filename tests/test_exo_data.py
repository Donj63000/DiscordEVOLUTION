from types import SimpleNamespace

import pytest

from utils.dofus_wiki import WikiDetail, WikiEntry
from utils.exo_data import from_detail, is_mageable, parse_effects


def entry(category="Anneau"):
    return WikiEntry("item","1","Gelano",category,60,"/items/gelano","gelano")


def test_basic_french_effects():
    bounds,unknown,immutable=parse_effects([
        "+1 PA","+11 à 20 en force","+51 à 100 en vitalité",
        "+1 à 2 aux coups critiques","+5 % de résistance feu",
        "+1 créatures invocables",
    ])
    assert bounds == {"pa":(1,1),"fo":(11,20),"vi":(51,100),"cc":(1,2),"rp_fe":(5,5),"invo":(1,1)}
    assert not unknown


def test_negative_bounds_and_unknown_preserved():
    bounds,unknown,_=parse_effects(["-10 à -5 en force","Bonus mystère 20","+1 PA","+2 PA"])
    assert bounds["fo"] == (-10,-5)
    assert bounds["pa"] == (1,1)
    assert len(unknown) == 2


@pytest.mark.parametrize("category",["Dofus","Familier","Monture","Ressource","Bouclier"])
def test_non_mageable_rejected(category):
    assert not is_mageable(entry(category))
    with pytest.raises(ValueError):
        from_detail(WikiDetail(entry(category),{"stats":["+1 PA"]},None,False))


def test_xixou_effects_preferred_and_pods_not_stat_weight():
    detail=WikiDetail(entry(),{"stats":["+50 en force"]},None,False)
    enrichment=SimpleNamespace(effects=("+1 PA",),stale=False,weight=5)
    item=from_detail(detail,enrichment)
    assert item.bounds == {"pa":(1,1)}
    assert "Xixou" in item.source
    assert "pod" not in item.bounds


def test_wiki_fallback_and_stale_label():
    detail=WikiDetail(entry(),{"stats":["+1 PA"]},None,True)
    item=from_detail(detail,None)
    assert "repli" in item.source
    assert "cache ancien" in item.source


def test_no_effects_is_not_invented():
    with pytest.raises(ValueError):
        from_detail(WikiDetail(entry(),{},None,False))


CATALOG_ITEMS = (
    ("Gelano", "Anneau", ("+1 PA",), {"pa": (1, 1)}),
    ("Anneau du Dragon Cochon", "Anneau", (
        "+51 à 80 en vitalité", "+21 à 35 en agilité", "+6 à 10 en prospection",
        "+3 à 4 aux coups critiques", "Augmente les dommages de 3 à 5%",
        "+16 à 25 en sagesse", "+101 à 150 en initiative", "+3 à 5 de dommages",
        "+21 à 35 en force", "+21 à 35 à la chance",
    ), {"vi": (51, 80), "age": (21, 35), "pp": (6, 10), "cc": (3, 4),
        "pui": (3, 5), "sa": (16, 25), "ini": (101, 150), "do": (3, 5),
        "fo": (21, 35), "cha": (21, 35)}),
    ("Voile d'encre", "Cape", (
        "+251 à 350 en vitalité", "+51 à 70 en force", "+51 à 70 en intelligence",
        "+26 à 40 en sagesse", "+6 à 10 de dommages",
        "6 à 10 % de résistance à l'eau", "6 à 10 % de résistance à l'air",
        "6 à 10 % de résistance au feu", "+1 à la portée", "+2 à 3 aux coups critiques",
    ), {"vi": (251, 350), "fo": (51, 70), "ine": (51, 70), "sa": (26, 40),
        "do": (6, 10), "rp_ea": (6, 10), "rp_ai": (6, 10), "rp_fe": (6, 10),
        "po": (1, 1), "cc": (2, 3)}),
)


@pytest.mark.parametrize("name,category,effects,expected", CATALOG_ITEMS)
@pytest.mark.parametrize("source", ["xixou", "moon"])
def test_real_catalog_wording_is_fully_mageable(name, category, effects, expected, source):
    catalog_entry = WikiEntry("item", "1", name, category, 100, "/items/test", name)
    moon_effects = [effect.replace("'", "\\'") for effect in effects]
    detail = WikiDetail(catalog_entry, {"stats": moon_effects}, None, False)
    enrichment = SimpleNamespace(effects=effects, stale=False) if source == "xixou" else None
    item = from_detail(detail, enrichment)
    assert item.bounds == expected
    assert not item.unsupported
    assert item.automatic


@pytest.mark.parametrize("element,key", [
    ("au neutre", "ne"), ("à la terre", "te"), ("au feu", "fe"),
    ("à l’eau", "ea"), ("à l\\'air", "ai"),
])
@pytest.mark.parametrize("resistance", ["résistance", "RÉSISTANCES"])
@pytest.mark.parametrize("percentage", [False, True])
def test_all_resistance_elements_and_typographic_variants(element, key, resistance, percentage):
    prefix = "% de " if percentage else "de "
    bounds, unsupported, immutable = parse_effects([
        f"+6 à 10 {prefix}{resistance} {element}",
    ])
    assert bounds == {("rp_" if percentage else "r_") + key: (6, 10)}
    assert not unsupported
    assert not immutable


@pytest.mark.parametrize("effects", [
    ("+1 à la chance", "+2 en chance"),
    ("+1 % de résistance au feu", "+2 % de résistance feu"),
    ("+1 en force", "+2 % de résistance aux pièges"),
    ("+1 en force", "+2 à la chance et en force"),
    ("+1 en force", "+2 résistance feu et eau"),
])
def test_aliases_keep_unknown_and_duplicate_effects_blocked(effects):
    detail = WikiDetail(entry(), {"stats": list(effects)}, None, False)
    item = from_detail(detail)
    assert len(item.unsupported) == 1
    assert not item.automatic
