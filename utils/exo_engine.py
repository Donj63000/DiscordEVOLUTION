"""Atelier nominal Retro : comptabilite deterministe, pertes explicitement heuristiques."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import math
import random
import re
import unicodedata

from utils.exo_math import integer, probability

D = Decimal
PROFILE = "retro-nominal-v1"
DISCLAIMER = (
    "Modèle pédagogique, pas le moteur Ankama. Les pertes sont heuristiques ; "
    "arrondis et particularités Rétro non reproduits. Puits nominal, non garanti en jeu."
)


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9%]+", " ", value.lower()).strip()


def decimal_value(value: object, maximum: str = "100000") -> Decimal:
    try:
        result = D(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("Poids invalide.") from None
    if not result.is_finite() or not D(0) <= result <= D(maximum):
        raise ValueError(f"Poids attendu entre 0 et {maximum}.")
    if result.as_tuple().exponent < -2:
        raise ValueError("Deux décimales au maximum.")
    return result


@dataclass(frozen=True)
class Stat:
    key: str
    name: str
    weight: Decimal
    rune: str
    gains: tuple[int, ...] = (1,)
    aliases: tuple[str, ...] = ()


STATS = {
    stat.key: stat for stat in (
        Stat("pa", "PA", D(100), "Ga Pa", aliases=("pa", "point d action", "points d action")),
        Stat("pm", "PM", D(90), "Ga Pme", aliases=("pm", "point de mouvement", "points de mouvement")),
        Stat("po", "Portée", D(51), "Po", aliases=("po", "portee", "a la portee")),
        Stat("vi", "Vitalité", D(".25"), "Vi", (3, 10, 30), ("vitalite", "vita", "vitalité")),
        Stat("fo", "Force", D(1), "Fo", (1, 3, 10), ("force",)),
        Stat("ine", "Intelligence", D(1), "Ine", (1, 3, 10), ("intelligence", "intel")),
        Stat("age", "Agilité", D(1), "Age", (1, 3, 10), ("agilite", "agi")),
        Stat("cha", "Chance", D(1), "Cha", (1, 3, 10), ("chance",)),
        Stat("sa", "Sagesse", D(3), "Sa", (1, 3, 10), ("sagesse",)),
        Stat("pp", "Prospection", D(3), "Prospe", (1, 3, 10), ("prospection", "prospe")),
        Stat("ini", "Initiative", D(".1"), "Ini", (10, 30, 100), ("initiative",)),
        Stat("pod", "Pods (bonus)", D(".25"), "Pod", (10, 30, 100), ("pods", "pod")),
        Stat("do", "Dommages", D(20), "Do", aliases=("dommages", "dommage", "de dommages")),
        Stat("so", "Soins", D(20), "So", aliases=("soins", "soin")),
        Stat("cc", "Coups critiques", D(30), "Cri", aliases=(
            "coups critiques", "coup critique", "aux coups critiques", "de coups critiques")),
        Stat("invo", "Invocations", D(30), "Invo", aliases=(
            "invocations", "invocation", "creatures invocables", "creature invocable",
            "creatures invocables supplementaires")),
        Stat("pui", "Dommages %", D(2), "Do Per", (1, 3, 10), (
            "% dommages", "dommages %", "% de dommages", "puissance", "dommages en %")),
        Stat("r_ne", "Rés. neutre", D(2), "Ré Neutre", aliases=(
            "resistance neutre", "resistances neutre", "resistance au neutre")),
        Stat("r_te", "Rés. terre", D(2), "Ré Terre", aliases=("resistance terre", "resistances terre")),
        Stat("r_fe", "Rés. feu", D(2), "Ré Feu", aliases=("resistance feu", "resistances feu")),
        Stat("r_ea", "Rés. eau", D(2), "Ré Eau", aliases=("resistance eau", "resistances eau")),
        Stat("r_ai", "Rés. air", D(2), "Ré Air", aliases=("resistance air", "resistances air")),
        Stat("rp_ne", "Rés. neutre %", D(6), "Ré Per Neutre", aliases=(
            "% resistance neutre", "% de resistance neutre", "resistance neutre %")),
        Stat("rp_te", "Rés. terre %", D(6), "Ré Per Terre", aliases=(
            "% resistance terre", "% de resistance terre", "resistance terre %")),
        Stat("rp_fe", "Rés. feu %", D(6), "Ré Per Feu", aliases=(
            "% resistance feu", "% de resistance feu", "resistance feu %")),
        Stat("rp_ea", "Rés. eau %", D(6), "Ré Per Eau", aliases=(
            "% resistance eau", "% de resistance eau", "resistance eau %")),
        Stat("rp_ai", "Rés. air %", D(6), "Ré Per Air", aliases=(
            "% resistance air", "% de resistance air", "resistance air %")),
    )
}
ALIASES = {
    normalized(alias): stat.key
    for stat in STATS.values()
    for alias in (stat.key, stat.name, *stat.aliases)
}


def stat_key(text: str) -> str:
    key = ALIASES.get(normalized(text))
    if key is None:
        raise ValueError(f"Statistique non prise en charge : {str(text)[:60]}.")
    return key


@dataclass(frozen=True)
class Rune:
    stat: str
    tier: int = 0

    def __post_init__(self):
        if self.stat not in STATS:
            raise ValueError("Rune inconnue.")
        integer(self.tier, 0, len(STATS[self.stat].gains) - 1, "Taille de rune")

    @property
    def gain(self) -> int:
        return STATS[self.stat].gains[self.tier]

    @property
    def weight(self) -> Decimal:
        return STATS[self.stat].weight * self.gain

    @property
    def name(self) -> str:
        return ("", "Pa ", "Ra ")[self.tier] + STATS[self.stat].rune


@dataclass(frozen=True)
class Item:
    name: str
    token: str
    bounds: dict[str, tuple[int, int]]
    source: str
    unsupported: tuple[str, ...] = ()
    immutable: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.name or len(self.name) > 200 or len(self.token) > 160:
            raise ValueError("Identité d'objet invalide.")
        if len(self.bounds) > len(STATS):
            raise ValueError("Trop de statistiques.")
        for key, (low, high) in self.bounds.items():
            if key not in STATS:
                raise ValueError("Caractéristique inconnue.")
            integer(low, -10000, 10000, "Minimum")
            integer(high, low, 10000, "Maximum")

    def maximum(self, key: str) -> int:
        return self.bounds.get(key, (0, 0))[1]

    @property
    def automatic(self) -> bool:
        return bool(self.bounds) and not self.unsupported and all(
            low >= 0 for low, _ in self.bounds.values()
        )


@dataclass
class State:
    jets: dict[str, int]
    sink: Decimal | None = D(0)
    sequence: int = 0
    spent: int = 0
    attempts: int = 0
    successes: int = 0
    journal: list[dict] = field(default_factory=list)

    def validate(self) -> None:
        if not isinstance(self.jets, dict) or len(self.jets) > len(STATS):
            raise ValueError("Jet invalide.")
        for key, value in self.jets.items():
            if key not in STATS:
                raise ValueError("Ligne inconnue.")
            integer(value, -10000, 10000, "Jet")
        if self.sink is not None:
            self.sink = decimal_value(self.sink)
        integer(self.sequence, 0, 10**9, "Séquence")
        integer(self.spent, 0, 10**18, "Dépense")
        integer(self.attempts, 0, 10**9, "Tentatives")
        integer(self.successes, 0, self.attempts, "Réussites")

    @classmethod
    def initial(cls, item: Item, tracking: bool = False) -> State:
        return cls({key: high for key, (_, high) in item.bounds.items()},
                   None if tracking else D(0))


def surplus(item: Item, jets: dict[str, int]) -> Decimal:
    return sum((max(0, value - max(0, item.maximum(key))) * STATS[key].weight
                for key, value in jets.items()), D(0))


def eligibility(item: Item, state: State, rune: Rune) -> tuple[bool, str]:
    current = state.jets.get(rune.stat, 0)
    target = current + rune.gain
    if target > 10000:
        return False, "Jet hors limite technique."
    native_max = max(0, item.maximum(rune.stat))
    if target > native_max and target * STATS[rune.stat].weight > 101:
        return False, "Plafond nominal de ligne : poids total 101 hors maximum naturel."
    after = dict(state.jets)
    after[rune.stat] = target
    if surplus(item, after) > 101:
        return False, "Plafond nominal cumulé over/exo de 101 dépassé."
    if rune.stat not in item.bounds:
        return True, "Exotique : caractéristique absente de la fiche naturelle."
    if target > native_max:
        return True, "Over : au-delà du maximum naturel."
    return True, "Remontage naturel : ce n'est pas un exo."


def fixed_exo(item: Item, state: State, rune: Rune) -> bool:
    return (
        rune.stat in {"pa", "pm", "po"}
        and rune.stat not in item.bounds
        and state.jets.get(rune.stat, 0) == 0
    )


@dataclass(frozen=True)
class Rates:
    sc: float
    sn: float
    source: str = "personnalisé"

    def __post_init__(self):
        probability(self.sc)
        probability(self.sn)
        if self.sc + self.sn > 1:
            raise ValueError("SC + SN ne doit pas dépasser 100 %.")

    @property
    def ec(self) -> float:
        return max(0.0, 1 - self.sc - self.sn)


def rates_for(item: Item, state: State, rune: Rune, custom: Rates | None) -> Rates:
    if fixed_exo(item, state, rune):
        return Rates(.01, 0, "hypothèse communautaire exo PA/PM/PO : 1 % SC")
    if custom is None:
        raise ValueError(
            "Taux serveur non disponibles pour ce remontage/over/exo léger. "
            "Renseignez des taux SC/SN de bac à sable, ou consignez un résultat observé."
        )
    return custom


def _take_loss(jets: dict[str, int], key: str, units: int, losses: dict[str, int]) -> Decimal:
    amount = min(max(0, jets.get(key, 0)), units)
    jets[key] -= amount
    losses[key] = losses.get(key, 0) + amount
    return amount * STATS[key].weight


def allocate_loss(
    item: Item, jets: dict[str, int], sink: Decimal, cost: Decimal,
    target: str, rng: random.Random,
) -> tuple[Decimal, dict[str, int], Decimal]:
    """Heuristique : surplus tiers, puits, puis lignes positives tirees uniformement."""
    losses: dict[str, int] = {}
    debt = cost
    extras = [
        key for key, value in jets.items()
        if key != target and value > max(0, item.maximum(key))
    ]
    rng.shuffle(extras)
    for key in extras:
        if debt <= 0:
            break
        available = jets[key] - max(0, item.maximum(key))
        amount = min(available, math.ceil(debt / STATS[key].weight))
        debt -= _take_loss(jets, key, amount, losses)
    if debt <= 0:
        return sink - debt, losses, D(0)
    consumed = min(sink, debt)
    sink -= consumed
    debt -= consumed
    while debt > 0:
        candidates = [key for key, value in jets.items() if value > 0 and key != target]
        if not candidates:
            break
        key = rng.choice(candidates)
        amount = min(jets[key], math.ceil(debt / STATS[key].weight))
        debt -= _take_loss(jets, key, amount, losses)
    return sink + max(D(0), -debt), losses, max(D(0), debt)


def _record(
    state: State, rune: Rune, outcome: str, before: Decimal | None,
    losses: dict[str, int], price: int, mode: str, deficit: Decimal = D(0),
) -> dict:
    record = {
        "n": state.sequence + 1, "mode": mode, "rune": rune.name,
        "stat": rune.stat, "gain": rune.gain, "weight": str(rune.weight),
        "outcome": outcome, "losses": losses,
        "sink_before": None if before is None else str(before),
        "sink_after": None if state.sink is None else str(state.sink),
        "price": price, "unexplained_weight": str(deficit),
    }
    state.sequence += 1
    state.attempts += 1
    state.successes += int(outcome in {"SC", "SN"})
    state.spent += price
    state.journal.append(record)
    state.journal[:] = state.journal[-100:]
    return record


def attempt(
    item: Item, state: State, rune: Rune, custom: Rates | None,
    seed: int, price: int = 0,
) -> dict:
    state.validate()
    integer(seed, 0, 2**64 - 1, "Graine")
    integer(price, 0, 10**12, "Prix")
    if state.attempts >= 10**9 or state.sequence >= 10**9 or state.spent + price > 10**18:
        raise ValueError("Limite de session atteinte : exportez et ouvrez une nouvelle session.")
    if not item.automatic or any(value < 0 for value in state.jets.values()):
        raise ValueError("Effet non interprété ou malus : atelier automatique bloqué, suivi manuel disponible.")
    if state.sink is None:
        raise ValueError("Déclarez un puits de départ pour cette simulation.")
    allowed, explanation = eligibility(item, state, rune)
    if not allowed:
        raise ValueError(explanation)
    rates = rates_for(item, state, rune, custom)
    rng = random.Random(f"{PROFILE}:{seed}:{state.sequence}")
    draw = rng.random()
    outcome = "SC" if draw < rates.sc else "SN" if draw < rates.sc + rates.sn else "EC"
    before = state.sink
    losses: dict[str, int] = {}
    deficit = D(0)
    if outcome in {"SC", "SN"}:
        state.jets[rune.stat] = state.jets.get(rune.stat, 0) + rune.gain
    if outcome != "SC":
        state.sink, losses, deficit = allocate_loss(
            item, state.jets, state.sink, rune.weight, rune.stat, rng,
        )
    return _record(state, rune, outcome, before, losses, price, "simulation", deficit)


def observe(
    item: Item, state: State, rune: Rune, outcome: str, losses: dict[str, int],
    price: int = 0,
) -> dict:
    """Consigne une observation, sans tirer ni inventer de pertes ou de probabilites."""
    state.validate()
    integer(price, 0, 10**12, "Prix")
    if state.attempts >= 10**9 or state.sequence >= 10**9 or state.spent + price > 10**18:
        raise ValueError("Limite de session atteinte : exportez et ouvrez une nouvelle session.")
    outcome = outcome.upper()
    if outcome not in {"SC", "SN", "EC"}:
        raise ValueError("Résultat attendu : SC, SN ou EC.")
    if not isinstance(losses, dict) or len(losses) > len(STATS):
        raise ValueError("Pertes invalides.")
    if outcome == "SC" and any(losses.values()):
        raise ValueError("Un SC ne comporte pas de pertes ; vérifiez le résultat.")
    jets = dict(state.jets)
    if outcome in {"SC", "SN"}:
        jets[rune.stat] = jets.get(rune.stat, 0) + rune.gain
    lost = D(0)
    for key, amount in losses.items():
        if key not in STATS:
            raise ValueError("Perte sur une caractéristique inconnue.")
        integer(amount, 0, max(0, jets.get(key, 0)), "Perte")
        if amount and state.jets.get(key, 0) < 0:
            raise ValueError("Calcul des malus non couvert par ce profil.")
        jets[key] = jets.get(key, 0) - amount
        lost += amount * STATS[key].weight
    if any(not -10000 <= value <= 10000 for value in jets.values()):
        raise ValueError("Jet résultant hors limite.")
    before = state.sink
    deficit = D(0)
    if not item.automatic or any(value < 0 for value in jets.values()):
        state.sink = None
    if outcome != "SC" and state.sink is not None:
        net = state.sink + lost - rune.weight
        deficit = max(D(0), -net)
        state.sink = max(D(0), net)
    state.jets = jets
    return _record(state, rune, outcome, before, dict(losses), price, "observation", deficit)


def risk(
    item: Item, state: State, rune: Rune, custom: Rates | None,
    seed: int, samples: int = 1500,
) -> dict:
    """Pertes dans le modele depuis un meme jet, jamais une estimation serveur."""
    integer(samples, 1, 5000, "Échantillons")
    counters = {key: 0 for key in state.jets}
    outcomes = {"SC": 0, "SN": 0, "EC": 0}
    loss_weight = D(0)
    deficits = 0
    for index in range(samples):
        trial = State(dict(state.jets), state.sink, index)
        row = attempt(item, trial, rune, custom, seed)
        outcomes[row["outcome"]] += 1
        for key, amount in row["losses"].items():
            counters[key] = counters.get(key, 0) + int(amount > 0)
            loss_weight += amount * STATS[key].weight
        deficits += int(D(row["unexplained_weight"]) > 0)
    return {
        "samples": samples, "outcomes": outcomes,
        "loss_probability": {key: count / samples for key, count in counters.items()},
        "mean_loss_weight": str(loss_weight / samples),
        "unexplained": deficits,
    }


def parse_jets(text: str) -> dict[str, int]:
    if len(text) > 4000:
        raise ValueError("Jet trop long.")
    result: dict[str, int] = {}
    for line in re.split(r"[\n;]+", text):
        if not line.strip():
            continue
        if "=" not in line:
            raise ValueError("Format : fo=50 ; vi=200 ; pa=1.")
        key_text, value_text = line.split("=", 1)
        key = stat_key(key_text.strip())
        if key in result:
            raise ValueError("Caractéristique répétée.")
        if not re.fullmatch(r"-?\d{1,5}", value_text.strip()):
            raise ValueError("Les jets sont des nombres entiers.")
        result[key] = integer(int(value_text), -10000, 10000, "Jet")
    return result
