"""Autorisations, atomicité, répétition des opérations et prévisualisations."""
import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from utils.build.models import *
from utils.build.repository import share_hash
from utils.build.catalog import freeze_catalog
from utils.build.import_export import export_build, import_build, parse_json
from utils.build.service import Preview


@pytest.mark.asyncio
async def test_create_replay_is_same_build(service,actor,profile):
    first,_,_=await service.new(actor,"Test",profile,operation="create:1")
    again,_,_=await service.new(actor,"Test",profile,operation="create:1")
    assert first == again and first.revision == 1
    assert len(await service.repository.list(actor)) == 1
    with pytest.raises(Conflict):
        await service.new(actor,"Different",profile,operation="create:1")


@pytest.mark.asyncio
async def test_preview_does_not_modify_and_double_click_once(service,actor,profile):
    build,_,_=await service.new(actor,"Test",profile,operation="create:1")
    preview=await service.equipment(actor,build.id,1,"coiffe","item:1")
    assert (await service.repository.get(actor,build.id)).in_slot("coiffe") is None
    results=await asyncio.gather(service.apply(actor,preview,"click:1"),service.apply(actor,preview,"click:1"))
    assert results[0][0] == results[1][0] and results[0][0].revision == 2
    assert len(service.repository.history[build.id]) == 2


@pytest.mark.asyncio
async def test_two_editors_compare_and_swap(service,actor,profile):
    build,_,_=await service.new(actor,"Test",profile,operation="create:1")
    a=await service.equipment(actor,build.id,1,"coiffe","item:1")
    b=await service.equipment(actor,build.id,1,"coiffe","item:3")
    results=await asyncio.gather(service.apply(actor,a,"a"),service.apply(actor,b,"b"),return_exceptions=True)
    assert sum(isinstance(r,Conflict) for r in results)==1
    assert (await service.repository.get(actor,build.id)).revision == 2


@pytest.mark.asyncio
async def test_quota_checked_under_concurrency(service,actor,profile):
    service.repository.quota=1
    previews=await asyncio.gather(*(service.new(actor,str(i),profile) for i in range(10)))
    results=await asyncio.gather(*(service.apply(actor,p,f"create:{i}") for i,p in enumerate(previews)),return_exceptions=True)
    assert sum(isinstance(r,BuildError) for r in results)==9
    assert len(await service.repository.list(actor))==1


@pytest.mark.asyncio
@pytest.mark.parametrize("other",[Actor(guild_id=100,user_id=201),Actor(guild_id=101,user_id=200)])
async def test_isolation_every_read_write(service,actor,profile,other):
    build,_,_=await service.new(actor,"SECRET",profile,operation="create:1")
    for call in [service.inspect(other,build.id),service.rename(other,build.id,1,"hack"),service.delete(other,build.id,1,"bad")]:
        with pytest.raises(NotFound,match="autorisé"):
            await call
    assert await service.repository.list(other)==()


@pytest.mark.asyncio
async def test_preview_owner_and_report_revalidated(service,actor,profile):
    p=await service.new(actor,"Test",profile)
    bad=revised(p,report=revised(p.report,equipability="invalid"))
    with pytest.raises(BuildError,match="prévisualisation"):
        await service.apply(actor,bad,"bad")
    with pytest.raises(NotFound):
        await service.apply(Actor(guild_id=100,user_id=201),p,"wrong-owner")


@pytest.mark.asyncio
async def test_share_frozen_copy_and_revocation(service,actor,profile):
    original,_,_=await service.new(actor,"Avant",profile,operation="create")
    receipt=await service.share(actor,original,"share")
    assert receipt == await service.share(actor,original,"share")
    preview=await service.rename(actor,original.id,1,"Après")
    await service.apply(actor,preview,"rename")
    recipient=Actor(guild_id=100,user_id=201)
    shared=await service.repository.shared(recipient,receipt["code"])
    assert shared.name=="Avant" and shared.revision==1
    copied=await service.copy(recipient,receipt["code"])
    assert copied.candidate.owner_id==201 and copied.candidate.id!=original.id and copied.candidate.revision==0
    assert len(await service.repository.list(recipient))==0
    await service.apply(recipient,copied,"copy")
    with pytest.raises(NotFound):
        await service.repository.shared(Actor(guild_id=101,user_id=201),receipt["code"])
    with pytest.raises(NotFound):
        await service.repository.revoke(recipient,receipt["code"])
    await service.repository.revoke(actor,receipt["code"])
    with pytest.raises(NotFound):
        await service.repository.shared(recipient,receipt["code"])


@pytest.mark.asyncio
async def test_publication_claim_prevents_duplicate_send(service,actor,profile):
    build,_,_=await service.new(actor,"Test",profile,operation="create")
    code=(await service.share(actor,build,"share"))["code"]
    claims=await asyncio.gather(service.repository.claim_publication(actor,code),service.repository.claim_publication(actor,code),return_exceptions=True)
    assert claims.count(True)==1 and sum(isinstance(x,BuildError) for x in claims)==1
    await service.repository.publication(actor,code,"sent",123,456)
    assert await service.repository.claim_publication(actor,code) is False
    await service.repository.publication(actor,code,"sent",123,456)


@pytest.mark.asyncio
async def test_failed_publication_cannot_be_automatically_replayed(service,actor,profile):
    build,_,_=await service.new(actor,"Test",profile,operation="create")
    code=(await service.share(actor,build,"share"))["code"]
    await service.repository.claim_publication(actor,code)
    await service.repository.publication(actor,code,"failed")
    with pytest.raises(BuildError):
        await service.repository.claim_publication(actor,code)


@pytest.mark.asyncio
async def test_expired_shares_hidden(service,actor,profile):
    build,_,_=await service.new(actor,"Test",profile,operation="create")
    code=(await service.share(actor,build,"share"))["code"]
    service.repository.shares[share_hash(code)]["expires"]=datetime.now(timezone.utc)-timedelta(seconds=1)
    with pytest.raises(NotFound):
        await service.repository.shared(actor,code)


@pytest.mark.asyncio
async def test_delete_purges_payload_history_and_shares(service,actor,profile):
    build,_,_=await service.new(actor,"Confidentiel",profile,operation="create")
    code=(await service.share(actor,build,"share"))["code"]
    await service.delete(actor,build.id,1,"delete")
    await service.delete(actor,build.id,1,"delete")
    assert build.id not in service.repository.history
    assert not service.repository.shares
    assert "Confidentiel" not in canonical(list(service.repository.operations.values()))
    with pytest.raises(NotFound):
        await service.repository.shared(actor,code)


@pytest.mark.asyncio
async def test_history_bounded(service,actor,profile):
    b,_,_=await service.new(actor,"v0",profile,operation="create")
    for i in range(25):
        p=await service.rename(actor,b.id,b.revision,f"v{i+1}")
        b,_,_=await service.apply(actor,p,f"rename:{i}")
    assert len(service.repository.history[b.id])==20
    assert b.revision==26


@pytest.mark.asyncio
async def test_snapshot_append_only_collision(service,catalog):
    with pytest.raises(BuildError,match="Collision"):
        await service.repository.put_snapshot("catalog",catalog.id,"different")
    assert await service.repository.snapshot("catalog",catalog.id)==canonical(catalog)


@pytest.mark.asyncio
async def test_pinned_catalog_and_explicit_migration(service,catalog,actor,profile,item_factory):
    b,_,_=await service.new(actor,"Pinned",profile,operation="create")
    p=await service.equipment(actor,b.id,1,"coiffe","item:1")
    b,_,_=await service.apply(actor,p,"equip")
    changed=freeze_catalog([item_factory(1,"coiffe",{"fo":99})])
    await service.repository.put_snapshot("catalog",changed.id,canonical(changed))
    service.catalogs.latest=changed
    _,report,_=await service.inspect(actor,b.id)
    assert report.totals["fo"]==30
    migration=await service.migrate(actor,b.id)
    assert migration.report.totals["fo"]==99
    assert (await service.repository.get(actor,b.id)).catalog_id==catalog.id
    b,report,_=await service.apply(actor,migration,"migrate")
    assert b.catalog_id==changed.id and report.totals["fo"]==99


@pytest.mark.asyncio
async def test_fm_service_accepts_typed_tuples(service,actor,profile):
    b,_,_=await service.new(actor,"FM",profile,operation="create")
    p=await service.equipment(actor,b.id,1,"coiffe","item:1")
    b,_,_=await service.apply(actor,p,"equip")
    p=await service.jets(actor,b.id,b.revision,"coiffe","declared_fm",(EffectValue(ref="e0",value=35),),values({"pa":1}))
    assert p.report.totals["fo"]==35 and p.report.totals["pa"]==8
    assert p.report.nature=="declared"


@pytest.mark.asyncio
async def test_import_needs_known_local_dependencies(service,actor,build):
    raw=export_build(revised(build,catalog_id="f"*64))
    with pytest.raises(BuildError):
        await service.importing(actor,raw)


def test_export_omits_identifiers_and_import_changes_identity(build,actor,catalog):
    build=build.with_slot("coiffe",equip(catalog.items[0]))
    raw=export_build(build)
    assert b'owner_id' not in raw and b'guild_id' not in raw
    result=import_build(raw,actor)
    assert result.id!=build.id and result.owner_id==actor.user_id
    assert result.in_slot("coiffe").id!=build.in_slot("coiffe").id
    assert result.in_slot("coiffe").template_revision==build.in_slot("coiffe").template_revision


@pytest.mark.parametrize(
    "raw",
    [b'{"a":1,"a":2}', b'{"a":NaN}', b'['*20+b'0'+b']'*20,
     b'x'*262145, b'not json', b'\xff'],
    ids=["duplicate-key", "non-finite", "too-deep", "too-large", "invalid-json", "invalid-utf8"],
)
def test_import_json_is_bounded_strict(raw):
    with pytest.raises(BuildError):
        parse_json(raw)


@pytest.mark.asyncio
async def test_explicit_import_current_catalog_checks_every_item_revision(service,actor,build,catalog):
    exported=revised(build,catalog_id="f"*64).with_slot("coiffe",equip(catalog.items[0]))
    preview=await service.importing(actor,export_build(exported),use_current=True)
    assert preview.candidate.catalog_id==catalog.id
    assert len(await service.repository.list(actor))==0  # toujours à confirmer
    wrong=exported.with_slot("coiffe",revised(exported.in_slot("coiffe"),template_revision="a"*64))
    with pytest.raises(BuildError):
        await service.importing(actor,export_build(wrong),use_current=True)
