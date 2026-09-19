import json
from pathlib import Path
import pytest
from utils.build.models import Actor, BuildError, Conflict, NotFound, canonical, revised, equip, digest, ItemTemplate, SetDefinition
from utils.build.catalog import freeze_catalog, search, normalize
from utils.build.diagnostics import catalog_coverage
from utils.build.calculator import calculate
from test_catalog_tools import source


@pytest.mark.asyncio
async def test_restore_is_preview_then_new_revision(service,actor,profile):
    first,_,_=await service.new(actor,'Premier nom',profile,operation='first')
    preview=await service.rename(actor,first.id,first.revision,'Deuxième nom')
    second,_,_=await service.apply(actor,preview,'rename')
    restore=await service.restore_revision(actor,first.id,second.revision,first.revision)
    assert (await service.repository.get(actor,first.id)).name=='Deuxième nom'
    restored,_,_=await service.apply(actor,restore,'restore')
    assert restored.name=='Premier nom' and restored.revision==second.revision+1
    assert restored.id==first.id and restored.owner_id==actor.user_id
    assert [b.name for b in await service.repository.revisions(actor,first.id)]==['Premier nom','Deuxième nom','Premier nom']
    replay,_,_=await service.apply(actor,restore,'restore')
    assert replay==restored


@pytest.mark.asyncio
async def test_restore_old_revision_is_cas_protected(service,actor,profile):
    first,_,_=await service.new(actor,'Original',profile,operation='first')
    preview=await service.restore_revision(actor,first.id,first.revision,first.revision)
    rename=await service.rename(actor,first.id,first.revision,'Plus récent')
    await service.apply(actor,rename,'rename')
    with pytest.raises(Conflict):await service.apply(actor,preview,'restore')
    assert (await service.repository.get(actor,first.id)).name=='Plus récent'
    with pytest.raises(Conflict):await service.restore_revision(actor,first.id,first.revision,first.revision)


@pytest.mark.asyncio
async def test_history_privacy_and_unknown_revision(service,actor,profile):
    first,_,_=await service.new(actor,'Secret',profile,operation='first')
    for stranger in (Actor(guild_id=actor.guild_id,user_id=999),Actor(guild_id=999,user_id=actor.user_id)):
        with pytest.raises(NotFound):await service.repository.revisions(stranger,first.id)
        with pytest.raises(NotFound):await service.restore_revision(stranger,first.id,first.revision,first.revision)
    with pytest.raises(BuildError):await service.restore_revision(actor,first.id,first.revision,999)


@pytest.mark.asyncio
async def test_history_retention_and_delete(service,actor,profile):
    b,_,_=await service.new(actor,'Initial',profile,operation='first')
    for n in range(22):
        p=await service.rename(actor,b.id,b.revision,f'Nom {n}')
        b,_,_=await service.apply(actor,p,f'rename{n}')
    assert len(await service.repository.revisions(actor,b.id))==20
    with pytest.raises(BuildError):await service.restore_revision(actor,b.id,b.revision,1)
    await service.delete(actor,b.id,b.revision,'delete')
    with pytest.raises(NotFound):await service.repository.revisions(actor,b.id)


def test_browse_pagination_sort_and_minimum(item_factory):
    items=[]
    for number in range(1,61):
        body=item_factory(number,stats={'fo':61-number}).model_dump(mode='json');body.pop('revision');body['level']=number
        items.append(ItemTemplate(revision=digest(body),**body))
    catalog=freeze_catalog(items)
    first=search(catalog,limit=25);second=search(catalog,offset=25);third=search(catalog,offset=50)
    assert (len(first),len(second),len(third))==(25,25,10)
    assert len({i.ref for i in first+second+third})==60
    assert first[0].level==60
    assert search(catalog,priority='fo')[0].ref=='item:1'
    assert [i.level for i in search(catalog,minimum=50,level=55)]==list(range(55,49,-1))
    for kwargs in ({'minimum':100,'level':50},{'priority':'money'},{'offset':-1}):
        with pytest.raises(BuildError):search(catalog,**kwargs)


def test_single_string_membership_is_not_lost():
    entries,raw=source();raw['data']['chapeaux'][0]['panoplie']=['Panoplie du Chêne Mou']
    item=normalize(entries,raw).items[0]
    assert item.set_ref=='panoplie du chene mou' and not item.warnings


def test_coverage_names_missing_tables_without_private_data(catalog):
    info=catalog_coverage(catalog)
    assert info['objets']==3 and info['tables_panoplies_manquantes']==[]
    assert 'owner' not in canonical(info)


PUBLISHED=json.loads((Path(__file__).resolve().parents[1]/'data/build/catalog_overrides_v1.json').read_text(encoding='utf-8'))['sets']

@pytest.mark.parametrize('row',PUBLISHED,ids=[row['name'] for row in PUBLISHED])
def test_published_tables_complete_and_fixed(row):
    definition=SetDefinition.model_validate(row)
    assert definition.source.startswith('https://xixou.io/encyclopedie/panoplies/')
    assert [tier.pieces for tier in definition.tiers]==list(range(1,len(definition.tiers)+1))
    assert definition.tiers[0].effects==()
    assert all(e.low==e.high and e.kind=='stat' for t in definition.tiers for e in t.effects)


@pytest.mark.parametrize('name,stat,value',[
 ('Panoplie du Chêne Mou','pm',1),('Panoplie du Meulou','pa',1),
 ('Panoplie Ventouse','sa',20),('Panoplie Ventouse','pa',1),
 ('Panoplie Souveraine','pp',10),('Panoplie des Sous-bois','pp',15),
 ('Panoplie Ougah','pp',30),('Panoplie du Minotot','so',6),
 ('Panoplie du Bworker Gladiateur','pa',1),('Panoplie du Bworker Berserker','pm',1),
 ('Panoplie du Bouftou','pv',30),('Panoplie du Prespic','ren',8),
 ('Panoplie du Jeune Aventurier','cha',40),('Panoplie Blop Multicolore Royale','po',1),
])
def test_published_final_tiers_not_cumulative(name,stat,value):
    """Valeurs contrôlées sur les pages de provenance ; pas un relevé en jeu."""
    row=next(r for r in PUBLISHED if r['name']==name)
    tier=SetDefinition.model_validate(row).tiers[-1]
    assert sum(e.high for e in tier.effects if e.stat==stat)==value


def test_report_records_new_engine(build,catalog,rules):
    assert calculate(build,catalog,rules).engine=='1.2.0-beta.1'
