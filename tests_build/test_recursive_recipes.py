"""Les recettes de ce module sont synthétiques et n'interrogent jamais Discord."""
from collections import Counter
from types import SimpleNamespace

import pytest

from utils.build.catalog import freeze_catalog
from utils.build.crafting import shopping_list
from utils.build.models import BuildError, equip, revised


def wiki_for(recipes):
    calls = Counter()
    async def items():
        return [SimpleNamespace(token=f"item:{identifier}", url=f"https://fixture.invalid/{identifier}")
                for identifier in recipes]
    async def detail(entry):
        calls[entry.token] += 1
        return SimpleNamespace(data=recipes[int(entry.token.split(":")[1])])
    return SimpleNamespace(client=SimpleNamespace(items=items, detail=detail)), calls


@pytest.mark.asyncio
async def test_recursive_quantity_and_shared_recipe_cache(build, item_factory):
    ring = item_factory(1, "anneau_1", recipe_known=True,
                        recipe=[{"ref": "item:10", "name": "Alliage", "quantity": 3}])
    catalog = freeze_catalog([ring])
    build = revised(build, catalog_id=catalog.id).with_slot("anneau_1", equip(ring)).with_slot("anneau_2", equip(ring))
    wiki, calls = wiki_for({10: {"recipe": [{"item_id": 11, "name": "Minerai", "qty": 4}]}, 11: {"recipe": []}})
    result = await shopping_list(build, catalog, wiki)
    assert result["complete"]
    assert result["ingredients"] == [{"ref": "item:11", "name": "Minerai", "quantity": 24, "status": "raw_material"}]
    assert calls == {"item:10": 1, "item:11": 1}
    owned = await shopping_list(build, catalog, wiki, owned_slots=("anneau_1",))
    assert owned["ingredients"][0]["quantity"] == 12


@pytest.mark.asyncio
async def test_empty_recipe_is_known_but_absent_field_is_unknown(build, item_factory):
    item = item_factory(recipe_known=True, recipe=[{"ref": "item:10", "name": "Inconnu", "quantity": 1},
                                                   {"ref": "item:11", "name": "Minerai", "quantity": 2}])
    catalog = freeze_catalog([item])
    build = revised(build, catalog_id=catalog.id).with_slot("coiffe", equip(item))
    wiki, _ = wiki_for({10: {}, 11: {"recipe": []}})
    result = await shopping_list(build, catalog, wiki)
    assert not result["complete"] and result["recettes_manquantes"] == ["Inconnu"]
    assert {row["name"]: row["status"] for row in result["ingredients"]} == {"Inconnu": "recipe_unknown", "Minerai": "raw_material"}


@pytest.mark.asyncio
async def test_cycles_depth_and_volume_are_bounded(build, item_factory):
    item = item_factory(recipe_known=True, recipe=[{"ref": "item:10", "name": "Alliage", "quantity": 2}])
    catalog = freeze_catalog([item])
    build = revised(build, catalog_id=catalog.id).with_slot("coiffe", equip(item))
    wiki, _ = wiki_for({10: {"recipe": [{"item_id": 10, "name": "Alliage", "qty": 1}]}})
    result = await shopping_list(build, catalog, wiki)
    assert result["diagnostics"][0]["code"] == "RECIPE_CYCLE"
    assert not result["complete"]
    limited = await shopping_list(build, catalog, wiki, max_depth=1)
    assert limited["diagnostics"][0]["code"] == "RECIPE_DEPTH"
    with pytest.raises(BuildError, match="Volume maximal"):
        await shopping_list(build, catalog, wiki, max_nodes=1)
    with pytest.raises(BuildError, match="Volume maximal"):
        await shopping_list(build, catalog, wiki, max_quantity=1)
