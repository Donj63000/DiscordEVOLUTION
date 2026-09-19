"""Catalogue de sorts Xixou chargé à la demande et archivé dans le dépôt."""
from __future__ import annotations

import asyncio
from functools import cached_property
import logging
import re
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, model_validator

from utils.dofus_wiki import WikiError
from utils.xixou_api import identity_key
from .combat import AttackDefinition, AttackLine, freeze_attack
from .effects import parse_line
from .models import BuildError, Frozen, Hash, canonical, digest

log = logging.getLogger(__name__)
SPELL_SOURCE = "https://xixou.io/les-outils/api/"


class SpellCatalog(Frozen):
    schema_version: Literal[1] = 1
    id: Hash
    source_hash: Hash
    source: str = SPELL_SOURCE
    generated_at: str = ""
    normalizer: str = "evolution-spells-1.0"
    attacks: Annotated[tuple[AttackDefinition, ...], Field(max_length=12000)]
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def identities(self):
        if len({(a.id, a.level) for a in self.attacks}) != len(self.attacks):
            raise ValueError("Sort et niveau dupliqués.")
        return self

    @cached_property
    def by_level(self):
        return MappingProxyType({(a.id, a.level): a for a in self.attacks})


def verify_spells(catalog):
    """Je vérifie le catalogue et chaque définition avant toute réutilisation."""
    body = catalog.model_dump(mode="json")
    if body.pop("id") != digest(body):
        raise BuildError("Empreinte du catalogue de sorts incohérente.")
    for attack in catalog.attacks:
        body = attack.model_dump(mode="json")
        if body.pop("revision") != digest(body):
            raise BuildError("Empreinte d'un sort incohérente.")


def _integer(value, *, minimum=0, maximum=10000):
    if isinstance(value, bool) or not re.fullmatch(r"\d{1,9}", str(value)):
        return None
    number = int(value)
    return number if minimum <= number <= maximum else None


def _denominator(value):
    if isinstance(value, bool):
        return None
    if value in ("-", "—", "Aucun", "Aucune", "0", 0):
        return 0
    match = re.fullmatch(r"1\s*/\s*([0-9]{1,5})", str(value))
    return _integer(match[1], minimum=2) if match else None


def _lines(rows):
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("Liste d'effets invalide.")
    lines, unsupported = [], []
    for index, row in enumerate(rows):
        text = row.get("text") if isinstance(row, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 600:
            unsupported.append("Effet source absent ou illisible.")
            continue
        effect = parse_line(text, index)
        healing = re.fullmatch(r"PDV rendus\s*:\s*(\d+)(?:\s*à\s*(\d+))?", text, re.I)
        if effect.kind in {"damage", "steal"}:
            lines.append(AttackLine(kind=effect.kind, element=effect.element,
                                    minimum=effect.low, maximum=effect.high, text=text))
        elif healing:
            lines.append(AttackLine(kind="heal", minimum=int(healing[1]),
                                    maximum=int(healing[2] or healing[1]), text=text))
        else:
            unsupported.append(text)
    if len(lines) > 16:
        raise ValueError("Plus de seize lignes d'attaque.")
    if any(l.kind == "heal" for l in lines) and any(l.kind != "heal" for l in lines):
        unsupported.append("Dégâts et soins combinés : destinataires ou branches conditionnelles non documentés.")
    return tuple(lines), tuple(unsupported)


def normalize_spells(payload):
    """Je lis la structure observée de sorts.json, sans reconstruire des sorts manquants."""
    if not isinstance(payload, dict) or payload.get("famille") != "sorts":
        raise BuildError("Enveloppe du catalogue de sorts inattendue.")
    if len(canonical(payload).encode("utf-8")) > 16 * 1024 * 1024:
        raise BuildError("Catalogue de sorts trop volumineux.")
    data = payload.get("data")
    block = data.get("spells") if isinstance(data, dict) else None
    rows = block.get("spells") if isinstance(block, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > 3000:
        raise BuildError("Structure des sorts Xixou inconnue ; ancienne version conservée.")
    attacks, diagnostics, duplicate_ids, seen = {}, [], set(), set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            diagnostics.append(f"Ligne {index} exclue : objet attendu.")
            continue
        identifier = _integer(row.get("id"), minimum=1, maximum=10**9 - 1)
        if identifier in seen:
            duplicate_ids.add(identifier)
        seen.add(identifier)
        name, levels = row.get("name"), row.get("levels")
        if identifier is None or not isinstance(name, str) or not 1 <= len(name) <= 180 or not isinstance(levels, dict):
            diagnostics.append(f"Ligne {index} exclue : identité ou niveaux invalides.")
            continue
        conditions = tuple(f"{label} : {row[key]}" for key, label in (
            ("required_states", "États requis"), ("forbidden_states", "États interdits"))
            if isinstance(row.get(key), str) and row[key] not in {"Aucun", "Aucune", "-", ""})
        for rawlevel, leveldata in levels.items():
            level = _integer(rawlevel, minimum=1, maximum=6)
            try:
                if level is None or not isinstance(leveldata, dict):
                    raise ValueError("Niveau invalide.")
                effects = leveldata.get("effects")
                if not isinstance(effects, dict):
                    raise ValueError("Effets absents.")
                normal, unsupported = _lines(effects.get("normal"))
                critical, critical_unsupported = (None, ())
                if "critical" in effects:
                    critical, critical_unsupported = _lines(effects["critical"])
                other = leveldata.get("other_characteristics")
                other = other if isinstance(other, dict) else {}
                denominator = _denominator(other.get("critical_hit_probability"))
                attack = freeze_attack(
                    id=f"spell:{identifier}", name=name, level=level,
                    required_level=_integer(leveldata.get("required_level"), minimum=1, maximum=200),
                    normal_lines=normal, critical_lines=critical,
                    critical_denominator=denominator,
                    failure_denominator=_denominator(other.get("critical_failure_probability")),
                    ap_cost=_integer(leveldata.get("ap_cost"), maximum=100),
                    unsupported_effects=unsupported, critical_unsupported_effects=critical_unsupported,
                    conditions=conditions, source=SPELL_SOURCE)
                attacks[(identifier, level)] = attack
                if unsupported or critical_unsupported:
                    diagnostics.append(f"spell:{identifier}/{level} : effets non calculables conservés.")
            except (ValueError, TypeError) as exc:
                diagnostics.append(f"spell:{identifier}/{rawlevel} exclu : {type(exc).__name__}.")
    for identifier in duplicate_ids:
        attacks = {key: value for key, value in attacks.items() if key[0] != identifier}
        diagnostics.append(f"spell:{identifier} exclu : identité dupliquée.")
    if not attacks:
        raise BuildError("Aucun sort normalisé ; ancienne version conservée.")
    body = dict(schema_version=1, source_hash=digest(payload), source=SPELL_SOURCE,
                generated_at=str(payload.get("genere_le", ""))[:100], normalizer="evolution-spells-1.0",
                attacks=[attacks[key].model_dump(mode="json") for key in sorted(attacks)],
                diagnostics=diagnostics)
    catalog = SpellCatalog(id=digest(body), **body)
    verify_spells(catalog)
    return catalog


class SpellCatalogService:
    """Les commandes wiki ordinaires ne sollicitent jamais ce service."""

    def __init__(self, repository, api_provider):
        self.repository = repository
        self.api_provider = api_provider
        self.latest = None
        self.lock = asyncio.Lock()
        self.last_error = ""

    async def restore(self):
        payload = await self.repository.latest_snapshot("spells")
        if payload:
            try:
                catalog = SpellCatalog.model_validate_json(payload)
                verify_spells(catalog)
            except ValueError as exc:
                raise BuildError("Archive de sorts invalide ; restauration refusée.") from exc
            self.latest = catalog
            log.debug("Catalogue sorts restauré id=%s attaques=%d", catalog.id, len(catalog.attacks))
        return self.latest

    async def ensure(self):
        return self.latest if self.latest is not None else await self.refresh(only_if_absent=True)

    async def refresh(self, *, only_if_absent=False):
        async with self.lock:
            if self.latest is None:
                await self.restore()
            if only_if_absent and self.latest is not None:
                return self.latest
            try:
                api = self.api_provider()
                if api is None or not getattr(api, "enabled", False):
                    raise BuildError("Catalogue de sorts indisponible : clé Xixou non configurée.")
                async with asyncio.timeout(45):
                    payload = await api.catalog("sorts")
                candidate = await asyncio.to_thread(normalize_spells, payload)
                await self.repository.put_snapshot("spells", candidate.id, canonical(candidate))
                restored = SpellCatalog.model_validate_json(await self.repository.snapshot("spells", candidate.id))
                verify_spells(restored)
                if restored.id != candidate.id:
                    raise BuildError("Archive de sorts différente de la version demandée.")
                self.latest, self.last_error = restored, ""
                log.debug("Catalogue sorts archivé id=%s attaques=%d", restored.id, len(restored.attacks))
            except (BuildError, WikiError, ValueError, OSError, TimeoutError) as exc:
                self.last_error = "Actualisation des sorts impossible ; dernière archive conservée."
                log.debug("Actualisation sorts refusée erreur=%s archive=%s", type(exc).__name__,
                          self.latest.id if self.latest else "absente")
                if self.latest is None:
                    raise BuildError(self.last_error) from exc
            return self.latest

    async def get(self, catalog_id):
        if self.latest is not None and self.latest.id == catalog_id:
            return self.latest
        try:
            catalog = SpellCatalog.model_validate_json(await self.repository.snapshot("spells", catalog_id))
            verify_spells(catalog)
            if catalog.id != catalog_id:
                raise BuildError("Archive de sorts différente de la version demandée.")
        except ValueError as exc:
            raise BuildError("Archive de sorts invalide ; restauration refusée.") from exc
        return catalog

    async def attack(self, catalog_id, ref, level):
        catalog = await self.get(catalog_id)
        attack = catalog.by_level.get((ref, level))
        if attack is None:
            raise BuildError("Sort ou niveau absent de cette version du catalogue.")
        await self.repository.put_snapshot("attacks", attack.revision, canonical(attack))
        return attack

    async def search(self, query="", classe=None, limit=25):
        catalog = await self.ensure()
        key = identity_key(query)
        matches = (a for a in catalog.attacks if key in identity_key(a.name)
                   and (classe is None or a.classe in {None, classe}))
        return tuple(sorted(matches, key=lambda a: (identity_key(a.name), a.level))[:max(1, min(25, limit))])
