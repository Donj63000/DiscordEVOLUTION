"""Politique explicite des commandes publiées et des fournisseurs disponibles."""

from __future__ import annotations

import logging
import os

import discord

log = logging.getLogger(__name__)

# Une ancienne clé peut être présente alors que le compte n'a plus de quota.
# La réactivation nécessite donc un consentement explicite ET une clé configurée.
GEMINI_COMMANDS = frozenset({"ia", "iahelp", "iaend", "bot", "analyse", "pl", "event"})
OPENAI_COMMANDS = frozenset({
    "iastaff", "annonce", "annonce-model", "annonce-config", "organisation-model",
})
BUILD_COMMANDS = frozenset({"build"})
EVO_COMMANDS = frozenset({"evo", "evo-oublier", "evo-budget"})
RETIRED_COMMANDS = frozenset({"event-rapide"})
MANAGED_UNAVAILABLE_ROOTS = GEMINI_COMMANDS | OPENAI_COMMANDS | RETIRED_COMMANDS | EVO_COMMANDS | BUILD_COMMANDS
RETIRED_SLASH_PATHS: dict[tuple[str, ...], str] = {
    ("stats", "classement"): (
        "Ce raccourci a été retiré du menu. Utilise `/ladder` pour le classement des profils."
    ),
    ("annonce-config",): (
        "Cette ancienne commande de configuration a été retirée du menu. "
        "Le Staff peut consulter les réglages du module d'annonces dans la configuration du bot."
    ),
}


def retired_slash_reason(path: tuple[str, ...]) -> str | None:
    """Je limite le retrait au chemin slash exact, sans désactiver son préfixe."""
    return RETIRED_SLASH_PATHS.get(path)


def enabled_flag(name: str, default: bool = False) -> bool:
    """Une valeur inconnue n'active jamais une fonctionnalité payante."""
    raw = os.getenv(name, "").strip().casefold()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "oui"}


def ai_service_enabled(provider: str) -> bool:
    # Le compteur Evo ne couvre pas les anciennes IA. Pas de réactivation implicite.
    if enabled_flag("EVO_ENABLED") and not enabled_flag("EVO_ALLOW_LEGACY_AI"):
        return False
    if not enabled_flag("ENABLE_AI_COMMANDS"):
        return False
    if provider == "staff":
        # IA Staff peut utiliser Gemini (par défaut) ou OpenAI : ne pas exiger
        # une clé OpenAI lorsqu'il est explicitement configuré pour Gemini.
        provider = (os.getenv("IASTAFF_BACKEND") or "gemini").strip().lower()
        if provider not in {"gemini", "openai"}:
            provider = "gemini"
    if provider == "gemini":
        return bool((os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip())
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY", "").strip())
    raise ValueError("Fournisseur IA inconnu.")


def unavailable_reason(qualified_name: str) -> str | None:
    root = qualified_name.split(" ", 1)[0]
    if root in BUILD_COMMANDS:
        return None if enabled_flag("BUILD_ENABLED") else "Evolution Build est désactivé par le Staff."
    if root in EVO_COMMANDS:
        return None if enabled_flag("EVO_ENABLED") else "Evo est désactivé par le Staff."
    if root in RETIRED_COMMANDS:
        return (
            "Cette ancienne commande a été retirée. Utilise `/activite creer` "
            "pour une sortie ou `/sondage` pour un vote."
        )
    provider = "gemini" if root in GEMINI_COMMANDS else "openai" if root in OPENAI_COMMANDS else None
    if root == "iastaff":
        provider = "staff"
    if provider and not ai_service_enabled(provider):
        return (
            "Les commandes IA sont désactivées pendant l'indisponibilité du service. "
            "Le calendrier, les activités et `/organisation` restent disponibles sans IA."
        )
    return None


def unavailable_roots() -> frozenset[str]:
    return frozenset(name for name in MANAGED_UNAVAILABLE_ROOTS if unavailable_reason(name))


def unavailable_slash_roots() -> frozenset[str]:
    """Je distingue les racines slash retirées des sous-commandes à préserver."""
    return unavailable_roots() | frozenset(
        path[0] for path in RETIRED_SLASH_PATHS if len(path) == 1
    )


def remove_unavailable_commands(bot) -> None:
    """Retire les points d'entrée, pas les cogs qui suivent des données déjà publiées."""
    for name in sorted(unavailable_roots()):
        prefix = bot.remove_command(name)
        native = bot.tree.remove_command(name, type=discord.AppCommandType.chat_input)
        if prefix is not None or native is not None:
            log.info("Commandes : retrait volontaire de /%s et de son accès préfixe.", name)
