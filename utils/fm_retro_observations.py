"""Corpus local de transitions Rétro, séparé des scénarios et immuable en service.

Chaque ligne représente UNE pose déclarée en jeu, avec provenance et partition.
Aucun chargement réseau, aucun apprentissage depuis l'IA ou depuis le journal simulé.
Un contexte exact est exigé : on ne généralise pas un Gelano à une amulette THL.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import random
import re
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

from utils.exo_engine import Item, Rune, State, STATS, decimal_value, validate_item_jets
from utils.exo_math import integer
from utils.fm_retro_reference import PROFILE, RETRO
from utils.fm_retro_statistics import wilson

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "fm_retro" / "observations.json"
CORPUS_LIMIT = 32 * 1024 * 1024
MAX_OBSERVATIONS = 100000
# Seuil de service, pas preuve de précision : les IC et effectifs restent affichés.
MIN_CONTEXT_SAMPLES = 100
Context = tuple


def _object(value, allowed: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != allowed:
        raise ValueError("Champs de corpus absents ou inattendus.")
    return value


def _text(value, maximum: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Texte de provenance invalide.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Texte de provenance non exportable en UTF-8.") from None
    return value


def context_key(item: Item, state: State, rune: Rune) -> Context:
    """La normalisation des zéros évite deux contextes pour un même jet."""
    return (
        item.token, tuple(sorted(item.bounds.items())),
        tuple(sorted((key, value) for key, value in state.jets.items() if value)),
        state.sink, rune.stat, rune.tier,
    )


@dataclass(frozen=True)
class Observation:
    id: str
    session: str
    split: str
    context: Context
    outcome: str
    after: tuple[tuple[str, int], ...]
    sink_after: Decimal | None
    losses: tuple[tuple[str, int], ...]
    source: Mapping
    before: tuple[tuple[str, int], ...]
    item_name: str

    def state_before(self) -> State:
        return State(dict(self.before), self.context[3])

    def item(self) -> Item:
        return Item(self.item_name, self.context[0], dict(self.context[1]), "corpus déclaré")

    def rune(self) -> Rune:
        return Rune(self.context[4], self.context[5])


@dataclass(frozen=True)
class Sample:
    observations: tuple[Observation, ...]
    source: str
    counts: tuple[int, int, int] = field(init=False)

    def __post_init__(self) -> None:
        if not self.observations:
            raise ValueError("Échantillon vide.")
        counts = Counter(row.outcome for row in self.observations)
        object.__setattr__(self, "counts", tuple(counts[key] for key in ("SC", "SN", "EC")))

    @property
    def count(self) -> int:
        return len(self.observations)

    def rates(self) -> tuple[float, float, str]:
        return self.counts[0] / self.count, self.counts[1] / self.count, self.source

    def summary(self) -> dict:
        counts = dict(zip(("SC", "SN", "EC"), self.counts))
        return {
            "effectif": self.count, "origine": "observations déclarées, contexte exact",
            "intervalles_wilson_95": {outcome: list(wilson(counts[outcome], self.count))
                                     for outcome in ("SC", "SN", "EC")},
            "certifie_ankama": False,
        }

    def draw(self, rng: random.Random) -> Observation:
        # Un seul tirage JOINT préserve la corrélation résultat/pertes/reliquat.
        return self.observations[rng.randrange(self.count)]


@dataclass(frozen=True)
class Corpus:
    id: str
    digest: str
    observations: tuple[Observation, ...]
    groups: Mapping[Context, Sample]
    scope: tuple[str, str] | None = None

    def match(self, item: Item, state: State, rune: Rune) -> Sample | None:
        if state.sink is None or item.unsupported:
            return None
        return self.groups.get(context_key(item, state, rune))

    def summary(self) -> dict:
        return {
            "id": self.id, "empreinte": self.digest, "serveur_version": self.scope,
            "poses_apprentissage": sum(row.split == "train" for row in self.observations),
            "poses_validation": sum(row.split == "validation" for row in self.observations),
            "contextes_couverts": len(self.groups),
            "minimum_par_contexte": MIN_CONTEXT_SAMPLES,
            "fidelite_serveur_demontree": False,
        }


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Clé JSON dupliquée dans le corpus.")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"Constante JSON interdite : {value}.")


def load_corpus(path: Path) -> Corpus:
    try:
        # Lecture bornée même si le fichier grandit entre stat() et read().
        with Path(path).open("rb") as handle:
            raw = handle.read(CORPUS_LIMIT + 1)
        if len(raw) > CORPUS_LIMIT:
            raise ValueError("Corpus limité à 32 Mio.")
        data = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        _object(data, {"schema", "reference", "id", "description", "observations"})
        if type(data["schema"]) is not int or data["schema"] != 1 or data["reference"] != PROFILE:
            raise ValueError("Version de corpus incompatible.")
        ident = _text(data["id"], 64)
        if re.fullmatch(r"[a-zA-Z0-9_-]+", ident) is None:
            raise ValueError("Identifiant de corpus invalide.")
        _text(data["description"], 2000)
        rows = data["observations"]
        if not isinstance(rows, list) or len(rows) > MAX_OBSERVATIONS:
            raise ValueError("Nombre d'observations invalide.")
        observations, ids, partitions, grouped = [], set(), {}, defaultdict(list)
        scope = None
        for index, raw_row in enumerate(rows):
            try:
                row = _object(raw_row, {
                    "id", "session", "split", "origin", "source", "item",
                    "before", "rune", "outcome", "after",
                })
                rid, session = _text(row["id"]), _text(row["session"])
                if rid in ids:
                    raise ValueError("Identifiant d'observation dupliqué.")
                ids.add(rid)
                if row["origin"] != "game_observation":
                    raise ValueError("Une simulation ou un cas synthétique n'est pas une observation.")
                split = row["split"]
                if split not in {"train", "validation"}:
                    raise ValueError("Partition attendue : train ou validation.")
                if session in partitions and partitions[session] != split:
                    raise ValueError("Fuite apprentissage/validation : séance présente dans les deux partitions.")
                partitions[session] = split
                source = _object(row["source"], {"url", "server", "game_version", "date", "profession_level"})
                url = urlsplit(_text(source["url"], 1000))
                if url.scheme != "https" or not url.hostname or url.username or url.password:
                    raise ValueError("La preuve doit avoir une URL HTTPS sans identifiants.")
                _text(source["server"], 80)
                integer(source["profession_level"], 100, 100, "Niveau du forgemage")
                version = _text(source["game_version"], 80)
                if re.fullmatch(r"(?:retro-)?1\.\d+(?:\.\d+)*", version, re.IGNORECASE) is None:
                    raise ValueError("Le corpus doit identifier une version Rétro 1.x précise.")
                date.fromisoformat(_text(source["date"], 10))
                row_scope = (source["server"], version)
                if scope is not None and row_scope != scope:
                    raise ValueError("Ne mélangez pas serveurs ou versions dans un même corpus.")
                scope = row_scope
                item_data = _object(row["item"], {"name", "token", "bounds"})
                bounds = {}
                if not isinstance(item_data["bounds"], dict):
                    raise ValueError("Bornes de corpus invalides.")
                for key, pair in item_data["bounds"].items():
                    if not isinstance(pair, list) or len(pair) != 2:
                        raise ValueError("Intervalle naturel invalide.")
                    bounds[key] = tuple(pair)
                item = Item(_text(item_data["name"], 200), _text(item_data["token"]), bounds, "corpus")
                states = []
                for field in ("before", "after"):
                    payload = _object(row[field], {"jets", "sink"})
                    jets = payload["jets"]
                    if not isinstance(jets, dict):
                        raise ValueError("Jet observé invalide.")
                    sink = None if payload["sink"] is None else decimal_value(payload["sink"])
                    state = State(dict(jets), sink)
                    state.validate()
                    validate_item_jets(item, state.jets)
                    for key, value in state.jets.items():
                        if value < min(0, item.bounds.get(key, (0, 0))[0]):
                            raise ValueError("Jet observé sous le plancher naturel.")
                    states.append(state)
                before, after = states
                rune_data = _object(row["rune"], {"stat", "tier"})
                rune = Rune(**rune_data)
                outcome = row["outcome"]
                if outcome not in {"SC", "SN", "EC"}:
                    raise ValueError("Résultat de pose invalide.")
                losses = {}
                for key in set(before.jets) | set(after.jets) | {rune.stat}:
                    gain = rune.gain if key == rune.stat and outcome != "EC" else 0
                    lost = before.jets.get(key, 0) + gain - after.jets.get(key, 0)
                    integer(lost, 0, 10000, "Perte observée")
                    if lost:
                        losses[key] = lost
                if outcome == "SC" and (losses or before.sink != after.sink):
                    raise ValueError("SC incompatible avec pertes ou variation du puits.")
                observation = Observation(
                    rid, session, split, context_key(item, before, rune), outcome,
                    tuple(sorted(after.jets.items())), after.sink, tuple(sorted(losses.items())),
                    MappingProxyType(dict(source)), tuple(sorted(before.jets.items())), item.name,
                )
                observations.append(observation)
                # Un puits inconnu reste utile pour l'audit, jamais pour un replay exact.
                if split == "train" and before.sink is not None and after.sink is not None:
                    grouped[observation.context].append(observation)
            except (TypeError, KeyError, ValueError, OverflowError) as exc:
                raise ValueError(f"Observation {index + 1} : {exc}") from exc
        groups = {
            key: Sample(tuple(rows), f"corpus {ident} · n={len(rows)} · contexte exact, non certifié")
            for key, rows in grouped.items() if len(rows) >= MIN_CONTEXT_SAMPLES
        }
        return Corpus(ident, hashlib.sha256(raw).hexdigest(), tuple(observations), MappingProxyType(groups), scope)
    except (OSError, UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError(f"Corpus FM illisible : {exc}") from exc


@lru_cache(maxsize=1)
def default_corpus() -> Corpus:
    # Chemin configuré par l'exploitant uniquement ; jamais fourni par un message Discord.
    return load_corpus(Path(os.environ.get("EXO_FM_CORPUS") or DEFAULT_PATH))


def active_model_signature() -> str:
    return f"{PROFILE}:{RETRO.digest}:{default_corpus().digest}"
