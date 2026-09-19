"""Configuration séparée de Luna et du stockage historique du bot."""
import os
from dataclasses import dataclass, field
from .models import BuildError


def flag(name, default=False):
    value = os.getenv(name, "").strip().lower()
    return default if not value else value in {"1", "true", "yes", "oui", "on"}


def integer(name, default, low, high):
    try:
        value = int(os.getenv(name, str(default)))
        if not low <= value <= high:
            raise ValueError()
        return value
    except ValueError as exc:
        raise BuildError(f"Configuration {name} invalide ({low}–{high}).") from exc


@dataclass(frozen=True)
class Config:
    backend: str
    dsn: str = field(repr=False)
    quota: int = 20
    migrate: bool = False
    rules_path: str | None = None
    overrides_path: str | None = None
    max_views: int = 500

    @classmethod
    def from_env(cls):
        backend = os.getenv("BUILD_BACKEND", "postgres").strip().lower()
        if backend not in {"postgres", "memory"}:
            raise BuildError("BUILD_BACKEND doit être postgres ou memory (essai non durable).")
        return cls(backend, os.getenv("BUILD_DATABASE_URL", ""), integer("BUILD_MAX_PER_USER", 20, 1, 100),
                   flag("BUILD_AUTO_MIGRATE"), os.getenv("BUILD_RULES_FILE") or None,
                   os.getenv("BUILD_CATALOG_OVERRIDES") or None, integer("BUILD_MAX_VIEWS", 500, 10, 1000))
