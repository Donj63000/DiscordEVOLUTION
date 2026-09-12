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
