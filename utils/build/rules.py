"""Règles de données versionnées ; aucune expression arbitraire exécutable."""
from __future__ import annotations
from pathlib import Path
from typing import Annotated, Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .models import (Frozen, Hash, Text, Profile, Contribution, Diagnostic, PRIMARY,
                     CLASSES, STAT_LABELS, StatValue, as_stats, digest, BuildError)

class Band(Frozen):
    until: Annotated[StrictInt, Field(ge=1, le=10000)]
    cost: Annotated[StrictInt, Field(ge=1, le=5)]
    gain: Annotated[StrictInt, Field(ge=1, le=2)] = 1

class AllocationRule(Frozen):
    classe: str
    stat: str
    bands: Annotated[tuple[Band, ...], Field(min_length=1, max_length=5)]

    @model_validator(mode="after")
    def check(self):
        if self.classe not in CLASSES or self.stat not in PRIMARY:
            raise ValueError("Palier inconnu.")
        if [b.until for b in self.bands] != sorted({b.until for b in self.bands}):
            raise ValueError("Seuils non croissants.")
        if self.bands[-1].until != 10000:
            raise ValueError("Palier terminal manquant.")
        return self

class Rules(Frozen):
    schema_version: Literal[1] = 1
    id: Hash
    version: Text
    sources: tuple[Text, ...]
    allocations: tuple[AllocationRule, ...]
    base_pa: Annotated[StrictInt, Field(ge=0, le=20)] = 6
    level_pa: Annotated[StrictInt, Field(ge=1, le=200)] = 100
    base_pm: Annotated[StrictInt, Field(ge=0, le=20)] = 3
    base_pp: Annotated[StrictInt, Field(ge=0, le=1000)] = 100
    enutrof_pp: Annotated[StrictInt, Field(ge=0, le=1000)] = 120
    base_pv: Annotated[StrictInt, Field(ge=0, le=10000)] | None = None
    pv_per_level: Annotated[StrictInt, Field(ge=0, le=20)] | None = None
    base_verified: StrictBool = False
    allocation_verified: StrictBool = False
    derivatives_verified: StrictBool = False
    restrictions_verified: StrictBool = False
    stat_conditions: Literal["unknown", "final"] = "unknown"
    set_count: Literal["distinct_templates"] = "distinct_templates"
    notes: tuple[Annotated[str, Field(max_length=500)], ...] = ()
    fixtures: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def complete(self):
        keys = [(a.classe, a.stat) for a in self.allocations]
        if len(keys) != len(set(keys)):
            raise ValueError("Palier dupliqué.")
        if set(keys) != {(c, s) for c in CLASSES for s in PRIMARY}:
            raise ValueError("Les 12 classes et 6 caractéristiques sont requises.")
        if any((self.base_verified, self.allocation_verified, self.derivatives_verified,
                self.restrictions_verified)) and not self.fixtures:
            raise ValueError("Références de recette requises avant validation des règles.")
        return self

    def allocated_value(self, classe: str, stat: str, spent: int, scroll: int) -> int:
        rule = next(r for r in self.allocations if (r.classe, r.stat) == (classe, stat))
        total = scroll
        while spent:
            band = next((b for b in rule.bands if total < b.until), None)
            if band is None or spent < band.cost:
                raise BuildError(f"Investissement {stat} non dépensable exactement avec ces paliers.")
            spent -= band.cost
            total += band.gain
        return total


def load_rules(path: str | Path | None = None) -> Rules:
    path = Path(path) if path else Path(__file__).resolve().parents[2] / "data/build/retro_rules_v1.json"
    raw = path.read_bytes()
    if len(raw) > 256 * 1024:
        raise BuildError("Fichier de règles trop volumineux.")
    import json
    obj = json.loads(raw)
    obj["id"] = digest({k: v for k, v in obj.items() if k != "id"})
    candidate = Rules.model_validate(obj)
    body = candidate.model_dump(mode="json")
    body.pop("id")
    return Rules(id=digest(body), **body)


def character(profile: Profile, rules: Rules):
    ledger, unknown, warnings = [], set(), []
    if profile.mode == "declared":
        totals = as_stats(profile.naked_stats)
        unknown = set(STAT_LABELS) - totals.keys()
        warnings.append(Diagnostic(code="DECLARED_PROFILE", text="Statistiques hors équipement déclarées, non relevées automatiquement dans le jeu."))
    else:
        totals = {s: 0 for s in STAT_LABELS}
        totals.update(pa=rules.base_pa + int(profile.level >= rules.level_pa), pm=rules.base_pm,
                      pp=rules.enutrof_pp if profile.classe == "enutrof" else rules.base_pp, invo=1)
        spent, scroll = as_stats(profile.allocated), as_stats(profile.scrolled)
        for stat in PRIMARY:
            totals[stat] = rules.allocated_value(profile.classe, stat, spent.get(stat, 0), scroll.get(stat, 0))
        if not rules.base_verified:
            unknown.update(set(STAT_LABELS) - set(PRIMARY))
            warnings.append(Diagnostic(code="BASE_RULES_BETA", text="Bases PA/PM/PP provisoires : recette en jeu absente. Pas une certification Retro."))
        if not rules.allocation_verified:
            unknown.update(PRIMARY)
            warnings.append(Diagnostic(code="ALLOCATION_BETA", text="Paliers transcrits depuis Xixou ; interaction parchottage et cas de référence à valider en jeu."))
        if rules.base_pv is None or rules.pv_per_level is None:
            unknown.add("pv")
        else:
            totals["pv"] = rules.base_pv + rules.pv_per_level * (profile.level - 1)
        # Les métriques dérivées partent de leurs composantes de base, pas d'un total déjà dérivé.
        unknown.update({"ini", "pod"})  # métiers/vie courante non modélisés
    for stat, value in totals.items():
        ledger.append(Contribution(origin="personnage", stat=stat, value=value))
    return ledger, unknown, warnings


def derive(totals, base, profile, rules, unknown):
    """Delta lorsque le profil nu inclut déjà ses dérivées. Entiers uniquement."""
    ledger = []
    def add(stat, value):
        totals[stat] = totals.get(stat, 0) + value
        ledger.append(Contribution(origin="derive:" + stat, stat=stat, value=value))
    if profile.mode == "rules":
        add("pp", max(0, totals.get("cha", 0)) // 10)
        add("pv", totals.get("vi", 0))
    else:
        if "pp" in base and "cha" in base:
            add("pp", max(0, totals.get("cha", 0)) // 10 - max(0, base["cha"]) // 10)
        else:
            unknown.add("pp")
        if "pv" in base and "vi" in base:
            add("pv", totals.get("vi", 0) - base["vi"])
        else:
            unknown.add("pv")
    if "cha" in unknown or not rules.derivatives_verified:
        unknown.add("pp")
    if "vi" in unknown or not rules.derivatives_verified:
        unknown.add("pv")
    return ledger
