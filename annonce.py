#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands

from utils.channel_resolver import resolve_text_channel
from utils.openai_config import (
    resolve_staff_model,
    build_async_openai_client,
    normalise_staff_model,
    resolve_reasoning_effort,
)

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

log = logging.getLogger("annonce")


# =========================
# ENV / CONFIG
# =========================

def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}

def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default

def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default

# Rôles staff
STAFF_ROLE_ENV = os.getenv("IASTAFF_ROLE", "Staff")

# Canal annonces
DEFAULT_ANNOUNCE_NAME = os.getenv("ANNONCE_CHANNEL_NAME", "annonces")

# OpenAI
DEFAULT_MODEL = resolve_staff_model()  # basé sur OPENAI_STAFF_MODEL (ou défaut)
OPENAI_TIMEOUT = _env_float("ANNONCE_OPENAI_TIMEOUT", _env_float("IASTAFF_TIMEOUT", 120.0))
MAX_OUTPUT_TOKENS = _env_int("ANNONCE_MAX_OUTPUT_TOKENS", _env_int("IASTAFF_MAX_OUTPUT_TOKENS", 1800))
ANNONCE_TEMPERATURE = max(0.0, min(2.0, _env_float("ANNONCE_TEMPERATURE", _env_float("IASTAFF_TEMPERATURE", 0.6))))

# DM
DM_TIMEOUT = _env_int("ANNONCE_DM_TIMEOUT", 300)

# UX / sécurité
ANNONCE_DELETE_TRIGGER = _env_flag("ANNONCE_DELETE_TRIGGER", False)
ANNONCE_REQUIRE_CONFIRM = _env_flag("ANNONCE_REQUIRE_CONFIRM", True)
ANNONCE_MAX_ITERATIONS = max(1, _env_int("ANNONCE_MAX_ITERATIONS", 4))  # regen/modif max
ANNONCE_TARGET_MAX_CHARS = max(800, _env_int("ANNONCE_TARGET_MAX_CHARS", 1700))  # pour éviter split
ANNONCE_SAFE_MENTIONS = _env_flag("ANNONCE_SAFE_MENTIONS", True)

# Mentions: le bot les ajoute, l’IA n’en ajoute jamais
ANNONCE_DEFAULT_MENTIONS = (os.getenv("ANNONCE_DEFAULT_MENTIONS", "@everyone") or "@everyone").strip()

# Regex anti-mentions IA
_RE_EVERYONE = re.compile(r"@(?=everyone\b)", re.IGNORECASE)
_RE_HERE = re.compile(r"@(?=here\b)", re.IGNORECASE)
_RE_USER_ROLE = re.compile(r"<@([!&]?\d+)>")  # <@123>, <@!123>, <@&123>
_RE_CHANNEL = re.compile(r"<#(\d+)>")  # mention canal


QUESTIONS: list[str] = [
    "Quel est le sujet principal de l'annonce ?",
    "Quels sont les détails importants à inclure (dates, heures, lieu, lien, etc.) ?",
    "Quel est le public visé (tous les membres, un rôle précis, nouveau joueur, etc.) ?",
    "Quel ton souhaites-tu adopter (enthousiaste, sérieux, professionnel, motivant, etc.) ?",
    "Y a-t-il un appel à l'action ou des instructions à transmettre ?",
    "Faut-il mentionner des récompenses, avantages ou conséquences ?",
    "Autre chose à ajouter pour aider à rédiger l'annonce parfaite ?",
]


# =========================
# Helpers OpenAI extraction
# =========================

def _to_dict(obj) -> dict:
    """Convertit un objet OpenAI SDK en dict sans casser si SDK change."""
    for attr in ("model_dump", "to_dict", "dict"):
        try:
            fn = getattr(obj, attr, None)
            if callable(fn):
                d = fn()
                if isinstance(d, dict):
                    return d
        except Exception:
            pass
    try:
        d = getattr(obj, "__dict__", None)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}

def _gather_text_nodes(node) -> list[str]:
    out: list[str] = []
    if isinstance(node, str):
        if node.strip():
            out.append(node.strip())
        return out

    if isinstance(node, dict):
        # Responses API
        ot = node.get("output_text")
        if isinstance(ot, str) and ot.strip():
            out.append(ot.strip())

        # content blocks
        content = node.get("content")
        if isinstance(content, list):
            for part in content:
                out.extend(_gather_text_nodes(part))

        # common keys that may hold nested structures
        for k in ("output", "outputs", "choices", "message", "data", "response", "result", "delta"):
            v = node.get(k)
            if v is not None:
                out.extend(_gather_text_nodes(v))
        return out

    if isinstance(node, list):
        for it in node:
            out.extend(_gather_text_nodes(it))
        return out

    return out

def extract_generated_text(resp_obj) -> str:
    """Extraction robuste du texte généré, compatible Responses et ChatCompletions."""
    if not resp_obj:
        return ""
    try:
        direct = getattr(resp_obj, "output_text", None)
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
    except Exception:
        pass

    data = _to_dict(resp_obj)
    texts = _gather_text_nodes(data)

    # Fallback chat.completions: choices[0].message.content
    if not texts and isinstance(data.get("choices"), list):
        for ch in data["choices"]:
            m = ch.get("message") if isinstance(ch, dict) else None
            if isinstance(m, dict):
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    texts.append(c.strip())

    joined = "\n".join(t for t in texts if isinstance(t, str) and t.strip())
    return joined.strip()


# =========================
# Helpers Discord formatting
# =========================

def split_message_for_discord(text: str, limit: int = 2000) -> list[str]:
    """Split intelligent sans dépasser 2000 chars."""
    if not text:
        return []
    text = text.strip()
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text

    # On privilégie les gros séparateurs
    preferred_breaks = ["\n\n", "\n", " "]

    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break

        cut = -1
        window = remaining[:limit + 1]
        for sep in preferred_breaks:
            idx = window.rfind(sep)
            if idx > cut:
                cut = idx

        if cut <= 0:
            cut = limit
        else:
            # inclure le séparateur si \n ou espace
            cut = cut + (2 if remaining[cut:cut+2] == "\n\n" else 1)

        chunk = remaining[:cut].rstrip()
        if chunk:
            parts.append(chunk)
        remaining = remaining[cut:].lstrip()

    return [p for p in parts if p.strip()]

def _neutralize_ai_mentions(text: str) -> str:
    """Empêche l’IA de ping via @everyone/@here et mentions <@...>."""
    if not text:
        return text
    if not ANNONCE_SAFE_MENTIONS:
        return text

    # @everyone / @here
    text = _RE_EVERYONE.sub("@\u200b", text)
    text = _RE_HERE.sub("@\u200b", text)

    # user/role mentions <@...>
    text = _RE_USER_ROLE.sub(lambda m: "<@\u200b" + m.group(1) + ">", text)

    # channel mentions <#...> (rare mais safe)
    text = _RE_CHANNEL.sub(lambda m: "<#\u200b" + m.group(1) + ">", text)

    return text

def _strip_leading_everyone_here(text: str) -> str:
    """Supprime un @everyone/@here en tête si le modèle en a mis un malgré les instructions."""
    if not text:
        return text
    t = text.lstrip()
    # On supprime uniquement au début (évite d’altérer le corps)
    for token in ("@everyone", "@here"):
        if t.lower().startswith(token):
            t = t[len(token):].lstrip()
    return t

def _safe_reply(ctx: commands.Context, content: str):
    return ctx.reply(content, mention_author=False)

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# =========================
# Staff / Mentions parsing
# =========================

_ID_RE = re.compile(r"\d+")

def _parse_staff_roles(raw: str) -> Tuple[list[int], list[str]]:
    ids: list[int] = []
    names: list[str] = []
    for part in (raw or "").split(","):
        entry = part.strip()
        if not entry:
            continue
        if entry.isdigit():
            ids.append(int(entry))
        else:
            names.append(entry.lower())
    return ids, names

STAFF_ROLE_IDS, STAFF_ROLE_NAMES = _parse_staff_roles(STAFF_ROLE_ENV)

def _is_staff(member: discord.Member) -> bool:
    # Admin = OK
    try:
        if member.guild_permissions.administrator:
            return True
    except Exception:
        pass

    roles = getattr(member, "roles", []) or []
    for role in roles:
        try:
            if role.id in STAFF_ROLE_IDS:
                return True
        except Exception:
            pass
        try:
            if role.name and role.name.lower() in STAFF_ROLE_NAMES:
                return True
        except Exception:
            pass
    return False

def staff_only():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.CheckFailure("Commande utilisable uniquement sur le serveur (pas en DM).")
        member = ctx.author
        if not isinstance(member, discord.Member):
            raise commands.CheckFailure("Contexte membre indisponible.")
        if not _is_staff(member):
            raise commands.CheckFailure("Commande réservée au staff.")
        return True
    return commands.check(predicate)

def _take_digits(value: str | None) -> Optional[int]:
    if not value:
        return None
    m = _ID_RE.search(value)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None

def parse_mentions(raw: str, guild: discord.Guild) -> list[str]:
    """Accepte: @everyone, @here, <@&id>, @RoleName, RoleName, none/aucune."""
    cleaned = (raw or "").strip()
    if not cleaned:
        cleaned = ANNOUNCE_DEFAULT_MENTIONS

    lowered = cleaned.strip().lower()
    if lowered in {"aucun", "aucune", "none", "no", "0"}:
        return []

    tokens: list[str] = []
    seen: set[str] = set()

    for part in re.split(r"[\s,]+", cleaned):
        item = part.strip()
        if not item:
            continue
        low = item.lower()

        if low in {"@everyone", "everyone"}:
            if "@everyone" not in seen:
                tokens.append("@everyone")
                seen.add("@everyone")
            continue

        if low in {"@here", "here"}:
            if "@here" not in seen:
                tokens.append("@here")
                seen.add("@here")
            continue

        # mention role direct <@&id>
        if item.startswith("<@&") and item.endswith(">"):
            if item not in seen:
                tokens.append(item)
                seen.add(item)
            continue

        # si on a un ID pur
        maybe_id = _take_digits(item)
        if maybe_id:
            role = discord.utils.get(guild.roles, id=maybe_id)
            if role:
                mention = f"<@&{role.id}>"
                if mention not in seen:
                    tokens.append(mention)
                    seen.add(mention)
            continue

        # nom de rôle
        role_name = item.lstrip("@").strip()
        if role_name:
            role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), guild.roles)
            if role:
                mention = f"<@&{role.id}>"
                if mention not in seen:
                    tokens.append(mention)
                    seen.add(mention)

    return tokens

def build_allowed_mentions(mentions: list[str], guild: discord.Guild) -> discord.AllowedMentions:
    """Autorise uniquement les mentions demandées (évite ping accidentel)."""
    if not mentions:
        return discord.AllowedMentions.none()

    allow_everyone = any(m in {"@everyone", "@here"} for m in mentions)

    allowed_roles: list[discord.Role] = []
    for m in mentions:
        if m.startswith("<@&") and m.endswith(">"):
            rid = _take_digits(m)
            if not rid:
                continue
            role = discord.utils.get(guild.roles, id=rid)
            if role:
                allowed_roles.append(role)

    # users=False : l’IA ne doit pas ping des users
    # replied_user=False : évite de ping l’auteur au reply
    if allowed_roles:
        return discord.AllowedMentions(everyone=allow_everyone, roles=allowed_roles, users=False, replied_user=False)

    return discord.AllowedMentions(everyone=allow_everyone, roles=False, users=False, replied_user=False)


# =========================
# Session DM (anti-concurrence)
# =========================

@dataclass
class AnnounceSession:
    author_id: int
    guild_id: int
    started_at_iso: str
    answers: list[str]
    channel_override: str = ""
    mentions_raw: str = ""


# =========================
# Cog
# =========================

class AnnonceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.model = DEFAULT_MODEL
        self.client = build_async_openai_client(AsyncOpenAI, timeout=OPENAI_TIMEOUT)

        # 1 annonce DM à la fois par user
        self._user_locks: dict[int, asyncio.Lock] = {}

    # --------- utils ---------

    def _get_lock(self, user_id: int) -> asyncio.Lock:
        lock = self._user_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[user_id] = lock
        return lock

    def _find_announcement_channel(self, guild: discord.Guild, override: str = "") -> discord.TextChannel | None:
        # 1) override (si fourni via DM)
        if override.strip():
            # accepte mention #canal, id, ou nom
            candidate = override.strip()
            cid = _take_digits(candidate)
            if cid:
                ch = guild.get_channel(cid)
                if isinstance(ch, discord.TextChannel):
                    return ch
            # si #nom => enlever #
            if candidate.startswith("#"):
                candidate = candidate[1:].strip()
            ch = discord.utils.get(guild.text_channels, name=candidate)
            if isinstance(ch, discord.TextChannel):
                return ch
            # fallback normalize via resolve_text_channel
            ch = resolve_text_channel(guild, default_name=candidate)
            if ch:
                return ch

        # 2) env
        primary = os.getenv("ANNONCE_CHANNEL_NAME")
        ch = resolve_text_channel(
            guild,
            id_env="ANNONCE_CHANNEL_ID",
            name_env="ANNONCE_CHANNEL_NAME",
            default_name=primary or DEFAULT_ANNOUNCE_NAME,
        )
        if ch:
            return ch

        # 3) legacy
        legacy = os.getenv("ANNONCE_CHANNEL") or DEFAULT_ANNOUNCE_NAME
        if legacy and legacy != primary:
            ch = resolve_text_channel(guild, default_name=legacy)
            if ch:
                return ch

        # 4) fallback default
        if primary and primary != DEFAULT_ANNOUNCE_NAME:
            ch = resolve_text_channel(guild, default_name=DEFAULT_ANNOUNCE_NAME)
            if ch:
                return ch

        return None

    def _system_prompt(self) -> str:
        return (
            "Tu es EvolutionBOT, l'assistant du staff Discord 'Évolution' (Dofus Retro).\n"
            "Objectif: transformer les infos du staff en annonce claire et immédiatement publiable.\n"
            "Contraintes:\n"
            f"- Reste en français.\n"
            f"- Ton: respecter le ton demandé.\n"
            f"- Style: paragraphes courts + listes à puces si pertinent.\n"
            f"- Ne mets AUCUNE mention Discord (interdit: @everyone, @here, <@...>, <#...>).\n"
            f"- N'invente pas de date/heure: toute info temporelle doit venir du staff.\n"
            f"- Vise une longueur <= {ANNONCE_TARGET_MAX_CHARS} caractères si possible.\n"
            "Tu ne renvoies que le texte final de l'annonce, sans explications."
        )

    def _build_user_prompt(self, answers: list[str], author: discord.Member) -> str:
        staff_context = [
            f"Préparé par : {author.display_name} (ID: {author.id})",
            f"Date génération (UTC): {_now_iso()}",
        ]
        for question, answer in zip(QUESTIONS, answers):
            staff_context.append(f"- {question}\n  Réponse : {(answer or '').strip() or '(aucune précision)'}")

        return (
            "Rédige l'annonce finale à publier.\n"
            "Utilise un format lisible:\n"
            "- 1 ligne d'accroche ou titre court\n"
            "- corps en 2–5 paragraphes\n"
            "- puces si nécessaire\n"
            "- termine par un CTA si demandé\n\n"
            "Informations fournies:\n"
            + "\n".join(staff_context)
        )

    def _messages_for_responses_api(self, system_prompt: str, user_prompt: str) -> list[dict]:
        """Format compatible Responses API (content blocks)."""
        return [
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
        ]

    def _messages_for_chat_api(self, system_prompt: str, user_prompt: str) -> list[dict]:
        """Fallback ChatCompletions."""
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    async def _call_openai(self, *, system_prompt: str, user_prompt: str) -> str:
        if not self.client or not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY manquante ou client OpenAI indisponible.")

        # 1) Chemin principal: Responses API
        req = {
            "model": self.model,
            "input": self._messages_for_responses_api(system_prompt, user_prompt),
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "temperature": ANNONCE_TEMPERATURE,
            "store": False,
        }
        reasoning = resolve_reasoning_effort(self.model)
        if reasoning:
            req["reasoning"] = reasoning

        try:
            resp = await self.client.responses.create(**req)
            text = extract_generated_text(resp).strip()
            if text:
                return text
        except Exception as exc:
            log.warning("Annonce: Responses API failed, fallback ChatCompletions. err=%s", exc, exc_info=True)

        # 2) Fallback: ChatCompletions (utile si OPENAI_BASE_URL proxy ne supporte pas responses)
        try:
            chat_req = {
                "model": self.model,
                "messages": self._messages_for_chat_api(system_prompt, user_prompt),
                "max_tokens": MAX_OUTPUT_TOKENS,
                "temperature": ANNONCE_TEMPERATURE,
            }
            resp2 = await self.client.chat.completions.create(**chat_req)
            text2 = extract_generated_text(resp2).strip()
            if text2:
                return text2
        except Exception as exc2:
            raise RuntimeError(f"Erreur OpenAI (Responses + Chat fallback): {exc2}") from exc2

        raise RuntimeError("Réponse OpenAI vide.")

    async def _dm_send_long(self, dm: discord.DMChannel, text: str):
        chunks = split_message_for_discord(text, limit=2000)
        for c in chunks:
            await dm.send(c)

    async def _dm_ask(
        self,
        dm: discord.DMChannel,
        author: discord.User | discord.Member,
        prompt: str,
        *,
        allow_empty: bool = False,
    ) -> str:
        await dm.send(prompt)
        while True:
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author.id == author.id and m.channel.id == dm.id,
                    timeout=DM_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise TimeoutError("Temps écoulé.")

            content = (msg.content or "").strip()
            low = content.lower()

            if low in {"annule", "cancel", "stop"}:
                raise KeyboardInterrupt("Annulé par l'utilisateur.")
            if not content and not allow_empty:
                await dm.send("Réponse vide. Réponds à la question (ou tape `annule`).")
                continue
            return content

    # --------- Commands ---------

    @commands.command(name="annonce-model", aliases=["annoncemodel"])
    @staff_only()
    async def annonce_model(self, ctx: commands.Context, *, model: str | None = None):
        candidate = (model or "").strip()
        if not candidate:
            await _safe_reply(ctx, "Précise un modèle, ex: `!annonce-model gpt-5-mini`.")
            return
        resolved = normalise_staff_model(candidate)
        if not resolved:
            await _safe_reply(ctx, "Modèle non reconnu. Ex: `gpt-5-mini`.")
            return
        self.model = resolved
        await _safe_reply(
            ctx,
            f"Modèle Annonce (runtime) : `{self.model}`.\n"
            "Pour le rendre permanent: définis `OPENAI_STAFF_MODEL` sur Render.",
        )

    @commands.command(name="annonce-config", aliases=["annoncecfg", "annonce-configure"])
    @staff_only()
    async def annonce_config(self, ctx: commands.Context):
        await _safe_reply(
            ctx,
            "Configuration annonce:\n"
            f"- model: `{self.model}`\n"
            f"- channel env name: `{os.getenv('ANNONCE_CHANNEL_NAME', DEFAULT_ANNOUNCE_NAME)}`\n"
            f"- channel env id: `{os.getenv('ANNONCE_CHANNEL_ID', '')}`\n"
            f"- dm_timeout: `{DM_TIMEOUT}s`\n"
            f"- max_output_tokens: `{MAX_OUTPUT_TOKENS}`\n"
            f"- temperature: `{ANNONCE_TEMPERATURE}`\n"
            f"- default_mentions: `{ANNONCE_DEFAULT_MENTIONS or 'none'}`\n"
            f"- require_confirm: `{ANNONCE_REQUIRE_CONFIRM}`\n"
            f"- max_iterations: `{ANNONCE_MAX_ITERATIONS}`\n"
            f"- delete_trigger: `{ANNONCE_DELETE_TRIGGER}`"
        )

    @commands.command(name="annonce", aliases=["annoncestaff", "*annonce", "annonces"])
    @staff_only()
    async def annonce_cmd(self, ctx: commands.Context, *, quick: str | None = None):
        # sécurité: serveur only
        if ctx.guild is None:
            await _safe_reply(ctx, "❌ Utilise cette commande sur le serveur (pas en DM).")
            return

        # client openai
        if not self.client or not os.environ.get("OPENAI_API_KEY"):
            await _safe_reply(
                ctx,
                "❌ `OPENAI_API_KEY` n'est pas configurée sur Render. Ajoute la variable puis redeploie.",
            )
            return

        # Optionnel: supprimer le message trigger
        if ANNONCE_DELETE_TRIGGER and hasattr(ctx, "message") and ctx.message:
            try:
                perms = ctx.channel.permissions_for(ctx.guild.me) if ctx.guild.me else None
                if perms and perms.manage_messages:
                    await ctx.message.delete()
            except Exception:
                pass

        lock = self._get_lock(ctx.author.id)
        if lock.locked():
            await _safe_reply(
                ctx,
                "⚠️ Une annonce est déjà en cours pour toi en DM.\n"
                "Termine-la ou tape `annule` dans le DM, puis relance `!annonce`.",
            )
            return

        async with lock:
            # ouvrir DM
            try:
                dm = await ctx.author.create_dm()
            except discord.Forbidden:
                await _safe_reply(
                    ctx,
                    "❌ Je ne peux pas t'envoyer de DM (DM fermés). "
                    "Active tes DM pour ce serveur, puis relance `!annonce`.",
                )
                return
            except Exception as exc:
                await _safe_reply(ctx, f"❌ Impossible d'ouvrir un DM: {exc}")
                return

            await _safe_reply(ctx, "📨 Je t'ai envoyé un DM pour préparer ton annonce.")

            session = AnnounceSession(
                author_id=ctx.author.id,
                guild_id=ctx.guild.id,
                started_at_iso=_now_iso(),
                answers=[],
            )

            try:
                intro = (
                    "Salut. On va préparer une annonce.\n"
                    "Réponds aux questions. Tu peux taper `annule` à tout moment.\n"
                )
                if quick and quick.strip():
                    intro += "\nMode rapide détecté: j'utiliserai ton texte comme base."
                await dm.send(intro)

                # collecte réponses
                if quick and quick.strip():
                    # On remplit la 1ère question avec quick, et on pose les autres
                    session.answers.append(quick.strip())
                    for question in QUESTIONS[1:]:
                        ans = await self._dm_ask(dm, ctx.author, question)
                        session.answers.append(ans)
                else:
                    for question in QUESTIONS:
                        ans = await self._dm_ask(dm, ctx.author, question)
                        session.answers.append(ans)

                # channel override (optionnel)
                session.channel_override = await self._dm_ask(
                    dm,
                    ctx.author,
                    "Optionnel: dans quel canal publier ? (ex: `#annonces` ou `annonces` ou ID). "
                    "Laisse vide pour le canal par défaut.",
                    allow_empty=True,
                )

                # mentions (optionnel)
                session.mentions_raw = await self._dm_ask(
                    dm,
                    ctx.author,
                    "Optionnel: quelles mentions au début ? "
                    "Ex: `@everyone`, `@here`, `@UnRole`, `<@&ID>`. "
                    "Laisse vide pour la valeur par défaut. Écris `aucune` pour ne rien ping.",
                    allow_empty=True,
                )

            except TimeoutError:
                await dm.send("⏰ Temps écoulé, opération annulée.")
                return
            except KeyboardInterrupt:
                await dm.send("🚫 Annonce annulée.")
                return
            except Exception as exc:
                log.error("Annonce DM flow error: %s", exc, exc_info=True)
                await dm.send(f"❌ Erreur pendant la préparation: {exc}")
                return

            # génération + boucle preview/regenerate/modifier
            author_member = ctx.author  # discord.Member
            guild = ctx.guild

            mentions = parse_mentions(session.mentions_raw, guild)
            mention_prefix = " ".join(mentions).strip()
            allowed_mentions = build_allowed_mentions(mentions, guild)

            channel = self._find_announcement_channel(guild, override=session.channel_override)
            if not channel:
                await dm.send(
                    "❌ Canal d'annonces introuvable.\n"
                    "Vérifie `ANNONCE_CHANNEL_NAME` / `ANNONCE_CHANNEL_ID` sur Render, "
                    "ou précise un canal dans la question."
                )
                return

            me = guild.me or guild.get_member(self.bot.user.id) if self.bot.user else None
            if me:
                perms = channel.permissions_for(me)
                if not perms.send_messages:
                    await dm.send(f"❌ Je n'ai pas la permission d'écrire dans #{channel.name}.")
                    return

            # prompts
            system_prompt = self._system_prompt()
            base_user_prompt = self._build_user_prompt(session.answers, author_member)

            # itérations max (regen/modif)
            current_text: str = ""
            iteration = 0

            while iteration < ANNONCE_MAX_ITERATIONS:
                iteration += 1

                user_prompt = base_user_prompt
                if current_text:
                    # Si on arrive ici après une modif, on a déjà current_text.
                    pass

                try:
                    generated = await self._call_openai(system_prompt=system_prompt, user_prompt=user_prompt)
                except Exception as exc:
                    await dm.send(f"❌ Erreur génération OpenAI: {exc}")
                    return

                generated = _strip_leading_everyone_here(generated)
                generated = _neutralize_ai_mentions(generated).strip()
                if not generated:
                    await dm.send("❌ L'IA n'a rien généré. Réessaie `!annonce`.")
                    return

                current_text = generated

                preview = current_text
                if mention_prefix:
                    preview = f"{mention_prefix}\n{preview}"

                await self._dm_send_long(dm, "Aperçu de l'annonce (proposition):")
                await self._dm_send_long(dm, preview)

                if not ANNONCE_REQUIRE_CONFIRM:
                    break

                await dm.send(
                    "Réponds avec:\n"
                    "- `publier` : publier dans le canal\n"
                    "- `regen` : regénérer une autre version\n"
                    "- `modifier` : demander une modification\n"
                    "- `annule` : abandonner"
                )

                try:
                    choice = await self._dm_ask(dm, ctx.author, "Ton choix ?", allow_empty=False)
                except TimeoutError:
                    await dm.send("⏰ Temps écoulé, opération annulée.")
                    return
                except KeyboardInterrupt:
                    await dm.send("🚫 Annonce annulée.")
                    return

                c = choice.strip().lower()

                if c in {"publier", "publish", "ok", "go"}:
                    break

                if c in {"regen", "regenerer", "regénérer", "retry"}:
                    # Variation: on force une version différente
                    base_user_prompt = (
                        base_user_prompt
                        + "\n\nIMPORTANT: Regénère une version DIFFERENTE de la précédente "
                        "en changeant l'accroche et la structure tout en gardant les mêmes informations."
                    )
                    continue

                if c in {"modifier", "modif", "edit"}:
                    try:
                        mod = await self._dm_ask(
                            dm,
                            ctx.author,
                            "Décris précisément les modifications à appliquer (ajouts, ton, structure, etc.).",
                            allow_empty=False,
                        )
                    except TimeoutError:
                        await dm.send("⏰ Temps écoulé, opération annulée.")
                        return
                    except KeyboardInterrupt:
                        await dm.send("🚫 Annonce annulée.")
                        return

                    # On demande une réécriture sur la base de l'annonce actuelle
                    base_user_prompt = (
                        base_user_prompt
                        + "\n\nAnnonce actuelle:\n"
                        + current_text
                        + "\n\nModifications demandées:\n"
                        + mod.strip()
                        + "\n\nRéécris l'annonce complète en appliquant ces modifications."
                    )
                    continue

                await dm.send("Choix non reconnu. Tape `publier`, `regen`, `modifier` ou `annule`.")

            # Publication
            final_body = current_text.strip()
            if mention_prefix:
                final_full = f"{mention_prefix}\n{final_body}"
            else:
                final_full = final_body

            chunks = split_message_for_discord(final_full, limit=2000)

            # Important: éviter ping multiple -> mettre mentions seulement sur le premier chunk
            if mention_prefix and len(chunks) > 1:
                # On reconstruit en garantissant que seul le 1er chunk contient les mentions
                body_only = final_body
                body_chunks = split_message_for_discord(body_only, limit=2000)
                chunks = [f"{mention_prefix}\n{body_chunks[0]}"] + body_chunks[1:]

            try:
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await channel.send(chunk, allowed_mentions=allowed_mentions)
                    else:
                        # allowed_mentions none par prudence sur les chunks suivants
                        await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
            except Exception as exc:
                await dm.send(f"❌ Erreur en publiant dans #{channel.name}: {exc}")
                return

            await dm.send(f"✅ Annonce publiée dans #{channel.name}.")


    # --------- Errors ---------

    @annonce_cmd.error
    async def annonce_cmd_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CheckFailure):
            await _safe_reply(ctx, f"❌ {error}")
            return
        log.error("Erreur annonce_cmd: %s", error, exc_info=True)
        await _safe_reply(ctx, "❌ Erreur interne sur la commande `!annonce` (voir logs).")

    @annonce_model.error
    async def annonce_model_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CheckFailure):
            await _safe_reply(ctx, f"❌ {error}")
            return
        log.error("Erreur annonce_model: %s", error, exc_info=True)
        await _safe_reply(ctx, "❌ Erreur interne sur `!annonce-model` (voir logs).")

    @annonce_config.error
    async def annonce_config_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CheckFailure):
            await _safe_reply(ctx, f"❌ {error}")
            return
        log.error("Erreur annonce_config: %s", error, exc_info=True)
        await _safe_reply(ctx, "❌ Erreur interne sur `!annonce-config` (voir logs).")


async def setup(bot: commands.Bot):
    # Remplacement propre si une autre extension avait déjà enregistré ces commandes
    for cmd_name in ("annonce", "annonce-model", "annonce-config"):
        try:
            bot.remove_command(cmd_name)
        except Exception:
            pass
    await bot.add_cog(AnnonceCog(bot))
