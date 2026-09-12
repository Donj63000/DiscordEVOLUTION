"""Probabilites exactes conditionnelles a un taux fixe, sans formule serveur inventee."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, localcontext
import math
import random

MAX_ATTEMPTS = 1_000_000
MAX_EXPERIMENTS = 50_000
MAX_KAMAS = 10**12


def integer(value: object, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} : entier entre {low:,} et {high:,}.")
    return value


def probability(p: float) -> float:
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        raise ValueError("Le taux doit être un nombre.")
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("Le taux doit être compris entre 0 et 100 %.")
    return float(p)


def success_within(p: float, n: int) -> float:
    p = probability(p)
    integer(n, 0, MAX_ATTEMPTS, "Tentatives")
    if n == 0 or p == 0:
        return 0.0
    if p == 1:
        return 1.0
    return -math.expm1(n * math.log1p(-p))


def no_success(p: float, n: int) -> float:
    p = probability(p)
    integer(n, 0, MAX_ATTEMPTS, "Tentatives")
    if n == 0 or p == 0:
        return 1.0
    if p == 1:
        return 0.0
    return math.exp(n * math.log1p(-p))


def geometric_quantile(p: float, confidence: float) -> int | None:
    p, confidence = probability(p), probability(confidence)
    if confidence == 0:
        return 0
    if p == 0:
        return None
    if p == 1:
        return 1
    if confidence == 1:
        return None
    ratio = math.log1p(-confidence) / math.log1p(-p)
    if not math.isfinite(ratio):
        with localcontext() as context:
            context.prec = 400
            quotient = Decimal(math.log1p(-confidence)) / Decimal(math.log1p(-p))
            return int(quotient.to_integral_value(rounding=ROUND_CEILING))
    n = max(1, math.ceil(ratio))
    target = math.log1p(-confidence)
    step = math.log1p(-p)
    while n > 1 and (n - 1) * step <= target:
        n -= 1
    while n * step > target:
        n += 1
    return n


def expected_capped_attempts(p: float, n: int) -> float:
    p = probability(p)
    integer(n, 0, MAX_ATTEMPTS, "Tentatives")
    return float(n) if p == 0 else success_within(p, n) / p


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    integer(trials, 0, MAX_ATTEMPTS, "Observations")
    integer(successes, 0, trials, "Réussites")
    if trials == 0:
        return 0.0, 1.0
    z = 1.959963984540054
    rate = successes / trials
    denominator = 1 + z * z / trials
    centre = (rate + z * z / (2 * trials)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / trials + z * z / (4 * trials**2))
    radius /= denominator
    return max(0.0, centre - radius), min(1.0, centre + radius)


@dataclass(frozen=True)
class Budget:
    """Le remontage est facture ENTRE tentatives, jamais apres la derniere."""

    rune: int = 0
    rebuild: int = 0
    item: int = 0
    preparation: int = 0
    limit: int = 0

    def __post_init__(self):
        for key, value in vars(self).items():
            integer(value, 0, MAX_KAMAS, key)

    def cost(self, attempts: int) -> int:
        integer(attempts, 0, MAX_ATTEMPTS, "Tentatives")
        if attempts == 0:
            return 0
        return self.item + self.preparation + attempts * self.rune + (attempts - 1) * self.rebuild

    def affordable(self) -> int:
        if self.limit < self.cost(1):
            return 0
        cycle = self.rune + self.rebuild
        if cycle == 0:
            return MAX_ATTEMPTS
        return min(MAX_ATTEMPTS, 1 + (self.limit - self.cost(1)) // cycle)

    def expected_until_success(self, p: float) -> float | None:
        p = probability(p)
        if p == 0:
            return None
        return self.item + self.preparation + (self.rune + self.rebuild) / p - self.rebuild

    def expected_capped_cost(self, p: float, cap: int) -> float:
        p = probability(p)
        integer(cap, 0, MAX_ATTEMPTS, "Plafond")
        if cap == 0:
            return 0.0
        mean = expected_capped_attempts(p, cap)
        return self.item + self.preparation + self.rune * mean + self.rebuild * (mean - 1)


@dataclass(frozen=True)
class CampaignResult:
    experiments: int
    cap: int
    successes: int
    mean_used: float
    mean_cost: float
    used_p50: int
    used_p95: int
    success_interval: tuple[float, float]
    seed: int


def run_campaigns(p: float, cap: int, experiments: int, budget: Budget, seed: int) -> CampaignResult:
    """Echantillonnage inverse geometrique, O(experiences), sans remontage fictif rune par rune."""
    p = probability(p)
    integer(cap, 0, MAX_ATTEMPTS, "Plafond")
    integer(experiments, 1, MAX_EXPERIMENTS, "Campagnes")
    integer(seed, 0, 2**64 - 1, "Graine")
    rng = random.Random(seed)
    used, successes = [], 0
    logarithm = math.log1p(-p) if 0 < p < 1 else None
    within = success_within(p, cap)
    for _ in range(experiments):
        draw = rng.random()
        first = (
            None if draw >= within else 1 if p == 1
            else math.floor(math.log1p(-draw) / logarithm) + 1
        )
        hit = first is not None and first <= cap
        successes += int(hit)
        used.append(first if hit else cap)
    ordered = sorted(used)
    mean = sum(used) / experiments
    costs = sum(budget.cost(n) for n in used) / experiments
    return CampaignResult(
        experiments, cap, successes, mean, costs,
        ordered[math.ceil(0.50 * experiments) - 1],
        ordered[math.ceil(0.95 * experiments) - 1],
        wilson_interval(successes, experiments), seed,
    )
