"""Jeu de données SYNTHÉTIQUE : prouve le code, jamais les valeurs de Dofus Retro."""
from types import SimpleNamespace
import pytest
import pytest_asyncio
from utils.build.models import (Actor, Profile, Build, ItemTemplate, Effect, STAT_LABELS,
                                values, digest, canonical)
from utils.build.catalog import freeze_catalog
from utils.build.rules import load_rules, Rules
from utils.build.repository import MemoryRepository
from utils.build.service import BuildService


def make_item(identifier=1, slot="coiffe", stats=None, **kwargs):
    effects = tuple(Effect(ref=f"e{n}", kind="stat", stat=k, low=v, high=v, text=f"{v} {k}")
                    for n, (k, v) in enumerate((stats or {}).items()))
    if "effects" in kwargs:
        effects = kwargs.pop("effects")
    slots = ("anneau_1", "anneau_2") if slot.startswith("anneau") else (slot,)
    item = ItemTemplate(ref=f"item:{identifier}", revision="0"*64, name=f"Objet synthétique {identifier}",
                        category=slot, level=1, allowed_slots=slots, effects=effects,
                        source="fixture://SYNTHETIC-NOT-GAME-DATA", conditions_known=True,
                        unique=kwargs.pop("unique", False), **kwargs)
    data = item.model_dump(mode="json"); data.pop("revision")
    return ItemTemplate(revision=digest(data), **data)


@pytest.fixture
def item_factory():
    return make_item


@pytest.fixture
def rules():
    body = load_rules().model_dump(mode="json"); body.pop("id")
    body.update(version="synthetic-fixture-ONLY", fixtures=["SYNTHETIC-NOT-A-GAME-REFERENCE"],
                sources=["fixture://synthetic"], base_verified=True, allocation_verified=True,
                derivatives_verified=True, restrictions_verified=True, stat_conditions="final",
                base_pv=50, pv_per_level=5)
    return Rules(id=digest(body), **body)


@pytest.fixture
def actor():
    return Actor(guild_id=100, user_id=200)


@pytest.fixture
def profile():
    stats = {s: 0 for s in STAT_LABELS}
    stats.update(pa=7, pm=3, pv=100, pp=100, invo=1)
    return Profile(classe="enutrof", level=200, mode="declared", naked_stats=values(stats))


@pytest.fixture
def catalog():
    return freeze_catalog([make_item(1, "coiffe", {"fo": 30}), make_item(2, "cape", {"pa": 1}),
                           make_item(3, "coiffe", {"fo": 50})])


@pytest.fixture
def build(actor, profile, catalog, rules):
    return Build(guild_id=actor.guild_id, owner_id=actor.user_id, name="Essai synthétique",
                 catalog_id=catalog.id, rules_id=rules.id, profile=profile)


@pytest_asyncio.fixture
async def service(catalog, rules):
    repo = MemoryRepository()
    await repo.open()
    await repo.put_snapshot("catalog", catalog.id, canonical(catalog))
    service = BuildService(repo, SimpleNamespace(latest=catalog), rules)
    await service.start()
    return service


@pytest.fixture(autouse=True)
def builder_without_maintenance(monkeypatch):
    """Je conserve les tests métier pour la future remise en service du builder."""
    monkeypatch.setattr("utils.command_policy.BUILD_MAINTENANCE", False)
