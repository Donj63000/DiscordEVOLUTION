"""Admission des historiques en mémoire ; appels synchrones sur la boucle Discord."""
from __future__ import annotations

from utils.exo_math import integer

GLOBAL_HISTORY_BYTES = 32 * 1024 * 1024


def history_size(session) -> int:
    return session.sim.journal_bytes + session.observed.journal_bytes


class HistoryBudget:
    """Réserve avant le premier await ; aucun verrou réseau global nécessaire.

    Ce budget borne les octets JSON conservés, pas la taille exacte des objets
    Python. Leur surcoût et les copies transactionnelles doivent aussi être
    prévus par l'exploitant. Une réservation refusée ne change pas l'allocation.
    """

    def __init__(self, limit: int = GLOBAL_HISTORY_BYTES):
        self.limit = integer(limit, 1, 1024 ** 3, "Budget mémoire")
        self.allocations: dict[int, int] = {}
        self.used = 0

    def reserve(self, owner: int, size: int) -> None:
        integer(size, 0, self.limit, "Taille de l'historique")
        projected = self.used - self.allocations.get(owner, 0) + size
        if projected > self.limit:
            raise ValueError("Mémoire des ateliers pleine : exportez et fermez une séance avant de poursuivre.")
        self.allocations[owner] = size
        self.used = projected

    def release(self, owner: int) -> None:
        self.used -= self.allocations.pop(owner, 0)
