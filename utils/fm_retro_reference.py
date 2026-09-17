"""Référentiel Rétro immuable ; règles et niveaux de preuve sont versionnés ensemble."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data" / "fm_retro" / "reference-v3.json"
REFERENCE_SHA256 = "90064ed388246df0d57a7603f132ef93fb5936dae124b98880714c6f5c1c0671"
PROFILE = "retro-documented-v3"
MODEL = "historical-envelope-v1"
OLD_PROFILES = frozenset({"retro-nominal-v1", "retro-workshop-v2"})
# Bornes de service, pas limites du jeu. On refuse la prochaine action, sans tronquer.
MAX_HISTORY = 8192
MAX_HISTORY_BYTES = 3 * 1024 * 1024


@dataclass(frozen=True)
class RuneSpec:
    weight: Decimal
    gains: tuple[int, ...]
    costs: tuple[Decimal, ...]
    sources: tuple[str, ...]


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


@dataclass(frozen=True)
class Reference:
    id: str
    version: str
    digest: str
    stats: Mapping[str, RuneSpec]
    anchors: Mapping[str, tuple[float, float]]
    metadata: Mapping

    def summary(self) -> dict:
        # Contrat public minimal ; aucune donnée de session n'est incluse.
        return {
            "version": self.version, "profil_tirage": self.id, "empreinte": self.digest,
            "statut": "reference_documentee_modele_non_calibre", "certifie_ankama": False,
            "taux_ordinaires": "interpolation des bornes historiques 1.27 ; hypothèse non calibrée",
            "exo_pa_pm_po": "convention communautaire : 1 % SC, 0 % SN si bonus non naturel",
            "poids": "référentiel Rétro ; puissance nominale et coût arrondi distingués",
            "pertes": "transitions observées si couvertes ; sinon repli pondéré non calibré",
            "perimetre": self.metadata["scope"],
        }


def _load() -> Reference:
    raw = REFERENCE_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    # Une modification de règles ne doit jamais altérer silencieusement les replays.
    if digest != REFERENCE_SHA256:
        raise RuntimeError("Référentiel FM modifié sans versionnement de son empreinte.")
    data = json.loads(raw)
    if data["schema"] != 1 or data["id"] != PROFILE or data["model"]["id"] != MODEL:
        raise RuntimeError("Référentiel FM incompatible.")
    specs = {}
    for key, row in data["stats"].items():
        spec = RuneSpec(Decimal(row["weight_per_point"]), tuple(row["gains"]),
                        tuple(Decimal(value) for value in row["rune_costs"]),
                        tuple(row["sources"]))
        if (not spec.weight.is_finite() or spec.weight <= 0
                or not 1 <= len(spec.gains) <= 3 or len(spec.costs) != len(spec.gains)
                or any(type(gain) is not int or not 1 <= gain <= 100 for gain in spec.gains)
                or any(not cost.is_finite() or cost <= 0 for cost in spec.costs)
                or any(cost != (spec.weight * gain).to_integral_value(rounding=ROUND_CEILING)
                       for gain, cost in zip(spec.gains, spec.costs))):
            raise RuntimeError("Rune invalide dans le référentiel FM.")
        specs[key] = spec
    anchors = {}
    for key, row in data["anchors"].items():
        sc, sn = row["sc"], row["sn"]
        if not (0 <= sc <= 1 and 0 <= sn <= 1 and sc + sn <= 1):
            raise RuntimeError("Ancrage probabiliste FM invalide.")
        anchors[key] = (sc, sn)
    return Reference(data["id"], data["version"], digest, MappingProxyType(specs),
                     MappingProxyType(anchors), _freeze(data))


RETRO = _load()
