#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord
from discord.ext import commands

from utils.channel_resolver import resolve_text_channel
from utils.console_json_store import ConsoleJSONSnapshotStore

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Compatibilité avec l'existant : ces constantes restent importables par les tests
# et par les autres modules éventuels.
INVITES_ROLE_NAME = os.getenv("WELCOME_INVITE_ROLE_NAME", "Invites")
VALIDATED_ROLE_NAME = os.getenv(
    "WELCOME_VALIDATED_ROLE_NAME",
    os.getenv("VALIDATED_ROLE_NAME", "Membre valide d Evolution"),
)
GENERAL_CHANNEL_NAME = os.getenv(
    "WELCOME_GENERAL_CHANNEL_NAME",
    os.getenv("GENERAL_CHANNEL_NAME", "📄 Général 📄"),
)
RECRUITMENT_CHANNEL_NAME = os.getenv(
    "WELCOME_RECRUITMENT_CHANNEL_NAME",
    os.getenv("RECRUITMENT_CHANNEL_NAME", "📌 Recrutement 📌"),
)
WELCOME_CHANNEL_NAME = os.getenv("WELCOME_CHANNEL_NAME", "𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞")
STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", os.getenv("STAFF_ROLE_NAME", "Staff"))
STAFF_CHANNEL_NAME = os.getenv("STAFF_CHANNEL_NAME", "general-staff")
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", "console")

DATA_FILE = os.getenv("WELCOME_DATA_FILE", os.path.join(BASE_DIR, "welcome_data.json"))
WELCOME_IMAGE_PATH = os.getenv("WELCOME_IMAGE_PATH", os.path.join(BASE_DIR, "welcome1.png"))
WELCOME_MARKER = "===WELCOME==="
WELCOME_RULES_URL = (os.getenv("WELCOME_RULES_URL") or "").strip()
WELCOME_PENDING_TTL_SECONDS = int(os.getenv("WELCOME_PENDING_TTL_SECONDS", str(7 * 24 * 60 * 60)))
WELCOME_PUBLIC_FALLBACK = (os.getenv("WELCOME_PUBLIC_FALLBACK", "1") or "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
TIMEOUT_RESPONSE = 300.0  # Conservé pour compatibilité avec l'ancien module.

CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
EVERYONE_HERE_RE = re.compile(r"@(?=everyone\b|here\b)", re.IGNORECASE)
DISCORD_MENTION_RE = re.compile(r"<(@!?|@&|#)\d+>")
URL_LIKE_RE = re.compile(r"https?://|discord\.gg/", re.IGNORECASE)
VALID_STAGES = {"reglement", "status", "pseudo", "recruiter"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except (TypeError, ValueError):
        return max(default, minimum)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sanitize_display_text(value: str | None, *, default: str = "non", max_len: int = 64) -> str:
    """Nettoie un texte utilisateur avant de l'afficher dans Discord.

    Objectif sécurité : neutraliser les mentions, retirer les caractères de
    contrôle, normaliser les caractères exotiques et éviter l'injection via
    backticks dans les blocs Markdown.
    """
    text = unicodedata.normalize("NFKC", value or "").strip()
    text = CONTROL_CHARS_RE.sub("", text)
    text = EVERYONE_HERE_RE.sub("@\u200b", text)
    text = DISCORD_MENTION_RE.sub("[mention retirée]", text)
    text = text.replace("`", "ʼ")
    text = " ".join(text.split())

    if max_len > 0 and len(text) > max_len:
        text = text[:max_len].rstrip() + "…"

    return text or default


def _safe_nickname(value: str | None, fallback: str) -> str:
    nickname = _sanitize_display_text(value, default=fallback, max_len=32)
    return nickname[:32] or fallback[:32]


def _normalize_reply(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").strip().lower()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    cleaned = []
    for ch in text:
        if ch.isalnum() or ch.isspace():
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def _human_dt(value: Any) -> str:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return "inconnu"
    return parsed.astimezone().strftime("%d/%m/%Y %H:%M")


class WelcomeCog(commands.Cog):
    """Parcours d'accueil MP pour les nouveaux arrivants.

    Le cog garde une compatibilité avec l'ancien format `welcome_data.json`
    tout en stockant maintenant des informations par serveur quand elles sont
    disponibles. La source de vérité de production reste le snapshot `#console`.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.already_welcomed: set[int] = set()  # héritage : user_ids complétés au moins une fois
        self.completed_welcomes: dict[str, dict[str, Any]] = {}
        self.pending_welcomes: dict[int, dict[str, Any]] = {}
        self.console_message_id: int | None = None
        self._init_task: asyncio.Task | None = None
        self._locks: dict[int, asyncio.Lock] = {}
        self.pending_ttl_seconds = _env_int(
            "WELCOME_PENDING_TTL_SECONDS",
            WELCOME_PENDING_TTL_SECONDS,
            minimum=60,
        )
        self.store = ConsoleJSONSnapshotStore(
            bot,
            marker=WELCOME_MARKER,
            filename="welcome_data.json",
            default_channel_name=CONSOLE_CHANNEL_NAME,
            history_limit_env="WELCOME_HISTORY_LIMIT",
        )
        self.load_welcomed_data()

    async def cog_load(self) -> None:
        if hasattr(self.bot, "wait_until_ready"):
            if self._init_task is None or self._init_task.done():
                self._init_task = asyncio.create_task(self._post_ready_init())

    def cog_unload(self) -> None:
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()

    async def _post_ready_init(self) -> None:
        await self.bot.wait_until_ready()
        await self._load_from_console()
        await self._cleanup_expired_pending()
        await self._persist_state()

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        return lock

    def _guild_id(self, guild: Any) -> int:
        """Retourne l'ID de guild en restant compatible avec les doubles de test."""
        try:
            return int(getattr(guild, "id", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _completion_key(self, guild_id: int, user_id: int) -> str:
        return f"{guild_id}:{user_id}"

    def _serialize_state(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "already_welcomed": sorted(int(x) for x in self.already_welcomed),
            "completed_welcomes": self.completed_welcomes,
            "pending_welcomes": {str(int(k)): dict(v) for k, v in self.pending_welcomes.items()},
        }

    def _coerce_pending_state(self, raw: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        stage = raw.get("stage")
        if stage not in VALID_STAGES:
            return None
        state = dict(raw)
        state.setdefault("created_at", _iso_now())
        state.setdefault("updated_at", state["created_at"])
        guild_id = state.get("guild_id")
        if guild_id is not None:
            try:
                state["guild_id"] = int(guild_id)
            except (TypeError, ValueError):
                state.pop("guild_id", None)
        return state

    def _load_payload(self, raw: Any) -> None:
        if isinstance(raw, list):
            self.already_welcomed = {int(x) for x in raw}
            self.completed_welcomes = {}
            self.pending_welcomes = {}
            return

        if not isinstance(raw, dict):
            return

        ids = raw.get("already_welcomed", [])
        if isinstance(ids, list):
            cleaned_ids: set[int] = set()
            for item in ids:
                try:
                    cleaned_ids.add(int(item))
                except (TypeError, ValueError):
                    continue
            self.already_welcomed = cleaned_ids

        completed = raw.get("completed_welcomes", {})
        if isinstance(completed, dict):
            cleaned_completed: dict[str, dict[str, Any]] = {}
            for key, value in completed.items():
                if not isinstance(value, dict):
                    continue
                cleaned_completed[str(key)] = dict(value)
            self.completed_welcomes = cleaned_completed

        # Ancien format : pending_welcomes est déjà un dict uid -> state.
        pending = raw.get("pending_welcomes", {})
        if isinstance(pending, dict):
            cleaned_pending: dict[int, dict[str, Any]] = {}
            for key, value in pending.items():
                try:
                    uid = int(key)
                except (TypeError, ValueError):
                    continue
                state = self._coerce_pending_state(value)
                if state is not None and not self._is_state_expired(state):
                    cleaned_pending[uid] = state
            self.pending_welcomes = cleaned_pending

    async def _load_from_console(self) -> None:
        message, payload = await self.store.load_latest(current_message_id=self.console_message_id)
        if payload is None:
            return
        self._load_payload(payload)
        self.console_message_id = getattr(message, "id", None)

    async def _persist_state(self) -> None:
        self.save_welcomed_data()
        message = await self.store.save(self._serialize_state(), current_message_id=self.console_message_id)
        if message is not None:
            self.console_message_id = message.id

    def load_welcomed_data(self) -> None:
        if not os.path.isfile(DATA_FILE):
            return
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._load_payload(raw)
        except Exception:
            log.warning("Erreur chargement %s.", DATA_FILE, exc_info=True)

    def save_welcomed_data(self) -> None:
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._serialize_state(), f, indent=2, ensure_ascii=False, sort_keys=True)
        except Exception:
            log.warning("Erreur sauvegarde %s.", DATA_FILE, exc_info=True)

    def _is_state_expired(self, state: dict[str, Any]) -> bool:
        updated_at = _parse_iso_datetime(state.get("updated_at")) or _parse_iso_datetime(state.get("created_at"))
        if updated_at is None:
            return False
        return _utcnow() - updated_at > timedelta(seconds=self.pending_ttl_seconds)

    async def _cleanup_expired_pending(self) -> None:
        expired = [uid for uid, state in self.pending_welcomes.items() if self._is_state_expired(state)]
        for uid in expired:
            self.pending_welcomes.pop(uid, None)
        if expired:
            log.debug("Welcome pending expirés nettoyés: %s", expired)

    def _touch_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _iso_now()

    def _normalize_reply(self, value: str) -> str:
        return _normalize_reply(value)

    def _is_yes(self, value: str) -> bool:
        normalized = self._normalize_reply(value)
        if not normalized:
            return False
        if normalized.startswith("oui"):
            return True
        return normalized in {
            "ok",
            "okay",
            "ouais",
            "ouai",
            "dac",
            "daccord",
            "d accord",
            "accepte",
            "jaccepte",
            "j accepte",
            "lu et approuve",
        }

    def _is_cancel(self, value: str) -> bool:
        return self._normalize_reply(value) in {"annuler", "cancel", "stop", "quitter"}

    def _is_restart(self, value: str) -> bool:
        return self._normalize_reply(value) in {"recommencer", "restart", "reprendre", "reset"}

    def _is_help(self, value: str) -> bool:
        return self._normalize_reply(value) in {"aide", "help", "?"}

    def _resolve_status(self, value: str) -> Optional[str]:
        normalized = self._normalize_reply(value)
        if normalized in {"m", "membre", "guilde", "guild", "guildie"} or normalized.startswith("membre"):
            return "membre"
        if normalized in {"i", "invite", "invites", "visiteur", "visiteuse"} or normalized.startswith("inv"):
            return "invite"
        return None

    def _member_has_role(self, member: discord.Member, expected_name: str) -> bool:
        target = self._normalize_reply(expected_name)
        return any(self._normalize_reply(getattr(role, "name", "")) == target for role in getattr(member, "roles", []))

    def _is_onboarded(self, member: discord.Member) -> bool:
        return self._member_has_role(member, INVITES_ROLE_NAME) or self._member_has_role(member, VALIDATED_ROLE_NAME)

    def _find_role(self, guild: discord.Guild, expected_name: str):
        target = self._normalize_reply(expected_name)
        for role in getattr(guild, "roles", []) or []:
            if self._normalize_reply(getattr(role, "name", "")) == target:
                return role
        return None

    def _get_guild(self, guild_id: int | None) -> Optional[discord.Guild]:
        if guild_id is None:
            return None
        get_guild = getattr(self.bot, "get_guild", None)
        if callable(get_guild):
            guild = get_guild(int(guild_id))
            if guild is not None:
                return guild
        for guild in getattr(self.bot, "guilds", []) or []:
            if getattr(guild, "id", None) == int(guild_id):
                return guild
        return None

    def _find_member_for_user(self, user_id: int) -> discord.Member | None:
        """Retourne le membre Discord concerné par le MP.

        Priorité :
        1. serveur déjà présent dans l'état pending ;
        2. serveur où le membre n'est pas encore onboardé ;
        3. premier serveur connu.
        """
        state = self.pending_welcomes.get(user_id)
        guild_id = state.get("guild_id") if isinstance(state, dict) else None
        guild = self._get_guild(guild_id)
        if guild is not None:
            member = guild.get_member(user_id)
            if member is not None:
                return member

        first_member = None
        for candidate_guild in getattr(self.bot, "guilds", []) or []:
            member = candidate_guild.get_member(user_id)
            if member is None:
                continue
            if first_member is None:
                first_member = member
            if not self._is_onboarded(member):
                return member
        return first_member

    def _mark_welcomed(
        self,
        user_or_member: int | discord.Member,
        *,
        status: str | None = None,
        dofus_pseudo: str | None = None,
        recruiter_pseudo: str | None = None,
        role_applied: bool | None = None,
    ) -> None:
        """Marque le parcours comme terminé.

        Accepte encore un simple `user_id` pour conserver la compatibilité avec
        les anciens tests et appels internes.
        """
        if isinstance(user_or_member, int):
            user_id = int(user_or_member)
            self.already_welcomed.add(user_id)
            self.save_welcomed_data()
            return

        member = user_or_member
        user_id = int(member.id)
        self.already_welcomed.add(user_id)
        record = {
            "guild_id": str(self._guild_id(member.guild)),
            "user_id": str(user_id),
            "status": status or "unknown",
            "discord_display_name": _sanitize_display_text(getattr(member, "display_name", str(user_id)), default=str(user_id)),
            "completed_at": _iso_now(),
        }
        if dofus_pseudo:
            record["dofus_pseudo"] = _sanitize_display_text(dofus_pseudo, default="", max_len=64)
        if recruiter_pseudo:
            record["recruiter_pseudo"] = _sanitize_display_text(recruiter_pseudo, default="non", max_len=64)
        if role_applied is not None:
            record["role_applied"] = bool(role_applied)

        self.completed_welcomes[self._completion_key(self._guild_id(member.guild), user_id)] = record
        self.save_welcomed_data()

    def _status_prompt(self) -> str:
        return (
            "**Parfait !** Maintenant, dis-moi : tu es **membre** de la guilde ou simplement **invité** sur le serveur ?\n\n"
            "Réponds par `membre` ou `invite`."
        )

    def _pseudo_prompt(self) -> str:
        return (
            "**Super nouvelle !** Quel est ton pseudo exact sur Dofus ?\n\n"
            "Écris uniquement ton pseudo, sans mention Discord."
        )

    def _recruiter_prompt(self) -> str:
        return (
            "Dernière étape : qui t’a invité à nous rejoindre ?\n\n"
            "Tu peux indiquer un pseudo Discord ou Dofus. Si tu ne sais pas, réponds simplement `non`."
        )

    def _help_message(self, state: Optional[dict[str, Any]]) -> str:
        stage = (state or {}).get("stage")
        base = (
            "Je suis le parcours d’accueil automatique d’**Evolution**.\n"
            "Tu peux écrire `annuler` pour stopper ou `recommencer` pour repartir du début.\n\n"
        )
        if stage == "reglement":
            return base + "Étape actuelle : lis le règlement, puis réponds `oui` si tu l’acceptes."
        if stage == "status":
            return base + "Étape actuelle : réponds `membre` si tu es dans la guilde, ou `invite` sinon."
        if stage == "pseudo":
            return base + "Étape actuelle : donne ton pseudo Dofus exact."
        if stage == "recruiter":
            return base + "Étape actuelle : indique qui t’a invité, ou `non`."
        return base + "Je peux relancer l’accueil si tu m’envoies un message."

    async def _safe_send(self, channel: Any, content: str | None = None, **kwargs: Any) -> Any:
        kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
        try:
            return await channel.send(content, **kwargs)
        except TypeError:
            # Certains doubles de test n'acceptent pas toutes les kwargs.
            kwargs.pop("allowed_mentions", None)
            return await channel.send(content, **kwargs)

    async def _send_welcome_intro(self, member: discord.Member, dm_channel: discord.DMChannel) -> None:
        description = (
            "Nous sommes ravis de t’accueillir parmi nous.\n\n"
            "Avant de te donner accès aux bons salons, j’ai besoin de valider trois choses :\n"
            "1. que tu as lu et accepté le règlement ;\n"
            "2. si tu es **membre** de la guilde ou **invité** ;\n"
            "3. ton pseudo Dofus si tu rejoins la guilde.\n\n"
            "Pour commencer, réponds simplement par **oui** une fois le règlement lu et accepté."
        )

        embed = discord.Embed(
            title=f"Bienvenue dans Evolution, {member.display_name} !",
            description=description,
            color=discord.Color.green(),
        )
        embed.set_footer(text="Tu peux écrire “aide”, “annuler” ou “recommencer” à tout moment.")

        view = None
        if WELCOME_RULES_URL:
            embed.add_field(name="Règlement", value=f"[Ouvrir le règlement]({WELCOME_RULES_URL})", inline=False)
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Lire le règlement", url=WELCOME_RULES_URL))

        file = None
        if os.path.isfile(WELCOME_IMAGE_PATH):
            file = discord.File(WELCOME_IMAGE_PATH, filename="welcome1.png")
            embed.set_image(url="attachment://welcome1.png")

        kwargs: dict[str, Any] = {"embed": embed}
        if file is not None:
            kwargs["file"] = file
        if view is not None:
            kwargs["view"] = view
        await self._safe_send(dm_channel, **kwargs)

    async def _start_welcome_flow(
        self,
        member: discord.Member,
        dm_channel: discord.DMChannel | None = None,
        *,
        force: bool = False,
    ) -> None:
        existing = self.pending_welcomes.get(member.id)
        if existing and not force and not self._is_state_expired(existing):
            channel = dm_channel or await member.create_dm()
            await self._safe_send(channel, self._help_message(existing))
            await self._send_current_prompt(channel, existing)
            return

        channel = dm_channel or await member.create_dm()
        await self._send_welcome_intro(member, channel)
        self.pending_welcomes[member.id] = {
            "schema_version": 2,
            "guild_id": int(self._guild_id(member.guild)),
            "stage": "reglement",
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
        }
        await self._persist_state()
        log.debug("Welcome flow started for user=%s guild=%s.", member.id, self._guild_id(member.guild))

    async def _send_current_prompt(self, channel: Any, state: dict[str, Any]) -> None:
        stage = state.get("stage")
        if stage == "reglement":
            await self._safe_send(channel, "Réponds par **oui** quand tu as lu et accepté le règlement.")
        elif stage == "status":
            await self._safe_send(channel, self._status_prompt())
        elif stage == "pseudo":
            await self._safe_send(channel, self._pseudo_prompt())
        elif stage == "recruiter":
            await self._safe_send(channel, self._recruiter_prompt())

    def _validate_pseudo(self, value: str) -> tuple[Optional[str], Optional[str]]:
        raw = (value or "").strip()
        if not raw:
            return None, "J’ai besoin de ton pseudo Dofus exact pour continuer."
        if DISCORD_MENTION_RE.search(raw) or EVERYONE_HERE_RE.search(raw):
            return None, "Écris le pseudo en texte simple, sans mention Discord."
        if URL_LIKE_RE.search(raw):
            return None, "Le pseudo ne doit pas contenir de lien."
        safe = _sanitize_display_text(raw, default="", max_len=32)
        normalized = self._normalize_reply(safe)
        if len(safe) < 2 or normalized in {"non", "aucun", "rien", "invite"}:
            return None, "Ce pseudo semble invalide. Donne ton pseudo Dofus exact, par exemple `Mon-Pseudo`."
        return safe, None

    def _validate_recruiter(self, value: str) -> tuple[str, Optional[str]]:
        raw = (value or "").strip()
        if not raw:
            return "non", None
        normalized = self._normalize_reply(raw)
        if normalized in {"non", "aucun", "personne", "je sais pas", "sais pas", "nsp"}:
            return "non", None
        if DISCORD_MENTION_RE.search(raw) or EVERYONE_HERE_RE.search(raw):
            return "", "Indique le pseudo en texte simple, sans mention Discord."
        if URL_LIKE_RE.search(raw):
            return "", "Le recruteur ne doit pas contenir de lien."
        return _sanitize_display_text(raw, default="non", max_len=64), None

    async def _notify_staff_issue(self, member: discord.Member, message: str) -> None:
        staff_channel = resolve_text_channel(
            member.guild,
            id_env="STAFF_CHANNEL_ID",
            name_env="STAFF_CHANNEL_NAME",
            default_name=STAFF_CHANNEL_NAME,
        )
        if staff_channel is None:
            staff_channel = resolve_text_channel(
                member.guild,
                id_env="CHANNEL_CONSOLE_ID",
                name_env="CHANNEL_CONSOLE",
                default_name=CONSOLE_CHANNEL_NAME,
            )
        if staff_channel is None:
            log.warning("Welcome staff issue for %s: %s", member.id, message)
            return
        await self._safe_send(
            staff_channel,
            f"⚠️ Accueil: {message}\nMembre: {member.mention} (`{member.id}`)",
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False, replied_user=False),
        )

    async def _add_role_if_available(
        self,
        member: discord.Member,
        role_name: str,
        dm_channel: Any,
        *,
        missing_message: str,
    ) -> bool:
        role = self._find_role(member.guild, role_name)
        if role is None:
            await self._safe_send(dm_channel, missing_message)
            await self._notify_staff_issue(member, f"rôle introuvable: `{role_name}`.")
            return False

        try:
            # Pas de kwarg reason ici : cela conserve la compatibilité avec les
            # tests existants et les doubles AsyncMock du projet.
            await member.add_roles(role)
            return True
        except discord.Forbidden:
            await self._safe_send(dm_channel, "Je n’ai pas les permissions nécessaires pour attribuer ton rôle. Le staff est prévenu.")
            await self._notify_staff_issue(member, f"permission insuffisante pour attribuer le rôle `{role_name}`.")
        except discord.HTTPException as exc:
            await self._safe_send(dm_channel, "Discord a refusé l’attribution du rôle. Le staff est prévenu.")
            await self._notify_staff_issue(member, f"erreur Discord pendant l’attribution du rôle `{role_name}`: {exc}.")
        return False

    async def _handle_invite_status(self, member: discord.Member, dm_channel: discord.DMChannel) -> None:
        role_applied = await self._add_role_if_available(
            member,
            INVITES_ROLE_NAME,
            dm_channel,
            missing_message="Le rôle **Invites** n’existe pas encore. Je préviens le staff pour correction.",
        )

        if role_applied:
            await self._safe_send(
                dm_channel,
                "C’est noté ! Je t’ai attribué le rôle **Invites**. "
                "Profite du serveur, et si tu veux rejoindre la guilde plus tard, fais signe au staff.",
            )

        self.pending_welcomes.pop(member.id, None)
        self._mark_welcomed(member, status="invite", role_applied=role_applied)
        await self._persist_state()

    async def _update_member_nickname(self, member: discord.Member, dofus_pseudo: str) -> bool:
        try:
            await member.edit(
                nick=_safe_nickname(dofus_pseudo, member.display_name),
                reason="Validation welcome Evolution",
            )
            return True
        except discord.Forbidden:
            await self._notify_staff_issue(member, "permission insuffisante pour renommer le membre.")
        except discord.HTTPException as exc:
            await self._notify_staff_issue(member, f"erreur Discord pendant le renommage: {exc}.")
        return False

    async def _auto_register_player(self, member: discord.Member, dofus_pseudo: str) -> None:
        players_cog = self.bot.get_cog("PlayersCog") if hasattr(self.bot, "get_cog") else None
        if players_cog is None:
            log.debug("PlayersCog missing during auto registration for %s.", member.id)
            return
        try:
            await players_cog.auto_register_member(
                discord_id=member.id,
                discord_display_name=member.display_name,
                dofus_pseudo=dofus_pseudo,
            )
            log.debug("PlayersCog auto registration done for %s.", member.id)
        except Exception:
            log.warning("PlayersCog auto registration failed for %s.", member.id, exc_info=True)
            await self._notify_staff_issue(member, "l’inscription automatique dans PlayersCog a échoué.")

    async def _announce_member_registration(
        self,
        member: discord.Member,
        *,
        dofus_pseudo: str,
        recruiter_pseudo: str,
        recruitment_date: str,
    ) -> None:
        general_channel = resolve_text_channel(
            member.guild,
            id_env="GENERAL_CHANNEL_ID",
            name_env="GENERAL_CHANNEL_NAME",
            default_name=GENERAL_CHANNEL_NAME,
        )
        if general_channel:
            embed = discord.Embed(
                title="Nouvelle recrue validée !",
                description=(
                    f"{member.mention} rejoint officiellement nos rangs sous le pseudo Dofus "
                    f"**{dofus_pseudo}**.\n\n"
                    "Bienvenue dans **Evolution** !"
                ),
                color=discord.Color.green(),
            )
            await self._safe_send(
                general_channel,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=[member],
                    roles=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
        else:
            log.debug("General channel not found for welcome announcement.")

        recruitment_channel = resolve_text_channel(
            member.guild,
            id_env="RECRUITMENT_CHANNEL_ID",
            name_env="RECRUITMENT_CHANNEL_NAME",
            default_name=RECRUITMENT_CHANNEL_NAME,
        )
        if recruitment_channel:
            recruiter_info = (
                "n’a pas indiqué de recruteur"
                if self._normalize_reply(recruiter_pseudo) == "non"
                else f"a été invité par **{recruiter_pseudo}**"
            )
            embed = discord.Embed(
                title="Recrutement enregistré",
                description=(
                    f"Le joueur **{dofus_pseudo}** a rejoint la guilde le **{recruitment_date}** "
                    f"et {recruiter_info}."
                ),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"Discord ID: {member.id}")
            await self._safe_send(recruitment_channel, embed=embed)
        else:
            log.debug("Recruitment channel not found for welcome announcement.")

    async def _finalize_member_registration(
        self,
        member: discord.Member,
        dm_channel: discord.DMChannel,
        dofus_pseudo: str,
        recruiter_pseudo: str,
    ) -> None:
        safe_dofus_pseudo = _sanitize_display_text(dofus_pseudo, default=member.display_name, max_len=32)
        safe_recruiter_pseudo = _sanitize_display_text(recruiter_pseudo, default="non", max_len=64)
        recruitment_date = discord.utils.utcnow().strftime("%d/%m/%Y")

        role_applied = await self._add_role_if_available(
            member,
            VALIDATED_ROLE_NAME,
            dm_channel,
            missing_message="Le rôle **Membre valide d’Evolution** est introuvable. Je préviens le staff pour correction.",
        )
        await self._update_member_nickname(member, safe_dofus_pseudo)
        await self._auto_register_player(member, safe_dofus_pseudo)

        await self._safe_send(
            dm_channel,
            f"Génial, **{safe_dofus_pseudo}** ! Ton accueil est terminé. "
            "Bienvenue officiellement dans la guilde **Evolution**.",
        )

        await self._announce_member_registration(
            member,
            dofus_pseudo=safe_dofus_pseudo,
            recruiter_pseudo=safe_recruiter_pseudo,
            recruitment_date=recruitment_date,
        )

        self._mark_welcomed(
            member,
            status="membre",
            dofus_pseudo=safe_dofus_pseudo,
            recruiter_pseudo=safe_recruiter_pseudo,
            role_applied=role_applied,
        )

    async def _handle_special_reply(self, member: discord.Member, message: discord.Message, state: Optional[dict[str, Any]]) -> bool:
        content = message.content or ""
        if self._is_cancel(content):
            self.pending_welcomes.pop(member.id, None)
            await self._persist_state()
            await self._safe_send(
                message.channel,
                "Parcours d’accueil annulé. Tu peux me réécrire `recommencer` quand tu veux le relancer.",
            )
            return True

        if self._is_restart(content):
            self.pending_welcomes.pop(member.id, None)
            await self._persist_state()
            await self._start_welcome_flow(member, message.channel, force=True)
            return True

        if self._is_help(content):
            await self._safe_send(message.channel, self._help_message(state))
            if state:
                await self._send_current_prompt(message.channel, state)
            return True

        return False

    async def _handle_welcome_message(self, member: discord.Member, message: discord.Message) -> None:
        content = message.content or ""
        lock = self._lock_for(member.id)
        async with lock:
            if self._is_onboarded(member):
                self.pending_welcomes.pop(member.id, None)
                self._mark_welcomed(member, status="already_onboarded", role_applied=True)
                await self._persist_state()
                return

            state = self.pending_welcomes.get(member.id)
            if state and self._is_state_expired(state):
                self.pending_welcomes.pop(member.id, None)
                await self._persist_state()
                await self._safe_send(message.channel, "Ton ancien parcours d’accueil a expiré, je le relance proprement.")
                await self._start_welcome_flow(member, message.channel, force=True)
                return

            if await self._handle_special_reply(member, message, state):
                return

            if state is None:
                status = self._resolve_status(content)
                if self._is_yes(content):
                    self.pending_welcomes[member.id] = {
                        "schema_version": 2,
                        "guild_id": int(self._guild_id(member.guild)),
                        "stage": "status",
                        "created_at": _iso_now(),
                        "updated_at": _iso_now(),
                    }
                    await self._persist_state()
                    await self._safe_send(message.channel, self._status_prompt())
                    return
                if status == "invite":
                    await self._handle_invite_status(member, message.channel)
                    return
                if status == "membre":
                    self.pending_welcomes[member.id] = {
                        "schema_version": 2,
                        "guild_id": int(self._guild_id(member.guild)),
                        "stage": "pseudo",
                        "created_at": _iso_now(),
                        "updated_at": _iso_now(),
                    }
                    await self._persist_state()
                    await self._safe_send(message.channel, self._pseudo_prompt())
                    return
                await self._start_welcome_flow(member, message.channel)
                return

            stage = state.get("stage")
            if stage == "reglement":
                if not self._is_yes(content):
                    await self._safe_send(message.channel, "Pour continuer, réponds simplement par **oui** après avoir lu et accepté le règlement.")
                    return
                state["stage"] = "status"
                self._touch_state(state)
                await self._persist_state()
                await self._safe_send(message.channel, self._status_prompt())
                return

            if stage == "status":
                status = self._resolve_status(content)
                if not status:
                    await self._safe_send(message.channel, "Réponds par `membre` ou `invite` pour continuer.")
                    return
                if status == "invite":
                    await self._handle_invite_status(member, message.channel)
                    return
                state["stage"] = "pseudo"
                self._touch_state(state)
                await self._persist_state()
                await self._safe_send(message.channel, self._pseudo_prompt())
                return

            if stage == "pseudo":
                dofus_pseudo, error = self._validate_pseudo(content)
                if error:
                    await self._safe_send(message.channel, error)
                    return
                state["dofus_pseudo"] = dofus_pseudo
                state["stage"] = "recruiter"
                self._touch_state(state)
                await self._persist_state()
                await self._safe_send(message.channel, self._recruiter_prompt())
                return

            if stage == "recruiter":
                recruiter_pseudo, error = self._validate_recruiter(content)
                if error:
                    await self._safe_send(message.channel, error)
                    return
                dofus_pseudo = state.get("dofus_pseudo") or member.display_name
                await self._finalize_member_registration(member, message.channel, dofus_pseudo, recruiter_pseudo)
                self.pending_welcomes.pop(member.id, None)
                await self._persist_state()
                return

            # État corrompu : on repart proprement.
            self.pending_welcomes.pop(member.id, None)
            await self._persist_state()
            await self._start_welcome_flow(member, message.channel, force=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        log.debug("Welcome join event for %s.", member.id)
        if member.bot:
            return
        if self._is_onboarded(member):
            self._mark_welcomed(member, status="already_onboarded", role_applied=True)
            await self._persist_state()
            return
        if member.id in self.pending_welcomes and not self._is_state_expired(self.pending_welcomes[member.id]):
            return
        try:
            await self._start_welcome_flow(member)
        except discord.Forbidden:
            await self.fallback_public_greeting(member)
        except discord.HTTPException:
            log.warning("Welcome DM failed for %s.", member.id, exc_info=True)
            await self.fallback_public_greeting(member)
        except Exception:
            log.warning("Welcome flow unexpected failure for %s.", member.id, exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if getattr(message.author, "bot", False):
            return
        if not isinstance(message.channel, discord.DMChannel):
            return
        if message.content and message.content.strip().startswith("!"):
            return
        member = self._find_member_for_user(message.author.id)
        if not member:
            return
        await self._handle_welcome_message(member, message)

    async def fallback_public_greeting(self, member: discord.Member) -> None:
        if not WELCOME_PUBLIC_FALLBACK:
            await self._notify_staff_issue(member, "MP fermés et fallback public désactivé.")
            return

        welcome_channel = resolve_text_channel(
            member.guild,
            id_env="WELCOME_CHANNEL_ID",
            name_env="WELCOME_CHANNEL_NAME",
            default_name=WELCOME_CHANNEL_NAME,
        )
        general_channel = resolve_text_channel(
            member.guild,
            id_env="GENERAL_CHANNEL_ID",
            name_env="GENERAL_CHANNEL_NAME",
            default_name=GENERAL_CHANNEL_NAME,
        )
        target_channel = welcome_channel or general_channel
        if target_channel is None:
            log.warning("Fallback welcome impossible: aucun salon public trouvé pour guild=%s.", self._guild_id(member.guild))
            await self._notify_staff_issue(member, "MP fermés et aucun salon public d’accueil/général trouvé.")
            return

        extra = " Active tes MP puis écris-moi pour finaliser ton accès."
        if welcome_channel and target_channel != welcome_channel:
            extra += f" Tu peux aussi passer par {welcome_channel.mention}."

        await self._safe_send(
            target_channel,
            f"👋 {member.mention}, je n’ai pas pu t’envoyer de message privé. "
            f"{extra} Bienvenue parmi nous !",
            allowed_mentions=discord.AllowedMentions(
                users=[member],
                roles=False,
                everyone=False,
                replied_user=False,
            ),
        )
        await self._notify_staff_issue(member, "MP fermés: impossible de lancer l’accueil privé.")

    def _can_manage_welcome(self, actor: discord.Member | None) -> bool:
        if actor is None or getattr(actor, "bot", False):
            return False
        permissions = getattr(actor, "guild_permissions", None)
        if permissions and (
            getattr(permissions, "administrator", False)
            or getattr(permissions, "manage_guild", False)
            or getattr(permissions, "manage_roles", False)
        ):
            return True
        target = self._normalize_reply(STAFF_ROLE_NAME)
        return any(self._normalize_reply(getattr(role, "name", "")) == target for role in getattr(actor, "roles", []) or [])

    async def _reply_no_permission(self, ctx: commands.Context) -> None:
        await ctx.reply(
            "Tu n’as pas la permission de gérer l’accueil.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.group(name="accueil", aliases=["welcome"], invoke_without_command=True)
    async def accueil_group(self, ctx: commands.Context) -> None:
        if not self._can_manage_welcome(ctx.author):
            await self._reply_no_permission(ctx)
            return
        await ctx.reply(
            "Commandes accueil : `!accueil statut @membre`, `!accueil relance @membre`, `!accueil reset @membre`.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @accueil_group.command(name="statut", aliases=["status"])
    async def accueil_status(self, ctx: commands.Context, member: discord.Member) -> None:
        if not self._can_manage_welcome(ctx.author):
            await self._reply_no_permission(ctx)
            return

        pending = self.pending_welcomes.get(member.id)
        completed = self.completed_welcomes.get(self._completion_key(self._guild_id(member.guild), member.id))
        onboarded = self._is_onboarded(member)

        embed = discord.Embed(title=f"Accueil de {member.display_name}", color=discord.Color.blurple())
        embed.add_field(name="Rôles d’accueil", value="oui" if onboarded else "non", inline=True)
        if pending:
            embed.add_field(name="État MP", value=str(pending.get("stage", "inconnu")), inline=True)
            embed.add_field(name="Dernière mise à jour", value=_human_dt(pending.get("updated_at")), inline=True)
        else:
            embed.add_field(name="État MP", value="aucun parcours en cours", inline=True)
        if completed:
            embed.add_field(name="Dernière validation", value=_human_dt(completed.get("completed_at")), inline=True)
            embed.add_field(name="Statut", value=str(completed.get("status", "inconnu")), inline=True)
            pseudo = completed.get("dofus_pseudo")
            if pseudo:
                embed.add_field(name="Pseudo Dofus", value=str(pseudo), inline=True)

        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=discord.AllowedMentions.none())

    @accueil_group.command(name="relance", aliases=["restart", "start"])
    async def accueil_restart(self, ctx: commands.Context, member: discord.Member) -> None:
        if not self._can_manage_welcome(ctx.author):
            await self._reply_no_permission(ctx)
            return
        if member.bot:
            await ctx.reply("Je ne lance pas l’accueil pour les bots.", mention_author=False)
            return

        try:
            await self._start_welcome_flow(member, force=True)
        except discord.Forbidden:
            await self.fallback_public_greeting(member)
            await ctx.reply(
                "MP fermés : j’ai envoyé un message public de fallback.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await ctx.reply(
            f"Accueil relancé en MP pour **{_sanitize_display_text(member.display_name)}**.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @accueil_group.command(name="reset", aliases=["reinitialiser", "réinitialiser"])
    async def accueil_reset(self, ctx: commands.Context, member: discord.Member) -> None:
        if not self._can_manage_welcome(ctx.author):
            await self._reply_no_permission(ctx)
            return

        self.pending_welcomes.pop(member.id, None)
        self.already_welcomed.discard(member.id)
        self.completed_welcomes.pop(self._completion_key(self._guild_id(member.guild), member.id), None)
        await self._persist_state()
        await ctx.reply(
            f"État d’accueil réinitialisé pour **{_sanitize_display_text(member.display_name)}**.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
