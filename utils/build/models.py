"""Contrats immuables, bornés et sérialisables (aucun objet Discord conservé)."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import cached_property
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

Int = Annotated[StrictInt, Field(ge=-10000, le=10000)]
Positive = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]
Text = Annotated[str, Field(min_length=1, max_length=180)]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Slot = Literal["coiffe", "cape", "amulette", "ceinture", "bottes", "anneau_1", "anneau_2", "arme", "bouclier", "compagnon", "dofus_1", "dofus_2", "dofus_3", "dofus_4", "dofus_5", "dofus_6"]
SLOTS = ("coiffe", "cape", "amulette", "ceinture", "bottes", "anneau_1", "anneau_2", "arme", "bouclier", "compagnon", *(f"dofus_{i}" for i in range(1, 7)))
PRIMARY = ("vi", "sa", "fo", "ine", "cha", "age")
CLASSES = ("feca", "osamodas", "enutrof", "sram", "xelor", "ecaflip", "eniripsa", "iop", "cra", "sadida", "sacrieur", "pandawa")
STAT_LABELS = {
    "vi": "Vitalité", "sa": "Sagesse", "fo": "Force", "ine": "Intelligence", "cha": "Chance", "age": "Agilité",
    "pa": "PA", "pm": "PM", "po": "Portée", "pv": "Points de vie", "pp": "Prospection", "ini": "Initiative",
    "pod": "Pods", "invo": "Invocations", "do": "Dommages", "pui": "Dommages %", "so": "Soins", "cc": "Bonus critiques",
    "pi": "Dommages pièges", "pi_per": "Dommages pièges %", "ren": "Renvoi dommages",
    **{f"r_{e}": f"Résistance {n}" for e, n in (("ne", "neutre"), ("te", "terre"), ("fe", "feu"), ("ea", "eau"), ("ai", "air"))},
    **{f"rp_{e}": f"Résistance {n} %" for e, n in (("ne", "neutre"), ("te", "terre"), ("fe", "feu"), ("ea", "eau"), ("ai", "air"))},
}


class BuildError(ValueError):
    """Message public, sans clé/DSN/trace ni contenu d'un autre utilisateur."""


class Conflict(BuildError):
    pass


class NotFound(BuildError):
    def __init__(self):
        super().__init__("Build ou partage introuvable dans ton espace autorisé.")


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True, str_strip_whitespace=True)


def canonical(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    def encode_nested(obj):
        if isinstance(obj, BaseModel):
            return obj.model_dump(mode="json")
        raise TypeError(f"Type non sérialisable : {type(obj).__name__}")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=encode_nested)


def digest(value) -> str:
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def revised(model, **changes):
    """model_copy(update=...) contourne la validation Pydantic : ne pas l'utiliser ici."""
    value = model.model_dump(mode="json")
    for key, item in changes.items():
        value[key] = json.loads(canonical(item)) if isinstance(item, BaseModel) else item
    return type(model).model_validate_json(canonical(value))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


def uuid_text(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("Identifiant UUID invalide.")
    return value


class StatValue(Frozen):
    stat: str
    value: Int

    @field_validator("stat")
    @classmethod
    def known(cls, value):
        if value not in STAT_LABELS:
            raise ValueError("Caractéristique inconnue.")
        return value


def values(mapping: dict[str, int]) -> tuple[StatValue, ...]:
    return tuple(StatValue(stat=k, value=v) for k, v in sorted(mapping.items()))


def as_stats(rows) -> dict[str, int]:
    result = {row.stat: row.value for row in rows}
    if len(result) != len(rows):
        raise ValueError("Caractéristique en double.")
    return result


class Actor(Frozen):
    guild_id: Positive
    user_id: Positive


class Profile(Frozen):
    classe: str
    level: Annotated[StrictInt, Field(ge=1, le=200)]
    allocated: tuple[StatValue, ...] = ()  # points DEPENSES, non caractéristiques gagnées
    scrolled: tuple[StatValue, ...] = ()
    mode: Literal["rules", "declared"] = "rules"
    naked_stats: tuple[StatValue, ...] = ()  # profil déclaré : valeurs hors équipement, parcho inclus
    alignment: Annotated[StrictInt, Field(ge=0, le=3)] | None = None
    grade: Annotated[StrictInt, Field(ge=0, le=10)] | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.classe not in CLASSES:
            raise ValueError("Classe Retro inconnue.")
        allocated, scrolled = as_stats(self.allocated), as_stats(self.scrolled)
        if any(k not in PRIMARY or v < 0 for k, v in allocated.items()):
            raise ValueError("Points investis invalides.")
        if any(k not in PRIMARY or not 0 <= v <= 101 for k, v in scrolled.items()):
            raise ValueError("Parchottage attendu entre 0 et 101.")
        if sum(allocated.values()) > 5 * (self.level - 1):
            raise ValueError("Plus de points investis que les points disponibles.")
        as_stats(self.naked_stats)
        if self.mode == "declared" and (allocated or scrolled):
            raise ValueError("Le profil déclaré inclut déjà investissement et parchottage.")
        if self.mode == "rules" and self.naked_stats:
            raise ValueError("Choisir le mode déclaré pour saisir des statistiques hors équipement.")
        return self


class Effect(Frozen):
    ref: Annotated[str, Field(min_length=1, max_length=60)]
    kind: Literal["stat", "damage", "steal", "heal", "context", "unknown", "cosmetic"]
    stat: str | None = None
    low: Int = 0
    high: Int = 0
    text: Annotated[str, Field(max_length=600)] = ""
    element: Literal["ne", "te", "fe", "ea", "ai"] | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.low > self.high:
            raise ValueError("Bornes inversées.")
        if self.kind == "stat" and self.stat not in STAT_LABELS:
            raise ValueError("Effet statique sans caractéristique.")
        if self.kind in {"damage", "steal"} and self.element is None:
            raise ValueError("Ligne de dégâts sans élément.")
        return self


class RecipeLine(Frozen):
    name: Text
    ref: Annotated[str, Field(max_length=160)] = ""
    quantity: Annotated[StrictInt, Field(ge=1, le=100000)]


class ItemTemplate(Frozen):
    ref: Annotated[str, Field(pattern=r"^(?:item:[0-9]{1,12}|xixou:[a-f0-9]{24})$")]
    revision: Hash
    name: Text
    category: Text
    level: Annotated[StrictInt, Field(ge=1, le=200)]
    allowed_slots: Annotated[tuple[Slot, ...], Field(min_length=1, max_length=6)]
    effects: Annotated[tuple[Effect, ...], Field(max_length=100)]
    set_ref: Annotated[str, Field(max_length=180)] | None = None
    conditions: Annotated[tuple[Annotated[str, Field(max_length=600)], ...], Field(max_length=32)] = ()
    conditions_known: StrictBool = False
    source: Annotated[str, Field(max_length=400)] = "https://xixou.io/les-outils/api/"
    correction_sources: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=400)], ...], Field(max_length=8)] = ()
    warnings: Annotated[tuple[Annotated[str, Field(max_length=300)], ...], Field(max_length=32)] = ()
    two_handed: StrictBool | None = None
    unique: StrictBool | None = None
    recipe: Annotated[tuple[RecipeLine, ...], Field(max_length=100)] = ()
    recipe_known: StrictBool = False
    image_urls: Annotated[tuple[Annotated[str, Field(max_length=500)], ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def no_duplicates(self):
        if len({e.ref for e in self.effects}) != len(self.effects):
            raise ValueError("Identité d'effet dupliquée.")
        if len(set(self.allowed_slots)) != len(self.allowed_slots):
            raise ValueError("Emplacement dupliqué.")
        return self


class SetTier(Frozen):
    pieces: Annotated[StrictInt, Field(ge=1, le=16)]
    effects: Annotated[tuple[Effect, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def deterministic_bonus(self):
        if any(e.kind == "stat" and e.low != e.high for e in self.effects):
            raise ValueError("Un bonus total de panoplie doit être fixe, pas un intervalle supposé optimal.")
        return self


class SetDefinition(Frozen):
    ref: Text
    name: Text
    tiers: Annotated[tuple[SetTier, ...], Field(max_length=16)]
    source: Text
    # Ne pas déduire l'absence d'un bonus 1 pièce lorsque le seuil n'est pas fourni.
    @model_validator(mode="after")
    def unique_tiers(self):
        if len({t.pieces for t in self.tiers}) != len(self.tiers):
            raise ValueError("Seuil de panoplie dupliqué.")
        return self


class EffectValue(Frozen):
    ref: Annotated[str, Field(min_length=1, max_length=60)]
    value: Int


class Instance(Frozen):
    id: str = Field(default_factory=new_id)
    template_ref: Text
    template_revision: Hash
    mode: Literal["natural_best", "natural_custom", "declared_fm"] = "natural_best"
    final_values: Annotated[tuple[EffectValue, ...], Field(max_length=100)] = ()
    extras: Annotated[tuple[StatValue, ...], Field(max_length=40)] = ()
    _uuid = field_validator("id")(uuid_text)

    @model_validator(mode="after")
    def coherent(self):
        if len({v.ref for v in self.final_values}) != len(self.final_values):
            raise ValueError("Jet en double.")
        as_stats(self.extras)
        if self.mode == "natural_best" and self.final_values:
            raise ValueError("Jets explicites incompatibles avec le mode automatique.")
        if self.extras and self.mode != "declared_fm":
            raise ValueError("Exos uniquement en mode FM déclarée.")
        return self


def equip(template: ItemTemplate) -> Instance:
    return Instance(template_ref=template.ref, template_revision=template.revision)


class SlotItem(Frozen):
    slot: Slot
    item: Instance


class Build(Frozen):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    guild_id: Positive
    owner_id: Positive
    name: Annotated[str, Field(min_length=1, max_length=80)]
    revision: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)] = 0
    catalog_id: Hash
    rules_id: Hash
    profile: Profile
    slots: Annotated[tuple[SlotItem, ...], Field(max_length=16)] = ()
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    _uuid = field_validator("id")(uuid_text)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if any(ord(c) < 32 or c in "\u202e\u202d\u2066\u2067\u2068\u2069" for c in value):
            raise ValueError("Nom contenant des caractères de contrôle.")
        return value

    @model_validator(mode="after")
    def unique_slots(self):
        if len({s.slot for s in self.slots}) != len(self.slots):
            raise ValueError("Emplacement présent deux fois.")
        if len({s.item.id for s in self.slots}) != len(self.slots):
            raise ValueError("Une instance ne peut occuper deux emplacements.")
        for timestamp in (self.created_at, self.updated_at):
            if len(timestamp) > 40 or datetime.fromisoformat(timestamp).tzinfo is None:
                raise ValueError("Date sans fuseau.")
        return self

    def in_slot(self, slot: str) -> Instance | None:
        return next((s.item for s in self.slots if s.slot == slot), None)

    def with_slot(self, slot: str, instance: Instance | None):
        if slot not in SLOTS:
            raise BuildError("Emplacement inconnu.")
        slots = [s for s in self.slots if s.slot != slot]
        if instance is not None:
            slots.append(SlotItem(slot=slot, item=instance))
        return revised(self, slots=[s.model_dump(mode="json") for s in sorted(slots, key=lambda s: SLOTS.index(s.slot))])


class Catalog(Frozen):
    schema_version: Literal[1] = 1
    id: Hash
    generated_at: Annotated[str, Field(max_length=100)] = ""
    source_hash: Hash
    normalizer: str = "evolution-build-1"
    items: Annotated[tuple[ItemTemplate, ...], Field(max_length=20000)]
    sets: Annotated[tuple[SetDefinition, ...], Field(max_length=2000)] = ()
    diagnostics: tuple[Annotated[str, Field(max_length=300)], ...] = ()

    @model_validator(mode="after")
    def identities(self):
        if len({i.ref for i in self.items}) != len(self.items):
            raise ValueError("Catalogue ambigu.")
        if len({s.ref for s in self.sets}) != len(self.sets):
            raise ValueError("Panoplie ambiguë.")
        return self

    @cached_property
    def by_ref(self):
        return MappingProxyType({i.ref: i for i in self.items})

    @cached_property
    def by_set(self):
        return MappingProxyType({s.ref: s for s in self.sets})

    def resolve(self, instance: Instance) -> ItemTemplate:
        item = self.by_ref.get(instance.template_ref)
        if item is None or item.revision != instance.template_revision:
            raise BuildError("Référence ou révision d'objet absente du catalogue figé.")
        return item


class Contribution(Frozen):
    origin: str
    stat: str
    value: StrictInt


class Metric(Frozen):
    stat: str
    value: StrictInt
    status: Literal["known", "partial", "unknown"] = "known"


class Diagnostic(Frozen):
    code: str
    text: str
    origin: str = ""


class Report(Frozen):
    metrics: tuple[Metric, ...]
    contributions: tuple[Contribution, ...]
    errors: tuple[Diagnostic, ...] = ()
    warnings: tuple[Diagnostic, ...] = ()
    equipability: Literal["valid", "invalid", "unknown"]
    completeness: Literal["complete", "partial"]
    nature: Literal["natural", "declared"]
    catalog_id: Hash
    rules_id: Hash
    engine: str = "1.0.0-beta.1"

    @property
    def totals(self):
        return {m.stat: m.value for m in self.metrics}

    def known(self, stat):
        metric = next((m for m in self.metrics if m.stat == stat), None)
        return metric is not None and metric.status == "known"
