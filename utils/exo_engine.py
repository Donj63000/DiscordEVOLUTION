"""Moteur FM Rétro : transitions atomiques, référentiel sourcé et aléatoire traçable."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import math
import json
import random
import re
import unicodedata
from typing import TYPE_CHECKING

from utils.exo_math import integer, probability

D = Decimal
from utils.fm_retro_reference import PROFILE, RETRO, MAX_HISTORY, MAX_HISTORY_BYTES, OLD_PROFILES
if TYPE_CHECKING:
    from utils.fm_retro_observations import Corpus, Sample

LEGACY_PROFILE = "retro-nominal-v1"
DISCLAIMER = (
    "FM Rétro • règles sourcées, distributions non certifiées • aucun objet réel modifié."
)


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9%]+", " ", value.lower()).strip()


def decimal_value(value: object, maximum: str = "100000") -> Decimal:
    if isinstance(value, bool) or len(str(value)) > 40:
        raise ValueError("Poids invalide.")
    try:
        result = D(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("Poids invalide.") from None
    if not result.is_finite() or not D(0) <= result <= D(maximum):
        raise ValueError(f"Poids attendu entre 0 et {maximum}.")
    if result.as_tuple().exponent < -2:
        raise ValueError("Deux décimales au maximum.")
    return result.quantize(D(".01"))


@dataclass(frozen=True)
class Stat:
    key: str
    name: str
    rune: str
    aliases: tuple[str, ...] = ()

    @property
    def weight(self) -> Decimal:
        return RETRO.stats[self.key].weight

    @property
    def gains(self) -> tuple[int, ...]:
        return RETRO.stats[self.key].gains


STATS = {
    stat.key: stat for stat in (
        Stat('pa', 'PA', 'Ga Pa', ('pa', 'point d action', 'points d action')),
        Stat('pm', 'PM', 'Ga Pme', ('pm', 'point de mouvement', 'points de mouvement')),
        Stat('po', 'Portée', 'Po', ('po', 'portee', 'a la portee')),
        Stat('vi', 'Vitalité', 'Vi', ('vitalite', 'vita', 'vitalité')),
        Stat('fo', 'Force', 'Fo', ('force',)),
        Stat('ine', 'Intelligence', 'Ine', ('intelligence', 'intel')),
        Stat('age', 'Agilité', 'Age', ('agilite', 'agi')),
        Stat('cha', 'Chance', 'Cha', ('chance',)),
        Stat('sa', 'Sagesse', 'Sa', ('sagesse',)),
        Stat('pp', 'Prospection', 'Prospe', ('prospection', 'prospe')),
        Stat('ini', 'Initiative', 'Ini', ('initiative',)),
        Stat('pod', 'Pods (bonus)', 'Pod', ('pods', 'pod')),
        Stat('do', 'Dommages', 'Do', ('dommages', 'dommage', 'de dommages')),
        Stat('so', 'Soins', 'So', ('soins', 'soin')),
        Stat('cc', 'Coups critiques', 'Cri', ('coups critiques', 'coup critique', 'aux coups critiques', 'de coups critiques')),
        Stat('invo', 'Invocations', 'Invo', ('invocations', 'invocation', 'creatures invocables', 'creature invocable', 'creatures invocables supplementaires')),
        Stat('pui', 'Dommages %', 'Do Per', ('% dommages', 'dommages %', '% de dommages', 'puissance', 'dommages en %')),
        Stat('r_ne', 'Rés. neutre', 'Ré Neutre', ('resistance neutre', 'resistances neutre', 'resistance au neutre')),
        Stat('r_te', 'Rés. terre', 'Ré Terre', ('resistance terre', 'resistances terre')),
        Stat('r_fe', 'Rés. feu', 'Ré Feu', ('resistance feu', 'resistances feu')),
        Stat('r_ea', 'Rés. eau', 'Ré Eau', ('resistance eau', 'resistances eau')),
        Stat('r_ai', 'Rés. air', 'Ré Air', ('resistance air', 'resistances air')),
        Stat('rp_ne', 'Rés. neutre %', 'Ré Per Neutre', ('% resistance neutre', '% de resistance neutre', 'resistance neutre %')),
        Stat('rp_te', 'Rés. terre %', 'Ré Per Terre', ('% resistance terre', '% de resistance terre', 'resistance terre %')),
        Stat('rp_fe', 'Rés. feu %', 'Ré Per Feu', ('% resistance feu', '% de resistance feu', 'resistance feu %')),
        Stat('rp_ea', 'Rés. eau %', 'Ré Per Eau', ('% resistance eau', '% de resistance eau', 'resistance eau %')),
        Stat('rp_ai', 'Rés. air %', 'Ré Per Air', ('% resistance air', '% de resistance air', 'resistance air %')),
        Stat('pi', 'Dommages aux pièges', 'Pi', ('dommages aux pieges', 'dommages pieges', 'dommage aux pieges')),
        Stat('pi_per', 'Dommages aux pièges %', 'Pi Per', ('% dommages aux pieges', '% de dommages aux pieges', 'dommages aux pieges %')),
        Stat('ren', 'Renvoi de dommages', 'Do Ren', ('renvoi de dommages', 'renvois de dommages', 'renvoie des dommages')),
    )
}
if set(STATS) != set(RETRO.stats):
    raise RuntimeError("Le catalogue de présentation et le référentiel FM divergent.")

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
        """Coût débité en SN/EC ; ne pas confondre avec le poids nominal ajouté."""
        return RETRO.stats[self.stat].costs[self.tier]

    @property
    def nominal_weight(self) -> Decimal:
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
    # Cache privé, reconstruit à l'import ; le journal n'est jamais tronqué.
    journal_bytes: int = field(default=0, repr=False, compare=False)
    profile: str = PROFILE

    def validate(self) -> None:
        if not isinstance(self.profile, str) or self.profile not in {PROFILE, *OLD_PROFILES}:
            raise ValueError("Profil d’état inconnu.")
        if not isinstance(self.jets, dict) or len(self.jets) > len(STATS):
            raise ValueError("Jet invalide.")
        for key, value in self.jets.items():
            if key not in STATS:
                raise ValueError("Ligne inconnue.")
            integer(value, -10000, 10000, "Jet")
        if self.sink is not None:
            if not isinstance(self.sink, D):
                raise ValueError("Le puits doit être un poids Decimal ou inconnu.")
            decimal_value(self.sink)
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


def validate_item_jets(item: Item, jets: dict[str, int], *, label: str = "Jet") -> None:
    """Controle toutes les lignes selon le profil local, pas seulement la rune cible.

    Les maxima naturels restent autorises, meme au-dessus de 101 de poids.
    Les malus sont valides techniquement ; leur simulation exige un contexte observé exact.
    Ce controle n'est pas une certification d'admissibilite sur le serveur.
    """
    State(jets).validate()
    for key, value in jets.items():
        if value > max(0, item.maximum(key)) and value * STATS[key].weight > D(RETRO.metadata["rules"]["line_cap"]["value"]):
            raise ValueError(f"{label} {STATS[key].name}={value} : plafond de ligne 101 dépassé.")
    if surplus(item, jets) > D(RETRO.metadata["rules"]["extra_cap"]["value"]):
        raise ValueError(f"{label} : plafond nominal cumulé over/exo de 101 dépassé.")


def validate_goals(item: Item, goals: dict[str, int]) -> None:
    """Validation commune des objectifs UI, importes et utilises par les lots."""
    if not isinstance(goals, dict) or not goals:
        raise ValueError("Indiquez au moins un objectif : pm=1 ; pa=1.")
    for value in goals.values():
        integer(value, 0, 10000, "Objectif")
    if not any(goals.values()):
        raise ValueError("Indiquez au moins un objectif positif.")
    validate_item_jets(item, goals, label="Objectif")


def eligibility(item: Item, state: State, rune: Rune) -> tuple[bool, str]:
    try:
        validate_item_jets(item, state.jets)
    except ValueError as exc:
        return False, str(exc)
    current = state.jets.get(rune.stat, 0)
    target = current + rune.gain
    if target > 10000:
        return False, "Jet hors limite technique."
    native_max = max(0, item.maximum(rune.stat))
    if target > native_max and target * STATS[rune.stat].weight > D(RETRO.metadata["rules"]["line_cap"]["value"]):
        return False, "Plafond nominal de ligne : poids total 101 hors maximum naturel."
    after = dict(state.jets)
    after[rune.stat] = target
    if surplus(item, after) > D(RETRO.metadata["rules"]["extra_cap"]["value"]):
        return False, "Plafond nominal cumulé over/exo de 101 dépassé."
    if rune.stat not in item.bounds:
        return True, "Exotique : caractéristique absente de la fiche naturelle."
    if target > native_max:
        return True, "Over : au-delà du maximum naturel."
    return True, "Remontage naturel : ce n'est pas un exo."


def fixed_exo(item: Item, state: State, rune: Rune) -> bool:
    return (
        rune.stat in RETRO.metadata["rules"]["heavy_exo"]["value"]
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


def recommended_rune(item: Item, state: State, key: str, target: int | None = None) -> Rune:
    """Choisit une taille de confort, sans garantir le meilleur rendement en jeu."""
    current = max(0, state.jets.get(key, 0))
    limit = item.maximum(key) if target is None else target
    missing = max(1, limit - current)
    gains = STATS[key].gains
    tier = next((index for index, gain in enumerate(gains) if current < 20 * gain),
                len(gains) - 1)
    while tier and gains[tier] > missing:
        tier -= 1
    return Rune(key, tier)


def estimated_rates(item: Item, state: State, rune: Rune) -> Rates:
    from utils.fm_retro_model import forecast
    return Rates(*forecast(item, state, rune))


def empirical_sample(
    item: Item, state: State, rune: Rune, custom: Rates | None = None, *, corpus: Corpus | None = None,
) -> Sample | None:
    """Une configuration de laboratoire ne devient jamais une mesure serveur."""
    if custom is not None:
        return None
    from utils.fm_retro_observations import default_corpus
    return (default_corpus() if corpus is None else corpus).match(item, state, rune)


def rates_for(
    item: Item, state: State, rune: Rune, custom: Rates | None, *, corpus: Corpus | None = None,
) -> Rates:
    sample = empirical_sample(item, state, rune, custom, corpus=corpus)
    if sample is not None:
        return Rates(*sample.rates())
    if fixed_exo(item, state, rune):
        rule = RETRO.metadata["rules"]["heavy_exo"]
        return Rates(rule["sc"], rule["sn"], "convention communautaire exo PA/PM/PO : 1 % SC, non calibrée")
    return custom if custom is not None else estimated_rates(item, state, rune)


def simulation_blocker(
    item: Item, state: State, rune: Rune, custom: Rates | None = None, *, corpus: Corpus | None = None,
) -> str:
    if state.profile != PROFILE:
        return "Ancien modèle : exportez puis redéclarez le jet et le puits, ou choisissez un nouveau jet de départ."
    try:
        state.validate()
        validate_item_jets(item, state.jets)
    except ValueError as exc:
        return str(exc)
    if item.unsupported or not item.bounds:
        return "Effet non interprété : simulation bloquée, suivi manuel disponible."
    if state.sink is None:
        return "Déclarez un puits de départ dans « Modifier le jet » (0 pour un scénario neuf)."
    if (not item.automatic or any(value < 0 for value in state.jets.values())) and (
        empirical_sample(item, state, rune, custom, corpus=corpus) is None
    ):
        return "Malus sans observations de contexte exact : simulation bloquée, suivi manuel disponible."
    allowed, explanation = eligibility(item, state, rune)
    return "" if allowed else explanation


def _take_loss(jets: dict[str, int], key: str, units: int, losses: dict[str, int]) -> Decimal:
    amount = min(max(0, jets.get(key, 0)), units)
    jets[key] -= amount
    losses[key] = losses.get(key, 0) + amount
    return amount * STATS[key].weight


def allocate_loss(
    item: Item, jets: dict[str, int], sink: Decimal, cost: Decimal,
    target: str, rng: random.Random,
) -> tuple[Decimal, dict[str, int], Decimal]:
    """Comptabilité exacte du modèle ; sélection des pertes NON calibrée.

    Le secours utilise la puissance disponible, plutôt qu'une équiprobabilité
    des lignes. Cette pondération demeure une hypothèse, pas le code serveur.
    Les observations de contexte exact remplacent toute cette allocation.
    """
    losses: dict[str, int] = {}
    debt = cost
    extras = [
        key for key, value in sorted(jets.items())
        if key != target and value > max(0, item.maximum(key))
    ]
    rng.shuffle(extras)
    for key in extras:
        if debt <= 0:
            break
        # Une ligne choisie pour son surplus n'est pas artificiellement protégée
        # à sa borne naturelle : elle peut aussi céder des points naturels.
        amount = min(jets[key], math.ceil(debt / STATS[key].weight))
        debt -= _take_loss(jets, key, amount, losses)
    if debt <= 0:
        return sink - debt, losses, D(0)
    consumed = min(sink, debt)
    sink -= consumed
    debt -= consumed
    while debt > 0:
        candidates = [key for key, value in sorted(jets.items()) if value > 0]
        if not candidates:
            break
        # Poids entiers au centième : tirage sans erreur d'arrondi flottante.
        powers = [int(STATS[key].weight * jets[key] * 100) for key in candidates]
        ticket = rng.randrange(sum(powers))
        key = candidates[-1]
        for candidate, power in zip(candidates, powers):
            if ticket < power:
                key = candidate
                break
            ticket -= power
        amount = min(jets[key], math.ceil(debt / STATS[key].weight))
        debt -= _take_loss(jets, key, amount, losses)
    return sink + max(D(0), -debt), losses, max(D(0), debt)


def weight_text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def _record(
    state: State, rune: Rune, outcome: str, after: State,
    losses: dict[str, int], price: int, mode: str, deficit: Decimal = D(0),
    rates: Rates | None = None, evidence: dict | None = None,
) -> dict:
    """Prépare le bilan complet, vérifie la capacité, puis commet atomiquement."""
    touched = sorted({rune.stat, *losses})
    lost = sum((STATS[key].weight * amount for key, amount in losses.items()), D(0))
    charged = D(0) if outcome == "SC" else rune.weight
    residual = None
    if state.sink is not None and after.sink is not None:
        residual = after.sink - state.sink - lost + charged - deficit
    record = {
        "n": state.sequence + 1, "mode": mode, "rune": rune.name,
        "stat": rune.stat, "tier": rune.tier, "gain": rune.gain,
        "weight": weight_text(rune.weight), "nominal_weight": weight_text(rune.nominal_weight),
        "outcome": outcome, "losses": dict(losses),
        "sink_before": None if state.sink is None else weight_text(state.sink),
        "sink_after": None if after.sink is None else weight_text(after.sink),
        "price": price, "unexplained_weight": weight_text(deficit),
        "applied_gain": rune.gain if outcome in {"SC", "SN"} else 0,
        "changes": {key: [state.jets.get(key, 0), after.jets.get(key, 0)] for key in touched},
        "before": dict(state.jets), "after": dict(after.jets),
        "rates": None if rates is None else {"sc": rates.sc, "sn": rates.sn, "source": rates.source},
        "profile": PROFILE, "reference": RETRO.digest,
        "evidence": evidence or {"kind": "declaration_non_verifiee"},
        "ledger": {
            "charged": weight_text(charged), "lost": weight_text(lost),
            # Un relevé peut contredire notre comptabilité : conserver cet écart
            # est préférable à réécrire discrètement l'observation.
            "residual": None if residual is None else weight_text(residual),
        },
    }
    size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    used = state.journal_bytes
    if state.journal and not used:
        used = sum(len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                   for row in state.journal)
    if len(state.journal) >= MAX_HISTORY or used + size > MAX_HISTORY_BYTES:
        raise ValueError("Historique plein : exportez la séance puis ouvrez-en une nouvelle. Aucune pose effectuée.")
    # Toute opération susceptible d'échouer précède le changement d'état.
    state.jets, state.sink = after.jets, after.sink
    state.sequence += 1
    state.attempts += 1
    state.successes += int(outcome in {"SC", "SN"})
    state.spent += price
    state.journal.append(record)
    state.journal_bytes = used + size
    return record


def attempt(
    item: Item, state: State, rune: Rune, custom: Rates | None,
    seed: int, price: int = 0, *, corpus: Corpus | None = None,
) -> dict:
    """Le moteur est l'unique producteur de résultats, y compris pour Evo."""
    state.validate()
    integer(seed, 0, 2**64 - 1, "Graine")
    integer(price, 0, 10**12, "Prix")
    if state.attempts >= 10**9 or state.sequence >= 10**9 or state.spent + price > 10**18:
        raise ValueError("Limite de session atteinte : exportez et ouvrez une nouvelle session.")
    reason = simulation_blocker(item, state, rune, custom, corpus=corpus)
    if reason:
        raise ValueError(reason)
    from utils.fm_retro_observations import default_corpus
    corpus = default_corpus() if corpus is None else corpus
    sample = empirical_sample(item, state, rune, custom, corpus=corpus)
    rates = rates_for(item, state, rune, custom, corpus=corpus)
    # La graine inclut les versions : aucune promesse de replay entre deux modèles.
    rng = random.Random(f"{PROFILE}:{RETRO.digest}:{corpus.digest}:{seed}:{state.sequence}")
    deficit = D(0)
    if sample is not None:
        observation = sample.draw(rng)
        outcome = observation.outcome
        jets, sink, losses = dict(observation.after), observation.sink_after, dict(observation.losses)
        evidence = {"kind": "empirical_joint", "corpus": corpus.digest,
                    "observation": observation.id, "count": sample.count}
    else:
        draw = rng.random()
        outcome = "SC" if draw < rates.sc else "SN" if draw < rates.sc + rates.sn else "EC"
        jets, sink, losses = dict(state.jets), state.sink, {}
        if outcome != "SC":
            sink, losses, deficit = allocate_loss(item, jets, sink, rune.weight, rune.stat, rng)
        if outcome in {"SC", "SN"}:
            jets[rune.stat] = jets.get(rune.stat, 0) + rune.gain
        evidence = {
            "kind": "custom_scenario" if custom is not None and not fixed_exo(item, state, rune)
                    else "community_prior" if fixed_exo(item, state, rune) else "historical_envelope",
            "loss_model": "surplus_sink_power_weighted_hypothesis",
            "corpus": corpus.digest,
        }
    candidate = State(jets, sink)
    candidate.validate()
    validate_item_jets(item, candidate.jets)
    return _record(state, rune, outcome, candidate, losses, price, "simulation", deficit, rates, evidence)


def observe(
    item: Item, state: State, rune: Rune, outcome: str, losses: dict[str, int],
    price: int = 0,
) -> dict:
    """Consigne les changements declares, sans inventer un puits manquant."""
    if state.profile != PROFILE:
        raise ValueError("Ancien suivi : redéclarez explicitement le jet et le puits avant de poursuivre.")
    state.validate()
    integer(price, 0, 10**12, "Prix")
    if state.attempts >= 10**9 or state.sequence >= 10**9 or state.spent + price > 10**18:
        raise ValueError("Limite de session atteinte : exportez et ouvrez une nouvelle session.")
    if not isinstance(outcome, str) or outcome.upper() not in {"SC", "SN", "EC"}:
        raise ValueError("Résultat attendu : SC, SN ou EC.")
    outcome = outcome.upper()
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
        malus = item.bounds.get(key, (0, 0))[0] < 0 or jets.get(key, 0) < 0
        integer(amount, 0, 10000 if malus else max(0, jets.get(key, 0)), "Perte")
        jets[key] = jets.get(key, 0) - amount
        lost += amount * STATS[key].weight
    before, sink = state.sink, state.sink
    deficit = D(0)
    if outcome != "SC" and (not item.automatic or any(value < 0 for value in jets.values())):
        sink = None
    if outcome != "SC" and sink is not None:
        net = sink + lost - rune.weight
        deficit = max(D(0), -net)
        sink = None if deficit else net
    candidate = State(jets, sink)
    candidate.validate()
    return _record(state, rune, outcome, candidate, losses, price, "observation", deficit)


def risk(
    item: Item, state: State, rune: Rune, custom: Rates | None,
    seed: int, samples: int = 1500,
) -> dict:
    """Pertes dans le modele depuis un meme jet, jamais une estimation serveur."""
    integer(samples, 1, 5000, "Échantillons")
    reason = simulation_blocker(item, state, rune, custom)
    if reason:
        raise ValueError(reason)
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
