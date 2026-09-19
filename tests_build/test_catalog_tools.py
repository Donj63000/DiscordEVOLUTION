import asyncio
import json
from types import SimpleNamespace
from io import BytesIO
import pytest
from PIL import Image
from utils.build.catalog import normalize, search, verify_snapshot, CatalogService, freeze_catalog
from utils.build.repository import MemoryRepository
from utils.build.models import *
from utils.build.calculator import calculate
from utils.build.damage import DamageLine, Scenario, simulate
from utils.build.renderer import render, text_report
from utils.build.config import Config
from utils.build.fm_adapter import from_exo, to_exo_values
from utils.build.crafting import shopping_list


def source():
    entries=[SimpleNamespace(kind="item",identifier="1",token="item:1",name="Chapeau de Test",category="Chapeau",level=30,url="https://fixture.invalid/1")]
    payload={"famille":"equipements","genere_le":"fixture-only","data":{"chapeaux":[{
        "id":1,"name":"Chapeau de Test","level":"30","effets":["+10 à 20 en force"],"conditions":[],"panoplie":""}]}}
    return entries,payload


def test_normalize_identity_effects_snapshot():
    entries,payload=source(); cat=normalize(entries,payload)
    item=cat.items[0]
    assert item.ref=="item:1" and item.effects[0].high==20
    assert item.allowed_slots==("coiffe",) and item.conditions_known
    verify_snapshot(cat)


@pytest.mark.parametrize("change", [{"id":999},{"name":"autre"},{"level":31},{"id":True},{"effets":None}])
def test_conflicting_identity_never_guessed(change):
    entries,payload=source();payload["data"]["chapeaux"][0].update(change)
    with pytest.raises(BuildError):
        normalize(entries,payload)


def test_conflicting_duplicate_excluded():
    entries,payload=source(); row=dict(payload["data"]["chapeaux"][0]);row["effets"]=["+40 en force"]
    payload["data"]["chapeaux"].append(row)
    with pytest.raises(BuildError):
        normalize(entries,payload)


def test_missing_panoplie_membership_is_unknown():
    entries,payload=source();del payload["data"]["chapeaux"][0]["panoplie"]
    assert normalize(entries,payload).items[0].warnings


def test_override_requires_provenance_and_retains_it():
    entries,payload=source()
    overrides={"schema_version":1,"items":{"item:1":{"unique":True}},"sets":[]}
    with pytest.raises(BuildError):
        normalize(entries,payload,overrides)
    overrides["items"]["item:1"]["source"]="fixture://reviewed-not-game-data"
    item=normalize(entries,payload,overrides).items[0]
    assert item.unique and item.correction_sources==("fixture://reviewed-not-game-data",)


@pytest.mark.parametrize("payload",[{}, {"famille":"sorts","data":{}}, {"famille":"equipements","data":[]}, {"famille":"equipements","data":{"chapeaux":None}}])
def test_corrupt_envelope_rejected(payload):
    with pytest.raises(BuildError):
        normalize([],payload)


def test_search_slot_level_reference_and_limit(item_factory):
    items=[item_factory(i,stats={"fo":i}) for i in range(1,40)]
    cat=freeze_catalog(items)
    assert len(search(cat))==25
    assert search(cat,"item:15")[0].ref=="item:15"
    assert search(cat,slot="cape")==()
    with pytest.raises(BuildError):
        search(cat,slot="invalid")


@pytest.mark.asyncio
async def test_bad_refresh_preserves_last_good_snapshot(tmp_path):
    entries,payload=source();cat=normalize(entries,payload)
    repo=MemoryRepository();await repo.put_snapshot("catalog",cat.id,canonical(cat))
    async def get_entries(): return entries
    async def bad(_): return {"bad":"data"}
    wiki=SimpleNamespace(client=SimpleNamespace(items=get_entries),enrichment_client=SimpleNamespace(enabled=True,catalog=bad))
    overrides=tmp_path/"overrides.json";overrides.write_text('{"schema_version":1,"items":{},"sets":[]}')
    service=CatalogService(repo,lambda:wiki,overrides)
    await service.restore()
    assert await service.refresh()==cat
    assert service.last_error and service.latest==cat
    assert await repo.latest_snapshot("catalog")==canonical(cat)


@pytest.mark.asyncio
async def test_missing_wiki_first_start_fails_safely(tmp_path):
    service=CatalogService(MemoryRepository(),lambda:None)
    with pytest.raises(BuildError):
        await service.refresh()
    assert service.latest is None


def test_png_and_text_share_same_report(build,catalog,rules,tmp_path):
    build=build.with_slot("coiffe",equip(catalog.items[0]))
    report=calculate(build,catalog,rules)
    png=render(build,report,catalog,False,{"item:1":b"not-image"})
    image=Image.open(BytesIO(png));assert image.size==(1200,1480) and image.format=="PNG"
    assert png==render(build,report,catalog,False)
    text=text_report(build,report,catalog,False)
    assert "Force : 30" in text and "ESSAI EN MÉMOIRE" in text and "e0" in text
    (tmp_path/"card.png").write_bytes(png)


def test_damage_separate_lines_and_experimental_label(build,catalog,rules):
    build=build.with_slot("coiffe",equip(catalog.items[0]));r=calculate(build,catalog,rules)
    result=simulate(r,(DamageLine(element="te",minimum=10,maximum=20),DamageLine(element="te",minimum=1,maximum=2,life_steal=True)),Scenario())
    assert (result["minimum"],result["maximum"])==(14,28)
    assert result["certified"] is False and len(result["lines"])==2


def test_damage_unknown_stats_refused(build,catalog,rules):
    profile=revised(build.profile,naked_stats=values({"pa":7}))
    report=calculate(revised(build,profile=profile),catalog,rules)
    with pytest.raises(BuildError):
        simulate(report,(DamageLine(element="te",minimum=1,maximum=2),),Scenario())


def test_config_uses_console_without_external_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL","secret-of-other-module")
    monkeypatch.delenv("BUILD_DATABASE_URL",raising=False)
    assert Config.from_env().backend == "console"
    monkeypatch.setenv("BUILD_DATABASE_URL","secret-build")
    assert "secret-build" not in repr(Config.from_env())
    monkeypatch.setenv("BUILD_BACKEND", "postgres")
    assert Config.from_env().backend == "console"
    monkeypatch.setenv("BUILD_MAX_PER_USER","0")
    with pytest.raises(BuildError):Config.from_env()


def test_fm_copy_independent(item_factory):
    item=item_factory(stats={"fo":30})
    session=SimpleNamespace(item=SimpleNamespace(token=item.ref),state=SimpleNamespace(jets={"fo":35,"pa":1}))
    instance=from_exo(equip(item),item,session)
    session.state.jets["fo"]=500
    assert instance.final_values[0].value==35 and instance.extras[0].value==1
    data=to_exo_values(instance,item)
    assert data


@pytest.mark.asyncio
async def test_recipe_quantity_counts_two_ring_instances(build,rules,item_factory):
    ring=item_factory(1,"anneau_1",recipe=[{"ref":"item:9","name":"Ressource fixture","quantity":3}],recipe_known=True)
    cat=freeze_catalog([ring]); b=revised(build,catalog_id=cat.id)
    b=b.with_slot("anneau_1",equip(ring)).with_slot("anneau_2",equip(ring))
    async def items():return []
    wiki=SimpleNamespace(client=SimpleNamespace(items=items))
    result=await shopping_list(b,cat,wiki)
    assert result["ingredients"][0]["quantity"]==6
    assert result["prix"]=="inconnus"


@pytest.mark.asyncio
async def test_none_enrichment_client_is_safe():
    wiki=SimpleNamespace(enrichment_client=None)
    service=CatalogService(MemoryRepository(),lambda:wiki)
    with pytest.raises(BuildError):await service.refresh()


@pytest.mark.asyncio
async def test_live_recipe_missing_is_not_free(build,catalog):
    from utils.dofus_wiki import WikiError
    async def items():
        return [SimpleNamespace(token=i.ref,url=i.source) for i in catalog.items]
    async def detail(entry):
        if entry.token=="item:1":return SimpleNamespace(data={"recipe":[{"item_id":10,"qty":2,"name":"Ressource"}]})
        raise WikiError("unavailable")
    b=build.with_slot("coiffe",equip(catalog.items[0])).with_slot("cape",equip(catalog.items[1]))
    result=await shopping_list(b,catalog,SimpleNamespace(client=SimpleNamespace(items=items,detail=detail)))
    resource = next(row for row in result["ingredients"] if row["ref"] == "item:10")
    assert resource["quantity"]==2 and resource["status"] == "recipe_unknown"
    assert result["recettes_manquantes"]==sorted([catalog.items[1].name, "Ressource"])
