"""Contrat Xixou observé, avec valeurs synthétiques et aucune connexion réseau."""
from copy import deepcopy
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils.build.models import BuildError, canonical, revised
from utils.build.repository import MemoryRepository
from utils.build.spells import SpellCatalogService, normalize_spells, verify_spells


def payload_with(normal=None, critical=None):
    return {"famille": "sorts", "genere_le": "2026-09-20", "data": {"spells": {
        "lang": "fr", "spells": [{"id": 3, "name": "Sort synthétique #3",
        "required_states": "Aucun", "forbidden_states": "Saoul",
        "levels": {"1": {"required_level": "1", "ap_cost": "3",
            "effects": {"normal": normal if normal is not None else [
                {"text": "Dommages : 9 à 13 (feu)", "area": None}],
                "critical": critical if critical is not None else [{"text": "Dommages : 15 (feu)"}]},
            "other_characteristics": {"critical_hit_probability": "1/50",
                                       "critical_failure_probability": "1/100"}}}}]}}}


def test_observed_nested_envelope_is_normalized():
    catalog = normalize_spells(payload_with())
    verify_spells(catalog)
    attack = catalog.by_level[("spell:3", 1)]
    assert attack.normal_lines[0].minimum == 9
    assert attack.critical_lines[0].maximum == 15
    assert attack.critical_denominator == 50
    assert attack.failure_denominator == 100
    assert attack.conditions == ("États interdits : Saoul",)
    assert attack.classe is None


@pytest.mark.parametrize("text,kind,element", [("Vole 31 à 40 PDV (eau)", "steal", "ea"),
                                              ("PDV rendus : 11 à 14", "heal", None)])
def test_heal_and_steal_forms(text, kind, element):
    attack = normalize_spells(payload_with(normal=[{"text": text}])).attacks[0]
    assert attack.normal_lines[0].kind == kind
    assert attack.normal_lines[0].element == element


def test_special_effects_and_original_text_are_preserved():
    text = "Dommages : 30% de la vie de l'attaquant (neutre)"
    attack = normalize_spells(payload_with(normal=[{"text": text}])).attacks[0]
    assert attack.normal_lines == ()
    assert attack.unsupported_effects == (text,)


def test_mixed_healing_and_damage_require_known_target_semantics():
    attack = normalize_spells(payload_with(normal=[{"text": "Dommages : 6 à 19 (terre)"},
                                                       {"text": "PDV rendus : 5 à 12"}])).attacks[0]
    assert len(attack.normal_lines) == 2
    assert attack.unsupported_effects


def test_missing_critical_data_is_not_zero_probability():
    payload = payload_with()
    level = payload["data"]["spells"]["spells"][0]["levels"]["1"]
    level["effects"].pop("critical")
    level["other_characteristics"].pop("critical_hit_probability")
    attack = normalize_spells(payload).attacks[0]
    assert attack.critical_lines is None
    assert attack.critical_denominator is None


def test_duplicate_spell_identity_rejected():
    payload = payload_with()
    payload["data"]["spells"]["spells"] *= 2
    with pytest.raises(BuildError, match="Aucun sort"):
        normalize_spells(payload)


def test_corrupt_snapshot_is_refused():
    catalog = normalize_spells(payload_with())
    with pytest.raises(BuildError, match="Empreinte"):
        verify_spells(revised(catalog, generated_at="changed"))


@pytest.mark.parametrize("payload", [{}, {"famille": "sorts", "data": []},
                                      {"famille": "sorts", "data": {"spells": {"spells": []}}}])
def test_unknown_envelope_is_not_guessed(payload):
    with pytest.raises(BuildError):
        normalize_spells(payload)


class SnapshotRepository(MemoryRepository):
    async def put_snapshot(self, kind, identifier, payload):
        self.snapshots[kind, identifier] = payload


@pytest.mark.asyncio
async def test_explicit_refresh_replaces_catalog_but_preserves_selected_attack():
    repo = SnapshotRepository()
    api = SimpleNamespace(enabled=True, catalog=AsyncMock(return_value=payload_with()))
    service = SpellCatalogService(repo, lambda: api)
    previous = await service.ensure()
    attack = await service.attack(previous.id, "spell:3", 1)
    api.catalog.return_value = payload_with(normal=[{"text": "Dommages : 50 (feu)"}])
    refreshed = await service.refresh()
    assert refreshed.id != previous.id
    assert (await service.search())[0].normal_lines[0].minimum == 50
    assert await repo.snapshot("attacks", attack.revision) == canonical(attack)
    assert await service.attack(previous.id, "spell:3", 1) == attack


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["equipements", "sorts", "tous"])
async def test_refresh_command_routes_catalogs(scope, monkeypatch):
    import build as module
    monkeypatch.setenv("BUILD_ENABLED", "1")
    cog = SimpleNamespace(closed=False, ready=True, last_refresh_request=0,
        require_staff=lambda interaction: None, ensure_ready=lambda: None,
        initialize=AsyncMock(), init_lock=asyncio.Lock(), start_error="",
        catalogs=SimpleNamespace(lock=asyncio.Lock(), latest=None, last_error="Source équipements indisponible"),
        spells=SimpleNamespace(lock=asyncio.Lock(), refresh=AsyncMock(return_value=SimpleNamespace(attacks=(1,))), last_error=""))
    interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock())
    await module.BuildCog.refreshing.callback(cog, interaction, scope)
    assert cog.initialize.await_count == int(scope in {"equipements", "tous"})
    assert cog.spells.refresh.await_count == int(scope in {"sorts", "tous"})
    content = interaction.edit_original_response.call_args.kwargs["content"]
    if scope == "tous":
        assert "Source équipements indisponible" in content and "Sorts" in content


@pytest.mark.asyncio
async def test_catalog_is_lazy_archived_and_restored_without_network():
    repo = SnapshotRepository()
    api = SimpleNamespace(enabled=True, catalog=AsyncMock(return_value=payload_with()))
    service = SpellCatalogService(repo, lambda: api)
    api.catalog.assert_not_awaited()
    catalog = await service.ensure()
    api.catalog.assert_awaited_once_with("sorts")
    attack = await service.attack(catalog.id, "spell:3", 1)
    assert await repo.snapshot("attacks", attack.revision) == canonical(attack)
    restarted = SpellCatalogService(repo, lambda: None)
    assert (await restarted.ensure()).id == catalog.id
    assert len(await restarted.search("synthétique")) == 1


@pytest.mark.asyncio
async def test_source_failure_keeps_last_archived_catalog():
    repo = SnapshotRepository()
    api = SimpleNamespace(enabled=True, catalog=AsyncMock(return_value=payload_with()))
    service = SpellCatalogService(repo, lambda: api)
    catalog = await service.refresh()
    api.catalog.return_value = None
    assert await service.refresh() == catalog
    assert service.last_error


@pytest.mark.asyncio
async def test_failed_archive_never_updates_cache():
    repo = SnapshotRepository()
    api = SimpleNamespace(enabled=True, catalog=AsyncMock(return_value=payload_with()))
    service = SpellCatalogService(repo, lambda: api)
    catalog = await service.ensure()
    changed = deepcopy(payload_with())
    changed["genere_le"] = "new"
    api.catalog.return_value = changed
    repo.put_snapshot = AsyncMock(side_effect=BuildError("Écriture refusée"))
    assert await service.refresh() == catalog


@pytest.mark.asyncio
async def test_missing_api_without_archive_fails_clearly():
    with pytest.raises(BuildError, match="Actualisation"):
        await SpellCatalogService(SnapshotRepository(), lambda: None).ensure()


@pytest.mark.asyncio
async def test_concurrent_first_use_downloads_once():
    repo = SnapshotRepository()
    api = SimpleNamespace(enabled=True, catalog=AsyncMock(return_value=payload_with()))
    service = SpellCatalogService(repo, lambda: api)
    results = await asyncio.gather(service.ensure(), service.ensure(), service.ensure())
    assert len({c.id for c in results}) == 1
    api.catalog.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_after_restart_falls_back_to_saved_catalog():
    repo = SnapshotRepository()
    api = SimpleNamespace(enabled=True, catalog=AsyncMock(return_value=payload_with()))
    first = await SpellCatalogService(repo, lambda: api).ensure()
    restarted = SpellCatalogService(repo, lambda: None)
    assert await restarted.refresh() == first
    assert restarted.last_error


@pytest.mark.asyncio
async def test_refresh_keeps_previous_critical_definition_addressable():
    repo = SnapshotRepository()
    api = SimpleNamespace(enabled=True, catalog=AsyncMock(return_value=payload_with()))
    service = SpellCatalogService(repo, lambda: api)
    previous = await service.ensure()
    old_attack = await service.attack(previous.id, "spell:3", 1)
    api.catalog.return_value = payload_with(critical=[{"text": "Dommages : 25 (feu)"}])
    refreshed = await service.refresh()
    new_attack = await service.attack(refreshed.id, "spell:3", 1)
    restored_old = await service.attack(previous.id, "spell:3", 1)
    assert old_attack == restored_old
    assert old_attack.critical_lines[0].maximum == 15
    assert new_attack.critical_lines[0].maximum == 25
    assert new_attack.revision != old_attack.revision
