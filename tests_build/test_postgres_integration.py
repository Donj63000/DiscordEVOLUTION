"""PostgreSQL RÉEL requis. Ne ciblez JAMAIS la base de production.

Activer BUILD_TEST_DATABASE_URL sur une base vide/dédiée, installée par la CI.
Les tests ne présentent pas les mocks mémoire comme une preuve SQL.
"""
import asyncio
import os
from uuid import uuid4
import pytest
import pytest_asyncio
from utils.build.models import Actor, Build, Conflict, NotFound, canonical, digest, revised
from utils.build.postgres_store import PostgresRepository

DSN=os.getenv("BUILD_TEST_DATABASE_URL", "")
pytestmark=pytest.mark.skipif(not DSN, reason="PostgreSQL de test dédié absent (BUILD_TEST_DATABASE_URL).")


@pytest_asyncio.fixture
async def pg(catalog,rules):
    pytest.importorskip("asyncpg",reason="asyncpg non installé dans cet environnement.")
    repo=PostgresRepository(DSN,quota=2,migrate=True)
    await repo.open()
    await repo.put_snapshot("catalog",catalog.id,canonical(catalog))
    await repo.put_snapshot("rules",rules.id,canonical(rules))
    yield repo
    await repo.close()


def unique_actor():
    return Actor(guild_id=uuid4().int % (2**60)+1, user_id=uuid4().int % (2**60)+1)


@pytest.mark.asyncio
async def test_pg_survives_pool_close_and_reopen(pg,build):
    actor=unique_actor(); candidate=revised(build,guild_id=actor.guild_id,owner_id=actor.user_id)
    saved=await pg.commit(actor,candidate,None,"create",digest("create"),digest("report"))
    await pg.close();await pg.open()
    assert await pg.get(actor,saved.id)==saved
    await pg.delete(actor,saved.id,1,"delete",digest("delete"))


@pytest.mark.asyncio
async def test_pg_two_independent_pools_cas_and_idempotence(pg,build):
    other=PostgresRepository(DSN,quota=2);await other.open()
    actor=unique_actor();candidate=revised(build,guild_id=actor.guild_id,owner_id=actor.user_id)
    try:
        saved=await pg.commit(actor,candidate,None,"create",digest("create"),digest("report"))
        results=await asyncio.gather(*(repo.commit(actor,revised(saved,name=name),1,name,digest(name),digest("report")) for repo,name in ((pg,"a"),(other,"b"))),return_exceptions=True)
        assert sum(isinstance(r,Conflict) for r in results)==1
        assert (await pg.get(actor,saved.id)).revision==2
        previous=await other.commit(actor,candidate,None,"create",digest("create"),digest("report"))
        assert previous==saved
        with pytest.raises(Conflict):
            await other.commit(actor,candidate,None,"create",digest("other request"),digest("report"))
        with pytest.raises(NotFound):
            await other.get(unique_actor(),saved.id)
        await pg.delete(actor,saved.id,2,"delete",digest("delete"))
    finally:await other.close()


@pytest.mark.asyncio
async def test_pg_share_claim_and_rollback(pg,build):
    actor=unique_actor();b=revised(build,guild_id=actor.guild_id,owner_id=actor.user_id)
    b=await pg.commit(actor,b,None,"create",digest("create"),digest("report"))
    receipt=await pg.share(actor,b,"share",digest("share"));code=receipt["code"]
    assert await pg.claim_publication(actor,code)
    await pg.publication(actor,code,"sent",123,456)
    assert await pg.claim_publication(actor,code) is False
    with pytest.raises(Conflict):
        await pg.commit(actor,revised(b,name="stale"),99,"conflict",digest("conflict"),digest("report"))
    assert await pg.replay(actor,"conflict",digest("conflict")) is None
    assert (await pg.shared(actor,code)).name==b.name
    await pg.delete(actor,b.id,1,"delete",digest("delete"))
    with pytest.raises(NotFound):await pg.shared(actor,code)


@pytest.mark.asyncio
async def test_pg_history_owner_filter_and_new_revision(pg, build):
    actor = unique_actor()
    candidate = revised(build, guild_id=actor.guild_id, owner_id=actor.user_id)
    first = await pg.commit(actor, candidate, None, 'hist-create', digest('create'), digest('report'))
    second = await pg.commit(actor, revised(first, name='Autre nom'), first.revision,
                             'hist-edit', digest('edit'), digest('report'))
    assert [b.revision for b in await pg.revisions(actor, first.id)] == [2, 1]
    with pytest.raises(NotFound):
        await pg.revisions(unique_actor(), first.id)
    with pytest.raises(NotFound):
        await pg.revisions(actor, 'not-an-uuid')
    third = await pg.commit(actor, revised(second, name=first.name), second.revision,
                            'hist-restore', digest('restore'), digest('report'))
    assert third.revision == 3
    assert [b.name for b in await pg.revisions(actor, first.id)] == [first.name, 'Autre nom', first.name]
    await pg.delete(actor, first.id, third.revision, 'hist-delete', digest('delete'))
