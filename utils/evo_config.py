"""Configuration indépendante des anciennes IA. Aucune activation implicite payante."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import os
import logging

from utils.channel_resolver import resolve_text_channel

MODEL = "gpt-5.6-luna"
NANO = 1_000_000_000
log = logging.getLogger(__name__)


class EvoError(Exception):
    """Message volontairement publiable, jamais une exception de fournisseur brute."""


def resolve_console_channel(guild):
    """Je retrouve la console du serveur avec les noms de configuration existants."""
    for name_env in ("CHANNEL_CONSOLE", "CONSOLE_CHANNEL_NAME"):
        channel = resolve_text_channel(
            guild, id_env="CHANNEL_CONSOLE_ID", name_env=name_env,
        )
        if channel is not None:
            return channel
    return resolve_text_channel(guild, default_name="console")


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
    model: str = MODEL
    monthly_nano: int = 2 * NANO
    daily_nano: int = 120_000_000
    request_nano: int = 15_000_000
    max_input: int = 7500
    max_output: int = 400
    reasoning_effort: str = "medium"
    analysis_tokens: int = 3000
    writer_tokens: int = 3000
    specialist_tokens: int = 1800
    max_calls: int = 2
    deep_max_calls: int = 3
    specialist_output: int = 250
    max_tools: int = 5
    user_daily_calls: int = 60
    cooldown: int = 12
    history_channels: frozenset[int] = frozenset()
    public_member_data: bool = False
    public_job_data: bool = False
    max_sessions: int = 150
    session_seconds: int = 900
    knowledge_path: str = "config/evo_knowledge.json"

    def output_limit(self, role: str) -> int:
        """Je borne ensemble les tokens de raisonnement et de texte de chaque rôle."""
        if self.reasoning_effort not in {"none", "low", "medium"}:
            raise EvoError("EVO_REASONING_EFFORT doit être none, low ou medium.")
        limits = {
            "analysis": (self.analysis_tokens, 800, 4000),
            "writer": (self.writer_tokens, 800, 4000),
            "specialist": (self.specialist_tokens, 500, 3000),
        }
        if role not in limits:
            raise EvoError("Rôle IA non autorisé.")
        value, low, high = limits[role]
        if type(value) is not int or not low <= value <= high:
            raise EvoError("Limite de raisonnement IA invalide.")
        if self.reasoning_effort == "none":
            return min(self.specialist_output, self.max_output) if role == "specialist" else self.max_output
        return value

    @classmethod
    def from_env(cls, *, guilds=()) -> "EvoConfig":
        if os.getenv("EVO_MODEL", MODEL).strip() != MODEL:
            raise EvoError("EVO_MODEL doit rester gpt-5.6-luna : aucun remplacement payant automatique.")
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise EvoError("OPENAI_API_KEY manque dans la configuration Render.")
        configured_guilds = ids("EVO_GUILD_ID")
        available_guilds = {guild.id for guild in guilds}
        if not configured_guilds:
            if len(available_guilds) != 1:
                raise EvoError("Configure EVO_GUILD_ID lorsque le bot rejoint plusieurs serveurs.")
            configured_guilds = frozenset(available_guilds)
            log.debug("evo configuration inferred guild_id=%s", next(iter(configured_guilds)))
        if len(configured_guilds) != 1:
            raise EvoError("EVO_GUILD_ID doit désigner un seul serveur.")
        guild_id = next(iter(configured_guilds))
        if available_guilds and guild_id not in available_guilds:
            raise EvoError("Le serveur configuré pour Evo n'est pas accessible au bot.")
        channels = ids("EVO_CHANNEL_IDS")
        settings = cls(
            guild_id=guild_id, channel_ids=channels, api_key=key,
            monthly_nano=money("EVO_MONTHLY_USD", "2.00", "20"),
            daily_nano=money("EVO_DAILY_USD", "0.12", "2"),
            request_nano=money("EVO_REQUEST_USD", "0.015", "0.05"),
            max_input=integer("EVO_MAX_INPUT_TOKENS", 7500, 2000, 12000),
            max_output=min(integer("EVO_MAX_OUTPUT_TOKENS", 400, 200, 900), 400),
            reasoning_effort=os.getenv("EVO_REASONING_EFFORT", "medium").strip().lower(),
            analysis_tokens=integer("EVO_ANALYSIS_MAX_OUTPUT_TOKENS", 3000, 800, 4000),
            writer_tokens=integer("EVO_WRITER_MAX_OUTPUT_TOKENS", 3000, 800, 4000),
            specialist_tokens=integer("EVO_SPECIALIST_MAX_OUTPUT_TOKENS", 1800, 500, 3000),
            max_calls=min(integer("EVO_MAX_MODEL_CALLS", 2, 2, 3), 2),
            deep_max_calls=integer("EVO_MAX_DEEP_MODEL_CALLS", 3, 2, 3),
            max_tools=integer("EVO_MAX_TOOL_CALLS", 5, 1, 6),
            user_daily_calls=integer("EVO_USER_DAILY_CALLS", 60, 1, 180),
            cooldown=integer("EVO_COOLDOWN_SECONDS", 12, 5, 300),
            history_channels=ids("EVO_HISTORY_CHANNEL_IDS"),
            public_member_data=flag("EVO_PUBLIC_MEMBER_DATA"),
            public_job_data=flag("EVO_PUBLIC_JOB_DATA"),
        )
        for role in ("analysis", "writer", "specialist"):
            settings.output_limit(role)
        if settings.request_nano > settings.daily_nano or settings.daily_nano > settings.monthly_nano:
            raise EvoError("Les plafonds doivent respecter requête ≤ jour ≤ mois.")
        if settings.channel_ids and not settings.history_channels <= settings.channel_ids:
            raise EvoError("EVO_HISTORY_CHANNEL_IDS doit être inclus dans EVO_CHANNEL_IDS.")
        return settings
