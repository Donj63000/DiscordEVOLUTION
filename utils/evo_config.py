"""Configuration indépendante des anciennes IA. Aucune activation implicite payante."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path

MODEL = "gpt-5.6-luna"
NANO = 1_000_000_000


class EvoError(Exception):
    """Message volontairement publiable, jamais une exception de fournisseur brute."""


def flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
        raise EvoError(f"Configuration invalide : {name}.")
    return raw in {"1", "true", "yes", "on"}


def integer(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    if not raw.isascii() or not raw.isdigit() or not low <= int(raw) <= high:
        raise EvoError(f"Configuration invalide : {name} doit être entre {low} et {high}.")
    return int(raw)


def ids(name: str) -> frozenset[int]:
    values = os.getenv(name, "").strip()
    if not values:
        return frozenset()
    result = set()
    for part in values.split(","):
        part = part.strip()
        if not part.isascii() or not part.isdigit() or not 0 < int(part) < 2**64:
            raise EvoError(f"Identifiant Discord invalide dans {name}.")
        result.add(int(part))
    if len(result) > 50:
        raise EvoError(f"Trop d'identifiants dans {name}.")
    return frozenset(result)


def money(name: str, default: str, maximum: str) -> int:
    try:
        value = Decimal(os.getenv(name, default).strip())
    except (InvalidOperation, ValueError):
        raise EvoError(f"Montant USD invalide : {name}.") from None
    if not value.is_finite() or not Decimal("0.000001") <= value <= Decimal(maximum):
        raise EvoError(f"Montant USD invalide : {name}.")
    return int(value * NANO)


@dataclass(frozen=True)
class EvoConfig:
    guild_id: int
    channel_ids: frozenset[int]
    api_key: str = field(repr=False)
    database_url: str = field(default="", repr=False)
    sqlite_path: str = ""
    model: str = MODEL
    monthly_nano: int = 2 * NANO
    daily_nano: int = 120_000_000
    request_nano: int = 15_000_000
    max_input: int = 7500
    max_output: int = 600
    max_calls: int = 3
    max_tools: int = 5
    user_daily_calls: int = 60
    cooldown: int = 12
    history_channels: frozenset[int] = frozenset()
    public_member_data: bool = False
    max_sessions: int = 150
    session_seconds: int = 900
    knowledge_path: str = "config/evo_knowledge.json"

    @classmethod
    def from_env(cls) -> "EvoConfig":
        if os.getenv("EVO_MODEL", MODEL).strip() != MODEL:
            raise EvoError("EVO_MODEL doit rester gpt-5.6-luna : aucun remplacement payant automatique.")
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise EvoError("OPENAI_API_KEY manque dans la configuration Render.")
        guilds = ids("EVO_GUILD_ID")
        channels = ids("EVO_CHANNEL_IDS")
        if len(guilds) != 1 or not channels:
            raise EvoError("Configure EVO_GUILD_ID et EVO_CHANNEL_IDS avant d'activer /evo.")
        database = (os.getenv("EVO_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
        sqlite = os.getenv("EVO_SQLITE_PATH", "").strip()
        if database and not database.startswith(("postgresql://", "postgres://")):
            raise EvoError("EVO_DATABASE_URL doit désigner une base PostgreSQL.")
        if not database:
            if os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"):
                raise EvoError("Sur Render, /evo exige EVO_DATABASE_URL (ou DATABASE_URL) pour protéger le budget.")
            if not sqlite or not Path(sqlite).is_file():
                raise EvoError("Compteur durable absent. Configure PostgreSQL ; voir docs/EVO.md.")
        settings = cls(
            guild_id=next(iter(guilds)), channel_ids=channels, api_key=key,
            database_url=database, sqlite_path=sqlite,
            monthly_nano=money("EVO_MONTHLY_USD", "2.00", "20"),
            daily_nano=money("EVO_DAILY_USD", "0.12", "2"),
            request_nano=money("EVO_REQUEST_USD", "0.015", "0.05"),
            max_input=integer("EVO_MAX_INPUT_TOKENS", 7500, 2000, 12000),
            max_output=integer("EVO_MAX_OUTPUT_TOKENS", 600, 200, 900),
            max_calls=integer("EVO_MAX_MODEL_CALLS", 3, 2, 3),
            max_tools=integer("EVO_MAX_TOOL_CALLS", 5, 1, 6),
            user_daily_calls=integer("EVO_USER_DAILY_CALLS", 60, 1, 180),
            cooldown=integer("EVO_COOLDOWN_SECONDS", 12, 5, 300),
            history_channels=ids("EVO_HISTORY_CHANNEL_IDS"),
            public_member_data=flag("EVO_PUBLIC_MEMBER_DATA"),
        )
        if settings.request_nano > settings.daily_nano or settings.daily_nano > settings.monthly_nano:
            raise EvoError("Les plafonds doivent respecter requête ≤ jour ≤ mois.")
        if not settings.history_channels <= settings.channel_ids:
            raise EvoError("EVO_HISTORY_CHANNEL_IDS doit être inclus dans EVO_CHANNEL_IDS.")
        return settings
