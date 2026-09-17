"""Mesures statistiques sans dépendance : intervalles et scores prédictifs."""
from __future__ import annotations

import math
from statistics import NormalDist


def wilson(successes: int, total: int, confidence: float = .95) -> tuple[float, float]:
    """Intervalle binomial de Wilson ; ne confond pas zéro observé et impossibilité."""
    if (type(total) is not int or total <= 0 or type(successes) is not int
            or not 0 <= successes <= total or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float)) or not 0 < confidence < 1):
        raise ValueError("Effectifs ou niveau de confiance invalides.")
    # La queue inférieure évite que (1 + confiance) / 2 s'arrondisse à 1.
    z = -NormalDist().inv_cdf((1 - confidence) / 2)
    p = successes / total
    scale = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / scale
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / scale
    return max(0.0, centre - radius), min(1.0, centre + radius)


def total_variation(left: dict, right: dict) -> float:
    """Distance entre deux distributions discrètes déjà normalisées."""
    for distribution in (left, right):
        if (not distribution or any(not math.isfinite(p) or p < 0 for p in distribution.values())
                or not math.isclose(sum(distribution.values()), 1, abs_tol=1e-9)):
            raise ValueError("Distribution non normalisée.")
    return sum(abs(left.get(key, 0) - right.get(key, 0)) for key in left.keys() | right.keys()) / 2
