#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
import secrets
import logging
import asyncio
from datetime import datetime, time as datetime_time
import discord
from discord.ext import commands, tasks
from utils.openai_config import (
    build_async_openai_client,
    resolve_openai_model,
    resolve_staff_model,
    resolve_reasoning_effort,
)

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

log = logging.getLogger("iastaff")

STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")
# Par défaut spécifique à IA Staff : GPT‑5 mini
DEFAULT_MODEL = resolve_staff_model(default="gpt-5-mini")

CONTEXT_MESSAGES = int(os.getenv("IASTAFF_CHANNEL_CONTEXT", "40"))
PER_MSG_TRUNC = int(os.getenv("IASTAFF_PER_MSG_CHARS", "200"))
CONTEXT_MAX_CHARS = int(os.getenv("IASTAFF_CONTEXT_MAX_CHARS", "6000"))
HISTORY_TURNS = int(os.getenv("IASTAFF_HISTORY_TURNS", "8"))

EMBED_SAFE_CHUNK = 3800
OPENAI_TIMEOUT = float(os.getenv("IASTAFF_TIMEOUT", "120"))
MAX_OUTPUT_TOKENS = int(os.getenv("IASTAFF_MAX_OUTPUT_TOKENS", "1800"))
INPUT_MAX_CHARS = int(os.getenv("IASTAFF_INPUT_MAX_CHARS", "12000"))


def _parse_float_env(var_name: str, default: float) -> float:
    raw = (os.getenv(var_name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


IASTAFF_TEMPERATURE = max(0.0, min(2.0, _parse_float_env("IASTAFF_TEMPERATURE", 0.4)))
IASTAFF_SAFE_MENTIONS = os.getenv("IASTAFF_SAFE_MENTIONS", "1") != "0"
IASTAFF_RULES_MODE = (os.getenv("IASTAFF_RULES_MODE") or "always").strip().lower()
IASTAFF_CONFIRM_DESTRUCTIVE = (os.getenv("IASTAFF_CONFIRM_DESTRUCTIVE") or "0").strip().lower() not in {"0", "false", "off"}


def _parse_ttl_env(var_name: str, default: int) -> int:
    raw = (os.getenv(var_name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return value if value > 0 else default


IASTAFF_CONFIRM_TTL = _parse_ttl_env("IASTAFF_CONFIRM_TTL", 300)
SENSITIVE_TOOL_NAMES = {
    "clear_console",
    "reset_warnings",
    "delete_member_record",
    "stats_reset",
    "stats_disable",
    "revoke_role",
    "run_bot_command",
}


def _sanitize_discord_mentions(text: str) -> str:
    if not text or not IASTAFF_SAFE_MENTIONS:
        return text
    text = re.sub(r"@(?=everyone\b|here\b)", "@\u200b", text, flags=re.IGNORECASE)
    text = text.replace("<@", "<@\u200b")
    return text


def _allowed_mentions():
    return discord.AllowedMentions.none() if IASTAFF_SAFE_MENTIONS else None

ENABLE_WEB_SEARCH = os.getenv("IASTAFF_ENABLE_WEB", "1") != "0"
VECTOR_STORE_ID = os.getenv("IASTAFF_VECTOR_STORE_ID", "").strip()

LOGO_FILENAME = os.getenv("IASTAFF_LOGO", "iastaff.png")
DEFAULT_GENERAL_CHANNEL_NAME = "📑𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥📑"

FRENCH_WEEKDAYS = [
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
]

FRENCH_MONTHS = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]

DEFAULT_MORNING_SYSTEM_PROMPT = (
    "Tu es EvoBot, le messager officiel de la guilde Evolution sur Discord. "
    "Chaque matin, tu écris en français un court message chaleureux et motivant, sans ping ni hashtag."
)

DEFAULT_MORNING_USER_PROMPT = (
    "Nous sommes {long_date} ({iso_date}). "
    "Rédige un unique message de bonjour pour le canal {channel_name} de la guilde Evolution. "
    "Mentionne un détail contextuel sur la journée (météo imaginaire, énergie, objectif ludique, inspiration), "
    "mets en avant l'esprit d'équipe puis termine par un encouragement adapté au jour ({weekday_cap}). "
    "Ne dépasse pas trois phrases et n'utilise pas exactement la même tournure que le message précédent : {previous_message}."
)

def _normalize_channel_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())

def _parse_id_list(value: str) -> list[int]:
    ids: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except Exception:
            continue
    return ids

def _parse_hour(value: str, default: int) -> int:
    try:
        hour = int(value)
    except Exception:
        return default
    if 0 <= hour <= 23:
        return hour
    return default

def _parse_minute(value: str, default: int) -> int:
    try:
        minute = int(value)
    except Exception:
        return default
    if 0 <= minute <= 59:
        return minute
    return default

def _resolve_zone(name: str):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None

MORNING_TZ_NAME = os.getenv("IASTAFF_MORNING_TZ", "Europe/Paris")
MORNING_TIMEZONE = _resolve_zone(MORNING_TZ_NAME) or _resolve_zone("UTC")
MORNING_HOUR = _parse_hour(os.getenv("IASTAFF_MORNING_HOUR", "10"), 10)
MORNING_MINUTE = _parse_minute(os.getenv("IASTAFF_MORNING_MINUTE", "0"), 0)
if MORNING_TIMEZONE is not None:
    MORNING_TRIGGER_TIME = datetime_time(hour=MORNING_HOUR, minute=MORNING_MINUTE, tzinfo=MORNING_TIMEZONE)
else:
    MORNING_TRIGGER_TIME = datetime_time(hour=MORNING_HOUR, minute=MORNING_MINUTE)

GUILD_RULES = """Synthese Officielle du Reglement - Guilde Evolution

1. Valeurs & Principes Generaux
Convivialite et partage : ambiance chaleureuse, entraide (conseils, farm, donjons, metiers).
Progression collective : chacun contribue selon ses moyens.
Communication transparente : annonces sur Discord, ouvre un ticket (!ticket) en cas de souci.

2. Regles de Comportement
Respect absolu : aucun harcelement/insulte/discrimination, sanctions possibles.
Politesse et bienveillance : dire bonjour, rester courtois, eviter les reactions a chaud.
Gestion des conflits : privilegier prive ou mediation staff, pas de dispute publique.

3. Discord & Salons
Discord obligatoire pour suivre la vie de guilde.
Salons cles : #general (discussions), #annonces (officiel), #organisation (sorties), !ticket (prive staff), question-au-staff ou !avis (anonyme).

4. Participation & Vie de Guilde
Passage regulier recommande. Activites annoncees sur Discord. Chacun peut proposer via !activite creer.
Entraide encouragee (donjons, metiers, farm, builds).

5. Percepteurs & Ressources (charte)
Tableau des zones fait foi. Deco interdite sur zones 2 h / 4 h ; toleree sur 6 h+ (prevenir).
Zones non listees : 48 h max + 48 h de cooldown avant de reposer.
Conditions : droit de pose a partir de 4 000 000 XP guilde + periode d'essai validee ; 1 perco par personne avec perso de la guilde.
Cooldowns : 1 h entre recolte et nouvelle pose (sauf perco tue en defense, mais pas de repose immediate meme zone).
Perco full depuis >= 2 h : recolte pour banque Evolution + avertissement.
Abus interdits : poser puis se deconnecter longtemps, partager une zone, ignorer cooldowns, monopoliser.
Defense : toute la guilde est invitee a defendre.
Echelle avertissements perco : 1 rappel, 2 perte 1 sem, 3 perte 2 sem, 4 perte jusqu'a nouvel ordre.

6. Contribution d'XP
Taux libre 1 % a 90 %. 0 % interdit sauf demande via !ticket (rush 200, cas particuliers).

7. Recrutement & Nouveaux Membres
Reserve staff/veterans. Discord obligatoire. Periode d'essai possible 2-3 jours. Adhesion aux valeurs requise.

8. Organisation Interne
Staff (roles fusionnes) gere recrutement, moderation, organisation, cohesion. Meneurs : Thalata et Coca-Cola. Decisions importantes avec le Staff. Discord/bot geres par Coca-Cola.

9. Sanctions & Discipline
Rappel bienveillant -> sanctions progressives selon gravite (avertissements, retrait de droits, exclusions). Urgence (haine/harcelement) : sanction immediate avec explications.

10. Multi-guilde
Joueurs : autorise si aucun tort a Evolution ; conflit (defense perco) regle par discussion.
Staff : doit rester fidele a Evolution et pas actif dans une guilde concurrente.

11. Evenements, Sondages & Activites
Sondages reguliers, avis anonymes via !avis. Activites sur #organisation. Concours/recompenses/loteries (Clody). Interdit : multi-compte, achat/vente de compte, achat de kamas, violations CGU Ankama.

12. Charte (resume obligatoire)
En rejoignant Evolution, tu t'engages a : respecter le reglement ; rester respectueux et courtois ; contribuer a l'esprit d'equipe ; communiquer de facon constructive ; participer positivement et respecter le Staff ; reconnaitre les mises a jour internes. Signature par emoji check = acceptation.

Resume final (10 points)
Respect obligatoire, aucune toxicite. Discord obligatoire. Participation reguliere. Entraide et convivialite centrales.
Percepteurs strictement encadres (zones, cooldowns, abus). XP libre 1-90 %, 0 % sur demande.
Recrutement controle (essai possible). Staff + Meneurs = gestion. Sanctions progressives. Multi-guilde tolere sous conditions."""

SYSTEM_PROMPT_DEFAULT = (
    """Tu es EvolutionPRO, assistant du Staff de la guilde Évolution (Dofus Rétro 1.29).
Par défaut, réponds en FRANÇAIS, utile, concret et concis. Ta priorité est d'aider, pas de sermonner.

PRINCIPES
1) Answer-first : commence par régler le problème posé (étapes, commandes, résolution).
2) Capacité : ne promets JAMAIS d'actions que tu ne peux pas exécuter. Si une action requiert une commande, propose la commande (!profil, !ticket, !stats, !activite, !sondage, etc.) ou ping @Staff si c’est humain.
3) Règlement : NE cite le règlement que si (a) on te le demande, (b) le message contient injure/attaque/dérapage manifeste, ou (c) il y a un risque réel (arnaque, dox, propos haineux). Dans ce cas, rappelle la règle brièvement et reviens immédiatement à la solution concrète.
4) Transparence : si tu ne sais pas, dis-le et propose une voie de contournement.
5) Style : ton sobre, cordial, jamais passif-agressif. Pas d’emoji excessifs. Pas de “le règlement dit…” en ouverture.
6) Limites : pas d’infos inventées. Pas de chiffres fantaisistes. Si une donnée est variable, annonce l’incertitude.
7) Format : pour une réponse technique, fais des listes courtes et des étapes actionnables. Code = bloc complet prêt à l’emploi.

RÔLE
— Assistant Staff : modération légère, organisation (événements, percos, annonces), aide aux commandes, synthèse de discussions, conseils pratiques.
— Tu n’as pas d’accès hors des commandes du bot et des salons.
— Si l’utilisateur est agressif, recadre en 1 phrase maximum puis recentre immédiatement sur la demande.

EXEMPLES DE COMPORTEMENT
- Demande : “Le bot spam #console.” → Réponse : diagnostic rapide + 3 actions concrètes + commande pour ouvrir un ticket si besoin.
- Demande : “Fais un event donjon.” → Réponse : propose la commande adaptée, puis, si autorisé, créer la base via la commande ; sinon explique comment le staff le fera.

NE PAS FAIRE
- Ne jamais réciter le règlement en ouverture.
- Ne pas proposer un ban/kick si la situation n’en a pas besoin.
- Ne pas mentir sur les capacités techniques.

Tu es un assistant de confiance, pas un gendarme."""
)

MODERATION_PROMPT = (
    "Modération minimale et contextuelle : "
    "Rappelle brièvement une règle UNIQUEMENT si le message contient injure/propos haineux/harcèlement ou si on te le demande. "
    "Toujours recentrer la discussion sur la solution concrète en 1–2 phrases. "
    "Propose !ticket pour traiter à froid si le ton monte."
)

def chunk_text(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            if buf:
                parts.append(buf)
            while len(line) > limit:
                parts.append(line[:limit])
                line = line[limit:]
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf:
        parts.append(buf)
    return parts or [""]

def _to_dict(obj) -> dict:
    for attr in ("model_dump", "to_dict"):
        try:
            fn = getattr(obj, attr, None)
            if callable(fn):
                d = fn()
                if isinstance(d, dict):
                    return d
        except Exception:
            pass
    try:
        d = obj.__dict__
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}

def _gather_text_nodes(node) -> list[str]:
    out = []
    if isinstance(node, str):
        if node.strip():
            out.append(node.strip())
    elif isinstance(node, dict):
        if "output_text" in node and isinstance(node["output_text"], str) and node["output_text"].strip():
            out.append(node["output_text"].strip())
        if "text" in node:
            v = node["text"]
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
            elif isinstance(v, dict):
                vv = v.get("value")
                if isinstance(vv, str) and vv.strip():
                    out.append(vv.strip())
        if "content" in node and isinstance(node["content"], list):
            for c in node["content"]:
                out += _gather_text_nodes(c)
        if "message" in node and isinstance(node["message"], dict):
            out += _gather_text_nodes(node["message"])
        for k, v in node.items():
            if k in ("response", "output", "outputs", "choices", "delta", "result", "data"):
                out += _gather_text_nodes(v)
    elif isinstance(node, list):
        for it in node:
            out += _gather_text_nodes(it)
    return out

def extract_generated_text(resp_obj) -> str:
    try:
        t = getattr(resp_obj, "output_text", None)
        if isinstance(t, str) and t.strip():
            return t.strip()
    except Exception:
        pass
    data = _to_dict(resp_obj)
    texts = _gather_text_nodes(data)
    if not texts and isinstance(data.get("choices"), list):
        for ch in data["choices"]:
            m = ch.get("message") if isinstance(ch, dict) else None
            if isinstance(m, dict):
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    texts.append(c.strip())
    joined = "\n".join([s for s in texts if s.strip()])
    return joined.strip()

def _content_to_string(content_parts: list[dict]) -> str:
    if not isinstance(content_parts, list):
        return ""
    buf = []
    for p in content_parts:
        if not isinstance(p, dict):
            continue
        if "text" in p and isinstance(p["text"], str):
            buf.append(p["text"])
        elif "text" in p and isinstance(p["text"], dict):
            v = p["text"].get("value")
            if isinstance(v, str):
                buf.append(v)
    return "\n".join(buf).strip()

class IAStaff(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client: AsyncOpenAI | None = None
        self._load_error: str | None = None
        self._ensure_client()
        # Normalisé via utils.openai_config (ENV > défaut "gpt-5-mini" spécifique IA Staff)
        self.model = resolve_staff_model(default=DEFAULT_MODEL)
        self.system_prompt = os.getenv("IASTAFF_SYSTEM_PROMPT", SYSTEM_PROMPT_DEFAULT)
        self.history: dict[int, list[dict[str, str]]] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.channel_ctx_cache: dict[int, tuple[int | None, str]] = {}
        self.logo_path = os.path.join(os.path.dirname(__file__), LOGO_FILENAME)
        self.has_logo = os.path.exists(self.logo_path)
        raw_ids = os.getenv("IASTAFF_MORNING_CHANNEL_IDS", "")
        self.morning_channel_ids = _parse_id_list(raw_ids)
        raw_names = os.getenv(
            "IASTAFF_MORNING_CHANNEL_NAMES",
            f"{DEFAULT_GENERAL_CHANNEL_NAME},📄 Général 📄,général,general",
        )
        self.morning_channel_names = [name.strip() for name in raw_names.split(",") if name.strip()]
        self._morning_name_tokens = {
            token
            for token in (_normalize_channel_name(name) for name in self.morning_channel_names)
            if token
        }
        self.primary_morning_channel_name = self.morning_channel_names[0] if self.morning_channel_names else DEFAULT_GENERAL_CHANNEL_NAME
        self.morning_message = os.getenv(
            "IASTAFF_MORNING_MESSAGE",
            "Bonjour à tous les membres de la guilde Evolution ! Passez une excellente journée ☀️",
        ).strip()
        if not self.morning_message:
            self.morning_message = "Bonjour à tous les membres de la guilde Evolution ! Passez une excellente journée ☀️"
        self.morning_model = resolve_openai_model("IASTAFF_MORNING_MODEL", "gpt-5-mini")
        self.morning_temperature = float(os.getenv("IASTAFF_MORNING_TEMPERATURE", "0.75"))
        self.morning_max_tokens = int(os.getenv("IASTAFF_MORNING_MAX_TOKENS", "450"))
        self.morning_system_prompt = os.getenv(
            "IASTAFF_MORNING_SYSTEM_PROMPT", DEFAULT_MORNING_SYSTEM_PROMPT
        ).strip() or DEFAULT_MORNING_SYSTEM_PROMPT
        self.morning_user_prompt = os.getenv(
            "IASTAFF_MORNING_USER_PROMPT", DEFAULT_MORNING_USER_PROMPT
        ).strip() or DEFAULT_MORNING_USER_PROMPT
        self._last_morning_content: str | None = None
        self._last_morning_date: str | None = None
        flag_value = (os.getenv("IASTAFF_ENABLE_TOOLS") or "1").strip().lower()
        self.enable_tools = flag_value not in {"0", "false", "off"}
        self.pending_confirmations: dict[str, dict] = {}

    def _ensure_client(self) -> bool:
        if self.client is not None:
            return True
        if AsyncOpenAI is None:
            self._load_error = "Librairie openai manquante. Installe openai>=1.58.0."
            log.warning("IAStaff indisponible: %s", self._load_error)
            return False
        try:
            self.client = build_async_openai_client(AsyncOpenAI, timeout=OPENAI_TIMEOUT)
        except Exception as exc:
            self._load_error = f"Impossible d'initialiser openai.AsyncOpenAI: {exc}"
            log.error("IAStaff: initialisation du client OpenAI impossible: %s", exc, exc_info=True)
            self.client = None
            return False
        if self.client is None:
            if not os.environ.get("OPENAI_API_KEY"):
                self._load_error = "OPENAI_API_KEY manquante. Configure la cle puis redeploie."
                log.warning("OPENAI_API_KEY manquante: !iastaff renverra une erreur tant que la cle n'est pas definie.")
            else:
                self._load_error = "Configuration OpenAI invalide. Verifie projet et organisation."
                log.error("IAStaff: configuration OpenAI invalide (projet ou organisation).")
            return False
        self._load_error = None
        return True


    async def cog_load(self):
        if not self._ensure_client():
            if self._load_error:
                log.warning("IAStaff charge mais desactive: %s", self._load_error)
            else:
                log.warning("IAStaff charge mais le client OpenAI est indisponible.")
            return
        log.info("IAStaff prêt (model=%s | history=%d tours | web=%s | files=%s)", self.model, HISTORY_TURNS, "on" if ENABLE_WEB_SEARCH else "off", "on" if VECTOR_STORE_ID else "off")
        if not self.morning_greeting.is_running():
            self.morning_greeting.start()

    async def cog_unload(self):
        if self.morning_greeting.is_running():
            self.morning_greeting.cancel()

    def _resolve_morning_channels(self) -> list[discord.TextChannel]:
        channels: list[discord.TextChannel] = []
        seen: set[int] = set()
        for channel_id in self.morning_channel_ids:
            channel = self.bot.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel) and channel.id not in seen:
                channels.append(channel)
                seen.add(channel.id)
        if self._morning_name_tokens:
            for guild in self.bot.guilds:
                for channel in guild.text_channels:
                    if channel.id in seen:
                        continue
                    if _normalize_channel_name(channel.name) in self._morning_name_tokens:
                        channels.append(channel)
                        seen.add(channel.id)
        return channels

    def _current_morning_datetime(self) -> datetime:
        if MORNING_TIMEZONE is not None:
            return datetime.now(tz=MORNING_TIMEZONE)
        return datetime.now()

    def _render_morning_prompt(self, now: datetime) -> str:
        weekday = FRENCH_WEEKDAYS[now.weekday()]
        month = FRENCH_MONTHS[now.month - 1]
        long_date = f"{weekday.capitalize()} {now.day:02d} {month} {now.year}"
        iso_date = now.date().isoformat()
        return self.morning_user_prompt.format(
            long_date=long_date,
            weekday=weekday,
            weekday_cap=weekday.capitalize(),
            day=f"{now.day:02d}",
            month=month,
            month_cap=month.capitalize(),
            year=str(now.year),
            iso_date=iso_date,
            previous_message=self._last_morning_content or "",
            channel_name=self.primary_morning_channel_name,
        )

    async def _generate_morning_message(self) -> str:
        if not self._ensure_client():
            return ""
        now = self._current_morning_datetime()
        today_key = now.date().isoformat()
        prompt = self._render_morning_prompt(now).strip()
        if not prompt:
            return ""
        messages = [
            {"role": "system", "content": [{"type": "input_text", "text": self.morning_system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
        ]
        req = {
            "model": self.morning_model,
            "input": messages,
            "max_output_tokens": self.morning_max_tokens,
            "store": False,
            "temperature": self.morning_temperature,
        }
        self._maybe_attach_reasoning(req, model=self.morning_model)
        for attempt in range(3):
            try:
                resp = await self.client.responses.create(**req)
            except Exception as exc:
                log.warning("IAStaff: génération du message du matin échouée (tentative %d): %s", attempt + 1, exc)
                await asyncio.sleep(2)
                continue
            text = extract_generated_text(resp).strip()
            if not text:
                continue
            if self._last_morning_content and text == self._last_morning_content and attempt < 2:
                req["temperature"] = min(1.2, req.get("temperature", self.morning_temperature) + 0.1)
                continue
            self._last_morning_content = text
            self._last_morning_date = today_key
            return text
        return ""

    @tasks.loop(time=MORNING_TRIGGER_TIME)
    async def morning_greeting(self):
        if not self.bot.is_ready():
            return
        message = await self._generate_morning_message()
        if not message:
            message = self.morning_message
        if not message:
            return
        for channel in self._resolve_morning_channels():
            try:
                await channel.send(message)
            except Exception as exc:
                log.warning("IAStaff morning greeting failure on %s: %s", getattr(channel, "name", channel.id), exc)

    @morning_greeting.before_loop
    async def before_morning_greeting(self):
        await self.bot.wait_until_ready()

    def _get_lock(self, channel_id: int) -> asyncio.Lock:
        lock = self.locks.get(channel_id)
        if not lock:
            lock = asyncio.Lock()
            self.locks[channel_id] = lock
        return lock

    def _push_history(self, channel_id: int, role: str, text: str):
        buf = self.history.setdefault(channel_id, [])
        buf.append({"role": role, "text": text})
        max_items = HISTORY_TURNS * 2
        if len(buf) > max_items:
            self.history[channel_id] = buf[-max_items:]

    async def _build_channel_context(
        self,
        channel: discord.abc.Messageable,
        before: discord.Message | None,
    ) -> str:
        ch = channel
        channel_id = getattr(ch, "id", None)
        last_id = getattr(ch, "last_message_id", None)
        if channel_id is None:
            return ""
        cached = self.channel_ctx_cache.get(channel_id)
        if cached and cached[0] == last_id:
            return cached[1]
        history = getattr(ch, "history", None)
        if history is None:
            return ""

        def extract_text(message: discord.Message) -> str:
            text = (message.clean_content or "").strip()
            if not text:
                embeds = getattr(message, "embeds", None) or []
                segments: list[str] = []
                for embed in embeds:
                    title = getattr(embed, "title", None)
                    description = getattr(embed, "description", None)
                    if title:
                        segments.append(str(title))
                    if description:
                        segments.append(str(description))
                text = "\n".join(segments).strip()
            if not text:
                attachments = getattr(message, "attachments", None) or []
                filenames = [
                    getattr(attachment, "filename", "")
                    for attachment in attachments
                    if getattr(attachment, "filename", "")
                ]
                if filenames:
                    text = "Pièces jointes: " + ", ".join(filenames)
            if text:
                text = " ".join(text.split())
            return text

        messages: list[discord.Message] = []
        async for item in history(limit=CONTEXT_MESSAGES, before=before, oldest_first=False):
            messages.append(item)

        gathered_chars = 0
        collected: list[str] = []
        truncated = False
        for message in messages:
            text = extract_text(message)
            if not text:
                continue
            if len(text) > PER_MSG_TRUNC:
                text = text[:PER_MSG_TRUNC] + "…"
            author = (
                getattr(getattr(message, "author", None), "display_name", None)
                or getattr(getattr(message, "author", None), "name", None)
                or "?"
            )
            line = f"- {author} : {text}"
            if gathered_chars + len(line) + 1 > CONTEXT_MAX_CHARS:
                truncated = True
                break
            collected.append(line)
            gathered_chars += len(line) + 1

        collected.reverse()
        lines = ["Contexte du canal (messages récents, max 40) :"]
        if truncated:
            lines.append("… (messages plus anciens omis)")
        lines.extend(collected)
        block = "\n".join(lines).strip()
        self.channel_ctx_cache[channel_id] = (last_id, block)
        return block

    def _make_messages(self, channel_ctx: str, channel_id: int, user_msg: str) -> list[dict]:
        history_items = list(self.history.get(channel_id, []))

        rules_mode = (IASTAFF_RULES_MODE or "always").strip().lower()
        include_rules = True
        if rules_mode == "never":
            include_rules = False
        elif rules_mode == "auto":
            blob = f"{user_msg}\n{channel_ctx}".lower()
            keywords = (
                "règlement",
                "reglement",
                "perco",
                "percepteur",
                "avert",
                "warn",
                "ban",
                "kick",
                "mute",
                "insult",
                "harcel",
                "dox",
                "arnaque",
                "racis",
                "discri",
                "haine",
                "spam",
            )
            include_rules = any(keyword in blob for keyword in keywords)

        def build(ctx_text: str, hist_items: list[dict]) -> list[dict]:
            messages: list[dict] = []
            messages.append({"role": "system", "content": [{"type": "input_text", "text": self.system_prompt}]})
            messages.append({"role": "developer", "content": [{"type": "input_text", "text": MODERATION_PROMPT}]})
            if include_rules:
                messages.append({"role": "developer", "content": [{"type": "input_text", "text": GUILD_RULES}]})
            if ctx_text:
                messages.append({"role": "developer", "content": [{"type": "input_text", "text": ctx_text}]})
            for item in hist_items:
                role = item.get("role")
                text = item.get("text") or ""
                if not isinstance(text, str) or not text.strip():
                    continue
                if role == "user":
                    messages.append({"role": "user", "content": [{"type": "input_text", "text": text}]})
                else:
                    messages.append({"role": "assistant", "content": [{"type": "output_text", "text": text}]})
            messages.append({"role": "user", "content": [{"type": "input_text", "text": user_msg}]})
            return messages

        def approx_chars(payload: list[dict]) -> int:
            total = 0
            for message in payload:
                for content in message.get("content", []):
                    text = content.get("text")
                    if isinstance(text, str):
                        total += len(text)
            return total

        def trim_channel_ctx(ctx_text: str, overflow: int) -> str:
            if not ctx_text or overflow <= 0:
                return ctx_text
            header, separator, body = ctx_text.partition("\n")
            if not separator:
                header = ""
                body = ctx_text
            cut = min(len(body), overflow + 500)
            body = body[cut:].lstrip()
            if not body:
                return ""
            if header:
                return f"{header}\n…\n{body}"
            return f"…\n{body}"

        messages = build(channel_ctx, history_items)
        approx = approx_chars(messages)
        if approx > INPUT_MAX_CHARS:
            overflow = approx - INPUT_MAX_CHARS
            if channel_ctx:
                channel_ctx = trim_channel_ctx(channel_ctx, overflow)
                messages = build(channel_ctx, history_items)
                approx = approx_chars(messages)
            while approx > INPUT_MAX_CHARS and history_items:
                history_items = history_items[2:] if len(history_items) >= 2 else history_items[1:]
                messages = build(channel_ctx, history_items)
                approx = approx_chars(messages)
            if approx > INPUT_MAX_CHARS and channel_ctx:
                channel_ctx = ""
                messages = build(channel_ctx, history_items)
        return messages

    def _build_request(self, messages: list[dict]) -> dict:
        base = {
            "model": self.model,
            "input": messages,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
            "temperature": IASTAFF_TEMPERATURE,
        }
        tools = []
        if ENABLE_WEB_SEARCH:
            tools.append({"type": "web_search"})
        if VECTOR_STORE_ID:
            tools.append({"type": "file_search"})
            base["tool_resources"] = {"file_search": {"vector_store_ids": [VECTOR_STORE_ID]}}
        if tools:
            base["tools"] = tools
        self._maybe_attach_reasoning(base)
        return base

    def _maybe_attach_reasoning(self, request: dict, *, model: str | None = None):
        target = model or request.get("model") or self.model
        if not target:
            return
        options = resolve_reasoning_effort(target)
        if options:
            request["reasoning"] = options

    def _command_tools(self) -> list[dict]:
        """Describe the Discord commands that can be invoked via OpenAI tool-calling."""
        if not self.enable_tools:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_activity",
                    "description": "Crée une activité via `!activite creer <titre> <JJ/MM/AAAA HH:MM> <description>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["title", "datetime"],
                        "properties": {
                            "title": {"type": "string"},
                            "datetime": {"type": "string", "description": "Date au format JJ/MM/AAAA HH:MM"},
                            "description": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_activities",
                    "description": "Affiche la liste des activités via `!activite liste`.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "join_activity",
                    "description": "Inscrit un membre à une activité via `!activite join <id>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "cancel_activity",
                    "description": "Annule une activité via `!activite annuler <id>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "leave_activity",
                    "description": "Se retire d'une activité via `!activite leave <id>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "activity_info",
                    "description": "Affiche les détails d'une activité via `!activite info <id>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "modify_activity",
                    "description": "Met à jour une activité via `!activite modifier <id> <JJ/MM/AAAA HH:MM> <description>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["id", "datetime"],
                        "properties": {
                            "id": {"type": "string"},
                            "datetime": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "open_ticket",
                    "description": "Ouvre un ticket via `!ticket`.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "start_organisation",
                    "description": "Lance l'assistant `!organisation`.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "start_announce",
                    "description": "Démarre `!annonce` pour préparer une publication.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "start_event",
                    "description": "Prépare les informations (titre/date/description) puis lance `!event`.",
                    "parameters": {
                        "type": "object",
                        "required": ["title", "date_time", "description"],
                        "properties": {
                            "title": {"type": "string"},
                            "date_time": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "clear_console",
                    "description": "Nettoie le salon console via `!clear console`.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "channel": {
                                "type": "string",
                                "description": "Nom du salon à nettoyer (par défaut: console).",
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "show_warnings",
                    "description": "Consulte les avertissements d'un membre via `!warnings @membre`.",
                    "parameters": {
                        "type": "object",
                        "required": ["member"],
                        "properties": {
                            "member": {
                                "type": "string",
                                "description": "Mention, ID ou pseudo Discord du membre.",
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "reset_warnings",
                    "description": "Réinitialise les avertissements d'un membre via `!resetwarnings @membre`.",
                    "parameters": {
                        "type": "object",
                        "required": ["member"],
                        "properties": {
                            "member": {
                                "type": "string",
                                "description": "Mention, ID ou pseudo Discord du membre.",
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_recruitment_entry",
                    "description": "Ajoute un joueur via `!recrutement <Pseudo>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["pseudo"],
                        "properties": {"pseudo": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_member_record",
                    "description": "Supprime une fiche via `!membre del <pseudo>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["pseudo"],
                        "properties": {"pseudo": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "stats_reset",
                    "description": "Réinitialise les stats via `!stats reset`.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "stats_enable",
                    "description": "Active la collecte via `!stats on`.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "stats_disable",
                    "description": "Désactive la collecte via `!stats off`.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "job_list_all",
                    "description": "Affiche tous les métiers enregistrés via `!job liste`.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "job_list_professions",
                    "description": "Affiche le catalogue des noms de métiers via `!job liste metier`.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "job_lookup_player",
                    "description": "Montre les métiers d'un joueur via `!job <pseudo|mention>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["player"],
                        "properties": {"player": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "job_lookup_profession",
                    "description": "Liste les joueurs par métier via `!job <nom_metier>`.",
                    "parameters": {
                        "type": "object",
                        "required": ["profession"],
                        "properties": {"profession": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_member_job",
                    "description": "Ajoute ou met à jour un métier pour un joueur.",
                    "parameters": {
                        "type": "object",
                        "required": ["member", "job", "level"],
                        "properties": {
                            "member": {"type": "string", "description": "Mention, ID ou pseudo Discord."},
                            "job": {"type": "string"},
                            "level": {"type": ["integer", "string"], "description": "Niveau 1-200."},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "remove_member_job",
                    "description": "Supprime un métier pour un joueur.",
                    "parameters": {
                        "type": "object",
                        "required": ["member", "job"],
                        "properties": {
                            "member": {"type": "string"},
                            "job": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_member_mule",
                    "description": "Ajoute une mule à la fiche d'un joueur.",
                    "parameters": {
                        "type": "object",
                        "required": ["member", "mule"],
                        "properties": {
                            "member": {"type": "string"},
                            "mule": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "remove_member_mule",
                    "description": "Retire une mule de la fiche d'un joueur.",
                    "parameters": {
                        "type": "object",
                        "required": ["member", "mule"],
                        "properties": {
                            "member": {"type": "string"},
                            "mule": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_bot_command",
                    "description": "Exécute n'importe quelle commande du bot en fournissant les arguments nécessaires.",
                    "parameters": {
                        "type": "object",
                        "required": ["command"],
                        "properties": {
                            "command": {"type": "string", "description": "Nom complet de la commande (ex: job, stats reset)."},
                            "positional_args": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Arguments positionnels à passer tels quels.",
                                "default": [],
                            },
                            "keyword_args": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                                "description": "Arguments nommés (clé=valeur).",
                                "default": {},
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grant_role",
                    "description": "Ajoute un rôle Discord à un membre.",
                    "parameters": {
                        "type": "object",
                        "required": ["member", "role"],
                        "properties": {
                            "member": {"type": "string", "description": "Mention, ID ou pseudo Discord."},
                            "role": {"type": "string", "description": "Nom, ID ou mention du rôle."},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "revoke_role",
                    "description": "Retire un rôle Discord d'un membre.",
                    "parameters": {
                        "type": "object",
                        "required": ["member", "role"],
                        "properties": {
                            "member": {"type": "string"},
                            "role": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _resolve_member_argument(self, ctx: commands.Context, raw: str):
        if ctx.guild is None or not raw:
            return None
        value = raw.strip()
        if not value:
            return None
        if value.startswith("<@") and value.endswith(">"):
            inner = value[2:-1]
            if inner.startswith("!"):
                inner = inner[1:]
            value = inner
        member = None
        if value.isdigit():
            member = ctx.guild.get_member(int(value))
            if member is not None:
                return member
        lowered = value.lower()
        members = getattr(ctx.guild, "members", None) or []
        for item in members:
            item_id = getattr(item, "id", None)
            if item_id is not None and str(item_id) == value:
                return item
            display_name = getattr(item, "display_name", "")
            username = getattr(item, "name", "")
            if display_name.lower() == lowered or username.lower() == lowered:
                return item
        return None

    def _resolve_role(self, ctx: commands.Context, raw: str):
        guild = getattr(ctx, "guild", None)
        if guild is None or not raw:
            return None
        value = raw.strip()
        if not value:
            return None
        if value.startswith("<@&") and value.endswith(">"):
            payload = value[3:-1]
            value = payload
        if value.isdigit():
            role = guild.get_role(int(value))
            if role:
                return role
        lowered = value.lower()
        for role in guild.roles:
            if role.name.lower() == lowered:
                return role
        for role in guild.roles:
            if lowered in role.name.lower():
                return role
        return None

    def _format_command_summary(self, commands: list[str], summary: str) -> str:
        visible = [cmd for cmd in commands if cmd]
        sections: list[str] = []
        if visible:
            cmd_lines = "\n".join(f"- `{cmd}`" for cmd in visible)
            sections.append(f"Commandes exécutées :\n{cmd_lines}")
        if summary:
            sections.append(summary.strip())
        return "\n\n".join(sections).strip()

    def _get_job_cog(self):
        try:
            return self.bot.get_cog("JobCog")
        except Exception:
            return None

    def _get_players_cog(self):
        try:
            return self.bot.get_cog("PlayersCog")
        except Exception:
            return None

    async def _refresh_players_data(self, players_cog, ctx: commands.Context):
        guild = getattr(ctx, "guild", None)
        try:
            channel = await players_cog._resolve_console_channel(guild)
        except Exception:
            channel = None
        if channel is None:
            return
        try:
            await players_cog._load_data_from_console(channel)
        except Exception:
            return

    async def _summarize_job_profession(self, ctx: commands.Context, profession: str) -> str:
        job_cog = self._get_job_cog()
        guild = getattr(ctx, "guild", None)
        if job_cog is None or guild is None:
            return ""
        try:
            await job_cog.load_from_console(guild)
        except Exception:
            pass
        canonical = job_cog.resolve_job_name(profession) if hasattr(job_cog, "resolve_job_name") else None
        if not canonical:
            suggestions = []
            if hasattr(job_cog, "suggest_similar_jobs"):
                try:
                    suggestions = job_cog.suggest_similar_jobs(profession)
                except Exception:
                    suggestions = []
            if suggestions:
                joined = ", ".join(f"`{s}`" for s in suggestions[:5])
                return f"Aucun métier exact trouvé pour **{profession}**. Suggestions : {joined}"
            return f"Aucun métier correspondant à **{profession}**."
        entries: list[tuple[str, int]] = []
        for key, data in (getattr(job_cog, "jobs_data", {}) or {}).items():
            jobs = data.get("jobs") or {}
            level = jobs.get(canonical)
            if level is None:
                continue
            member = None
            if str(key).isdigit():
                member = guild.get_member(int(key))
            display_name = data.get("name") or (member.display_name if member else None) or str(key)
            entries.append((display_name, int(level)))
        if not entries:
            return f"Aucun membre n'est encore enregistré comme **{canonical}**."
        entries.sort(key=lambda item: (-item[1], item[0].lower()))
        listed = ", ".join(f"{name} (niv. {lvl})" for name, lvl in entries[:10])
        lines = [
            f"Il y a **{len(entries)}** {canonical.lower()}(s) enregistrés.",
            listed,
        ]
        if len(entries) > 10:
            lines.append(f"... et {len(entries) - 10} autre(s).")
        return "\n".join(filter(None, lines))

    async def _summarize_job_player(self, ctx: commands.Context, player_raw: str) -> str:
        job_cog = self._get_job_cog()
        guild = getattr(ctx, "guild", None)
        if job_cog is None or guild is None:
            return ""
        try:
            await job_cog.load_from_console(guild)
        except Exception:
            pass
        member = self._resolve_member_argument(ctx, player_raw)
        display_name = (member.display_name if member else (player_raw or "").strip()) or "ce joueur"
        identifier = str(member.id) if member else ""
        jobs = job_cog.get_user_jobs(identifier, display_name) if hasattr(job_cog, "get_user_jobs") else {}
        if not jobs and display_name:
            lowered = display_name.lower()
            for key, data in (getattr(job_cog, "jobs_data", {}) or {}).items():
                if data.get("name", "").lower() == lowered:
                    jobs = data.get("jobs", {})
                    if not member and str(key).isdigit():
                        guild_member = guild.get_member(int(key))
                        if guild_member:
                            display_name = guild_member.display_name
                    break
        if not jobs:
            return f"Aucune fiche métier enregistrée pour **{display_name}**."
        items = sorted(
            ((name, int(level)) for name, level in jobs.items()),
            key=lambda kv: (-kv[1], kv[0].lower()),
        )
        listed = ", ".join(f"{name} (niv. {lvl})" for name, lvl in items[:10])
        lines = [
            f"Métiers enregistrés pour **{display_name}** ({len(items)} entrées).",
            listed,
        ]
        if len(items) > 10:
            lines.append(f"... et {len(items) - 10} autre(s).")
        return "\n".join(filter(None, lines))

    def _resolve_job_target(self, job_cog, ctx: commands.Context, member_raw: str) -> tuple[str, str]:
        if not member_raw:
            raise RuntimeError("Membre requis pour gérer les métiers.")
        member = self._resolve_member_argument(ctx, member_raw)
        if member:
            return str(member.id), member.display_name
        value = member_raw.strip()
        lowered = value.lower()
        for key, data in (getattr(job_cog, "jobs_data", {}) or {}).items():
            stored = (data.get("name") or "").lower()
            if stored and stored == lowered:
                return str(key), data.get("name") or value
        for key, data in (getattr(job_cog, "jobs_data", {}) or {}).items():
            stored = (data.get("name") or "").lower()
            if stored and (stored in lowered or lowered in stored):
                return str(key), data.get("name") or value
        if value.isdigit() and value in job_cog.jobs_data:
            entry = job_cog.jobs_data[value]
            return value, entry.get("name") or value
        raise RuntimeError(f"Membre introuvable pour les métiers : {value}.")

    def _resolve_players_target(self, players_cog, ctx: commands.Context, member_raw: str) -> tuple[str, str]:
        if not member_raw:
            raise RuntimeError("Membre requis pour gérer le profil.")
        member = self._resolve_member_argument(ctx, member_raw)
        if member:
            players_cog._verifier_et_fusionner_id(str(member.id), member.display_name)
            return str(member.id), member.display_name
        value = member_raw.strip()
        lowered = value.lower()
        for key, data in (players_cog.persos_data or {}).items():
            candidates = (
                data.get("discord_name", ""),
                data.get("main", ""),
            )
            for candidate in candidates:
                if candidate and candidate.lower() == lowered:
                    return str(key), candidate
        if value in players_cog.persos_data:
            entry = players_cog.persos_data[value]
            label = entry.get("discord_name") or entry.get("main") or value
            return value, label
        raise RuntimeError(f"Profil introuvable pour {value}.")

    async def _set_member_job(self, ctx: commands.Context, member_raw: str, job_name: str, level_value: int) -> str:
        job_cog = self._get_job_cog()
        guild = getattr(ctx, "guild", None)
        if job_cog is None or guild is None:
            raise RuntimeError("Module métiers indisponible.")
        if not getattr(job_cog, "initialized", True):
            await job_cog.initialize_data()
        await job_cog.load_from_console(guild)
        target_id, display_name = self._resolve_job_target(job_cog, ctx, member_raw)
        canonical = job_cog.resolve_job_name(job_name) if hasattr(job_cog, "resolve_job_name") else None
        if canonical is None:
            canonical = job_name.strip()
        if not canonical:
            raise RuntimeError("Nom de métier invalide.")
        entry = job_cog.jobs_data.setdefault(target_id, {"name": display_name, "jobs": {}})
        entry["name"] = display_name
        entry.setdefault("jobs", {})
        entry["jobs"][canonical] = level_value
        job_cog.save_data_local()
        await job_cog.dump_data_to_console(guild)
        return self._format_command_summary(
            ["[IA] job.set"],
            f"**{display_name}** possède désormais **{canonical}** niveau **{level_value}**.",
        )

    async def _remove_member_job(self, ctx: commands.Context, member_raw: str, job_name: str) -> str:
        job_cog = self._get_job_cog()
        guild = getattr(ctx, "guild", None)
        if job_cog is None or guild is None:
            raise RuntimeError("Module métiers indisponible.")
        if not getattr(job_cog, "initialized", True):
            await job_cog.initialize_data()
        await job_cog.load_from_console(guild)
        target_id, display_name = self._resolve_job_target(job_cog, ctx, member_raw)
        entry = job_cog.jobs_data.get(target_id)
        if not entry or "jobs" not in entry:
            raise RuntimeError(f"Aucun métier enregistré pour {display_name}.")
        canonical = job_cog.resolve_job_name(job_name) if hasattr(job_cog, "resolve_job_name") else None
        if canonical is None:
            canonical = job_name.strip()
        if canonical not in entry["jobs"]:
            raise RuntimeError(f"{display_name} n'a pas {canonical}.")
        del entry["jobs"][canonical]
        job_cog.save_data_local()
        await job_cog.dump_data_to_console(guild)
        return self._format_command_summary(
            ["[IA] job.remove"],
            f"Le métier **{canonical}** a été retiré pour **{display_name}**.",
        )

    async def _add_member_mule(self, ctx: commands.Context, member_raw: str, mule_name: str) -> str:
        players_cog = self._get_players_cog()
        if players_cog is None:
            raise RuntimeError("Module membres indisponible.")
        await players_cog._ensure_initialized()
        await self._refresh_players_data(players_cog, ctx)
        target_id, display_name = self._resolve_players_target(players_cog, ctx, member_raw)
        entry = players_cog.persos_data.setdefault(
            target_id,
            {"discord_name": display_name or target_id, "main": "", "mules": []},
        )
        entry["discord_name"] = display_name or entry.get("discord_name") or target_id
        mules = entry.setdefault("mules", [])
        if any(isinstance(mule, str) and mule.lower() == mule_name.lower() for mule in mules):
            return self._format_command_summary(
                ["[IA] membre.addmule"],
                f"La mule **{mule_name}** est déjà enregistrée pour **{display_name}**.",
            )
        mules.append(mule_name)
        await players_cog.dump_data_to_console(ctx)
        return self._format_command_summary(
            ["[IA] membre.addmule"],
            f"Mule **{mule_name}** ajoutée pour **{display_name}**.",
        )

    async def _remove_member_mule(self, ctx: commands.Context, member_raw: str, mule_name: str) -> str:
        players_cog = self._get_players_cog()
        if players_cog is None:
            raise RuntimeError("Module membres indisponible.")
        await players_cog._ensure_initialized()
        await self._refresh_players_data(players_cog, ctx)
        target_id, display_name = self._resolve_players_target(players_cog, ctx, member_raw)
        entry = players_cog.persos_data.get(target_id)
        if not entry:
            raise RuntimeError(f"Aucune fiche trouvée pour {display_name}.")
        mules = entry.get("mules", [])
        idx = None
        for i, mule in enumerate(mules):
            if isinstance(mule, str) and mule.lower() == mule_name.lower():
                idx = i
                break
        if idx is None:
            raise RuntimeError(f"La mule {mule_name} n'est pas enregistrée pour {display_name}.")
        removed = mules.pop(idx)
        entry["mules"] = mules
        await players_cog.dump_data_to_console(ctx)
        return self._format_command_summary(
            ["[IA] membre.delmule"],
            f"Mule **{removed}** retirée pour **{display_name}**.",
        )

    async def _grant_role(self, ctx: commands.Context, member_raw: str, role_raw: str) -> str:
        member = self._resolve_member_argument(ctx, member_raw)
        if member is None:
            raise RuntimeError("Membre introuvable pour l'ajout de rôle.")
        role = self._resolve_role(ctx, role_raw)
        if role is None:
            raise RuntimeError("Rôle introuvable.")
        if role in getattr(member, "roles", []):
            return self._format_command_summary(
                ["[IA] roles.add"],
                f"{member.display_name} possède déjà le rôle **{role.name}**.",
            )
        try:
            await member.add_roles(role, reason="IA Staff role grant")
        except Exception as exc:
            raise RuntimeError(f"Impossible d'ajouter le rôle {role.name}: {exc}")
        return self._format_command_summary(
            ["[IA] roles.add"],
            f"Rôle **{role.name}** ajouté à **{member.display_name}**.",
        )

    async def _revoke_role(self, ctx: commands.Context, member_raw: str, role_raw: str) -> str:
        member = self._resolve_member_argument(ctx, member_raw)
        if member is None:
            raise RuntimeError("Membre introuvable pour le retrait de rôle.")
        role = self._resolve_role(ctx, role_raw)
        if role is None:
            raise RuntimeError("Rôle introuvable.")
        if role not in getattr(member, "roles", []):
            return self._format_command_summary(
                ["[IA] roles.remove"],
                f"{member.display_name} n'a pas le rôle **{role.name}**.",
            )
        try:
            await member.remove_roles(role, reason="IA Staff role revoke")
        except Exception as exc:
            raise RuntimeError(f"Impossible de retirer le rôle {role.name}: {exc}")
        return self._format_command_summary(
            ["[IA] roles.remove"],
            f"Rôle **{role.name}** retiré de **{member.display_name}**.",
        )

    def _requires_confirmation(self, tool_name: str) -> bool:
        return tool_name in SENSITIVE_TOOL_NAMES

    def _prune_expired_confirmations(self):
        if not self.pending_confirmations:
            return
        now = datetime.utcnow()
        expired = [
            nonce
            for nonce, entry in self.pending_confirmations.items()
            if (now - entry.get("created_at", now)).total_seconds() > IASTAFF_CONFIRM_TTL
        ]
        for nonce in expired:
            self.pending_confirmations.pop(nonce, None)

    def _register_confirmation_request(
        self, ctx: commands.Context, tool_name: str, payload: dict
    ) -> str:
        self._prune_expired_confirmations()
        nonce = secrets.token_hex(8)
        entry = {
            "created_at": datetime.utcnow(),
            "channel_id": getattr(ctx.channel, "id", None),
            "user_id": getattr(ctx.author, "id", None),
            "tool": tool_name,
            "payload": payload,
        }
        self.pending_confirmations[nonce] = entry
        minutes = max(1, int(round(IASTAFF_CONFIRM_TTL / 60)))
        return (
            "Action sensible détectée. Confirme avec `!iastaff confirm "
            f"{nonce}` dans les {minutes} minute(s)."
        )

    async def _confirm_pending_action(self, ctx: commands.Context, nonce: str) -> str:
        self._prune_expired_confirmations()
        entry = self.pending_confirmations.get(nonce)
        if not entry:
            return "Nonce introuvable ou expiré. Relance la commande initiale."
        now = datetime.utcnow()
        if (now - entry.get("created_at", now)).total_seconds() > IASTAFF_CONFIRM_TTL:
            self.pending_confirmations.pop(nonce, None)
            return "Nonce expiré. Relance la commande initiale."
        channel_id = getattr(ctx.channel, "id", None)
        user_id = getattr(ctx.author, "id", None)
        if channel_id != entry.get("channel_id"):
            return "Confirmation refusée : utilise le même salon que la demande initiale."
        if user_id != entry.get("user_id"):
            return "Confirmation refusée : seul l'auteur de la demande peut valider."
        self.pending_confirmations.pop(nonce, None)
        try:
            return await self._dispatch_command_tool(
                ctx,
                entry.get("tool", ""),
                entry.get("payload") or {},
                skip_confirmation=True,
            )
        except Exception as exc:
            log.warning("IAStaff: échec lors de la confirmation tool=%s nonce=%s: %s", entry.get("tool"), nonce, exc)
            return f"❌ Erreur pendant l'exécution confirmée : {exc}"

    async def _dispatch_command_tool(
        self, ctx: commands.Context, name: str, args_json: dict, *, skip_confirmation: bool = False
    ) -> str:
        """Route a tool call to the matching Discord command."""
        normalized = (name or "").strip().lower()
        payload = args_json or {}
        channel_id = getattr(ctx.channel, "id", None)
        user_id = getattr(ctx.author, "id", None)

        if (
            IASTAFF_CONFIRM_DESTRUCTIVE
            and not skip_confirmation
            and self._requires_confirmation(normalized)
        ):
            return self._register_confirmation_request(ctx, normalized, payload)

        async def invoke(cmd_name: str, /, *pos_args, **kwargs):
            command = self.bot.get_command(cmd_name)
            if not command:
                log.warning("IAStaff: commande %s introuvable pour tool %s", cmd_name, normalized)
                raise RuntimeError(f"Commande inconnue: {cmd_name}")
            log.debug(
                "IAStaff tool dispatch command=%s tool=%s user=%s channel=%s payload=%s",
                cmd_name,
                normalized,
                user_id,
                channel_id,
                {"args": pos_args, "kwargs": kwargs},
            )
            return await ctx.invoke(command, *pos_args, **kwargs)

        if normalized == "create_activity":
            title = (payload.get("title") or "").strip()
            datetime_value = (payload.get("datetime") or "").strip()
            description_value = (payload.get("description") or "").strip()
            if not title or not datetime_value:
                raise RuntimeError("Informations manquantes pour create_activity")
            parts = [title, datetime_value]
            if description_value:
                parts.append(description_value)
            args_text = " ".join(parts)
            await invoke("activite", action="creer", args=args_text)
            return f"Activité créée avec `!activite creer` : **{title}** ({datetime_value})."
        if normalized == "list_activities":
            await invoke("activite", action="liste", args=None)
            return "Liste des activités affichée."
        if normalized == "join_activity":
            event_id = (payload.get("id") or "").strip()
            if not event_id:
                raise RuntimeError("Identifiant requis pour join_activity")
            await invoke("activite", action="join", args=event_id)
            return f"Inscription demandée pour l'activité `{event_id}`."
        if normalized == "cancel_activity":
            event_id = (payload.get("id") or "").strip()
            if not event_id:
                raise RuntimeError("Identifiant requis pour cancel_activity")
            await invoke("activite", action="annuler", args=event_id)
            return f"Annulation demandée pour l'activité `{event_id}`."
        if normalized == "leave_activity":
            event_id = (payload.get("id") or "").strip()
            if not event_id:
                raise RuntimeError("Identifiant requis pour leave_activity")
            await invoke("activite", action="leave", args=event_id)
            return f"Désinscription demandée pour l'activité `{event_id}`."
        if normalized == "activity_info":
            event_id = (payload.get("id") or "").strip()
            if not event_id:
                raise RuntimeError("Identifiant requis pour activity_info")
            await invoke("activite", action="info", args=event_id)
            return f"Détails demandés pour l'activité `{event_id}`."
        if normalized == "modify_activity":
            event_id = (payload.get("id") or "").strip()
            datetime_value = (payload.get("datetime") or "").strip()
            description_value = (payload.get("description") or "").strip()
            if not event_id or not datetime_value:
                raise RuntimeError("Paramètres requis pour modify_activity")
            args_text = f"{event_id} {datetime_value}"
            if description_value:
                args_text = f"{args_text} {description_value}"
            await invoke("activite", action="modifier", args=args_text)
            return f"Modification demandée pour l'activité `{event_id}` ({datetime_value})."
        if normalized == "open_ticket":
            await invoke("ticket")
            return "Ouverture de ticket initialisée (`!ticket`)."
        if normalized == "start_organisation":
            await invoke("organisation")
            return "Assistant d'organisation lancé (`!organisation`)."
        if normalized == "start_announce":
            await invoke("annonce")
            return "Assistant d'annonce lancé (`!annonce`)."
        if normalized == "start_event":
            title = (payload.get("title") or "").strip()
            date_time = (payload.get("date_time") or "").strip()
            description = (payload.get("description") or "").strip()
            if not title or not date_time or not description:
                raise RuntimeError("Informations requises pour start_event")
            await invoke("event")
            summary_lines = [
                f"Briefing événement : **{title}**",
                f"Date/heure : {date_time}",
                description,
            ]
            summary = "\n".join(line for line in summary_lines if line)
            return self._format_command_summary(["!event"], summary)
        if normalized == "clear_console":
            channel_value = (payload.get("channel") or "console").strip() or "console"
            await invoke("clear", channel_name=channel_value)
            return f"Nettoyage demandé via `!clear {channel_value}`."
        if normalized == "show_warnings":
            member_raw = payload.get("member")
            member_obj = self._resolve_member_argument(ctx, member_raw)
            if member_obj is None:
                raise RuntimeError("Membre introuvable pour show_warnings")
            await invoke("warnings", member=member_obj)
            target_name = getattr(member_obj, "display_name", str(getattr(member_obj, "id", "?")))
            return f"Avertissements consultés pour {target_name}."
        if normalized == "reset_warnings":
            member_raw = payload.get("member")
            member_obj = self._resolve_member_argument(ctx, member_raw)
            if member_obj is None:
                raise RuntimeError("Membre introuvable pour reset_warnings")
            await invoke("resetwarnings", member=member_obj)
            target_name = getattr(member_obj, "display_name", str(getattr(member_obj, "id", "?")))
            return f"Avertissements réinitialisés pour {target_name}."
        if normalized == "add_recruitment_entry":
            pseudo = (payload.get("pseudo") or "").strip()
            if not pseudo:
                raise RuntimeError("Pseudo requis pour add_recruitment_entry")
            await invoke("recrutement", pseudo=pseudo)
            return f"Fiche recrutement créée pour **{pseudo}**."
        if normalized == "delete_member_record":
            pseudo = (payload.get("pseudo") or "").strip()
            if not pseudo:
                raise RuntimeError("Pseudo requis pour delete_member_record")
            await invoke("membre del", pseudo=pseudo)
            return f"Demande de suppression envoyée pour **{pseudo}**."
        if normalized == "stats_reset":
            await invoke("stats reset")
            return "Statistiques réinitialisées (`!stats reset`)."
        if normalized == "stats_enable":
            await invoke("stats on")
            return "Collecte des statistiques activée (`!stats on`)."
        if normalized == "stats_disable":
            await invoke("stats off")
            return "Collecte des statistiques désactivée (`!stats off`)."
        if normalized == "job_list_all":
            await invoke("job", "liste")
            summary = "La liste complète des métiers est disponible ci-dessus."
            return self._format_command_summary(["!job liste"], summary)
        if normalized == "job_list_professions":
            await invoke("job", "liste", "metier")
            summary = "Catalogue des métiers affiché."
            return self._format_command_summary(["!job liste metier"], summary)
        if normalized == "job_lookup_player":
            player = (payload.get("player") or "").strip()
            if not player:
                raise RuntimeError("Pseudo requis pour job_lookup_player")
            await invoke("job", player)
            summary = await self._summarize_job_player(ctx, player)
            return self._format_command_summary([f"!job {player}"], summary or f"Fiche métiers demandée pour **{player}**.")
        if normalized == "job_lookup_profession":
            profession = (payload.get("profession") or "").strip()
            if not profession:
                raise RuntimeError("Métier requis pour job_lookup_profession")
            await invoke("job", profession)
            summary = await self._summarize_job_profession(ctx, profession)
            return self._format_command_summary([f"!job {profession}"], summary or f"Recherche lancée pour le métier **{profession}**.")
        if normalized == "set_member_job":
            member_ref = (payload.get("member") or "").strip()
            job_name = (payload.get("job") or "").strip()
            level_raw = payload.get("level")
            if not member_ref or not job_name:
                raise RuntimeError("Informations manquantes pour set_member_job")
            try:
                level_value = int(level_raw)
            except (TypeError, ValueError):
                raise RuntimeError("Niveau invalide pour set_member_job")
            if not (1 <= level_value <= 200):
                raise RuntimeError("Le niveau doit être compris entre 1 et 200.")
            return await self._set_member_job(ctx, member_ref, job_name, level_value)
        if normalized == "remove_member_job":
            member_ref = (payload.get("member") or "").strip()
            job_name = (payload.get("job") or "").strip()
            if not member_ref or not job_name:
                raise RuntimeError("Informations manquantes pour remove_member_job")
            return await self._remove_member_job(ctx, member_ref, job_name)
        if normalized == "add_member_mule":
            member_ref = (payload.get("member") or "").strip()
            mule_name = (payload.get("mule") or "").strip()
            if not member_ref or not mule_name:
                raise RuntimeError("Informations manquantes pour add_member_mule")
            return await self._add_member_mule(ctx, member_ref, mule_name)
        if normalized == "remove_member_mule":
            member_ref = (payload.get("member") or "").strip()
            mule_name = (payload.get("mule") or "").strip()
            if not member_ref or not mule_name:
                raise RuntimeError("Informations manquantes pour remove_member_mule")
            return await self._remove_member_mule(ctx, member_ref, mule_name)
        if normalized == "run_bot_command":
            command_name = (payload.get("command") or "").strip()
            if not command_name:
                raise RuntimeError("Commande manquante pour run_bot_command")
            pos_args = payload.get("positional_args") or []
            kw_args = payload.get("keyword_args") or {}
            if not isinstance(pos_args, list):
                raise RuntimeError("`positional_args` doit être une liste.")
            if not isinstance(kw_args, dict):
                raise RuntimeError("`keyword_args` doit être un objet.")
            pos_list = [str(arg) for arg in pos_args]
            kw_map = {str(k): str(v) for k, v in kw_args.items()}
            await invoke(command_name, *pos_list, **kw_map)
            summary = f"Commande `{command_name}` exécutée."
            if pos_list:
                summary += f"\nArgs: {', '.join(pos_list)}"
            if kw_map:
                mapped = ", ".join(f"{k}={v}" for k, v in kw_map.items())
                summary += f"\nOptions: {mapped}"
            return self._format_command_summary([f"!{command_name}"], summary)
        if normalized == "grant_role":
            member_ref = (payload.get("member") or "").strip()
            role_ref = (payload.get("role") or "").strip()
            if not member_ref or not role_ref:
                raise RuntimeError("Informations manquantes pour grant_role")
            return await self._grant_role(ctx, member_ref, role_ref)
        if normalized == "revoke_role":
            member_ref = (payload.get("member") or "").strip()
            role_ref = (payload.get("role") or "").strip()
            if not member_ref or not role_ref:
                raise RuntimeError("Informations manquantes pour revoke_role")
            return await self._revoke_role(ctx, member_ref, role_ref)
        raise RuntimeError(f"Tool inconnu: {name}")

    async def _try_chat_with_tools(self, ctx: commands.Context, messages: list[dict]) -> str | None:
        """Ask Chat Completions for a tool call and execute it if present."""
        if not self.enable_tools or not self.client:
            return None
        tool_specs = self._command_tools()
        if not tool_specs:
            return None
        chat_messages = self._to_chat_messages(messages)
        chat_messages.insert(
            1,
            {
                "role": "system",
                "content": (
                    "Tu dois orchestrer les commandes Staff en plusieurs étapes si besoin. Utilise les outils "
                    "(create_activity, list_activities, join_activity, cancel_activity, open_ticket, "
                    "start_organisation, start_announce (annonces officielles via `!annonce`), start_event, "
                    "clear_console, warnings/resetwarnings, recrutement, membre del/mules, stats, jobs, rôles, etc.). "
                    "Lorsque l'utilisateur mentionne un canal (#annonces, #organisation, etc.), choisis l'outil "
                    "approprié (ex: `start_announce` pour une annonce officielle, `start_organisation` pour "
                    "préparer un événement interne). Quand aucune action dédiée n'existe, emploie `run_bot_command` "
                    "avec le nom de la commande et ses arguments. Pose des questions courtes pour obtenir les infos "
                    "manquantes et n'hésite pas à chaîner plusieurs outils pour atteindre l'objectif."
                ),
            },
        )
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=chat_messages,
                tools=tool_specs,
                tool_choice="auto",
            )
        except Exception as exc:
            log.warning("IAStaff: Chat Completions tool-calling indisponible: %s", exc)
            return None
        choices = getattr(resp, "choices", None)
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            return None
        ack_parts: list[str] = []
        for call in tool_calls:
            fn = getattr(call, "function", None)
            if not fn:
                continue
            fn_name = getattr(fn, "name", "")
            raw_args = getattr(fn, "arguments", "") or "{}"
            try:
                if isinstance(raw_args, str):
                    parsed_args = json.loads(raw_args)
                elif isinstance(raw_args, dict):
                    parsed_args = raw_args
                elif hasattr(raw_args, "__dict__"):
                    parsed_args = {k: v for k, v in raw_args.__dict__.items()}
                else:
                    parsed_args = dict(raw_args)
            except Exception:
                parsed_args = {}
            try:
                ack = await self._dispatch_command_tool(ctx, fn_name, parsed_args)
                if ack:
                    ack_parts.append(ack)
            except Exception as exc:
                log.warning("IAStaff: échec tool %s: %s", fn_name, exc)
                ack_parts.append(f"❌ Erreur pendant `{fn_name}` : {exc}")
        if ack_parts:
            return "\n".join(ack_parts)
        return "Action effectuée."

    def _to_chat_messages(self, messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            role = m.get("role", "user")
            content_parts = m.get("content") or []
            text = _content_to_string(content_parts)
            if not text:
                continue
            if role == "developer":
                role = "system"
            if role == "assistant":
                role = "assistant"
            if role == "system":
                role = "system"
            if role == "user":
                role = "user"
            out.append({"role": role, "content": text})
        return out

    async def _ask_openai(self, messages: list[dict]) -> str:
        if not self._ensure_client():
            raise RuntimeError(self._load_error or "Client OpenAI indisponible.")
        req = self._build_request(messages)
        try:
            resp = await self.client.responses.create(**req)
            txt = extract_generated_text(resp)
            if not txt.strip() and req.get("tools"):
                safe_req = dict(req)
                safe_req.pop("tools", None)
                safe_req.pop("tool_resources", None)
                resp2 = await self.client.responses.create(**safe_req)
                txt = extract_generated_text(resp2)
            if txt.strip():
                return txt
        except Exception as e:
            try:
                safe_req = dict(req)
                safe_req.pop("tools", None)
                safe_req.pop("tool_resources", None)
                resp = await self.client.responses.create(**safe_req)
                txt = extract_generated_text(resp)
                if txt.strip():
                    return txt
            except Exception as ee:
                raise RuntimeError(f"Erreur API OpenAI: {ee}") from ee
            raise RuntimeError(f"Erreur API OpenAI: {e}") from e
        if True:
            try:
                cc_messages = self._to_chat_messages(messages)
                resp = await self.client.chat.completions.create(model=self.model, messages=cc_messages, max_tokens=MAX_OUTPUT_TOKENS)
                data = _to_dict(resp)
                choice = None
                if isinstance(data.get("choices"), list) and data["choices"]:
                    choice = data["choices"][0]
                if isinstance(choice, dict):
                    msg = choice.get("message")
                    if isinstance(msg, dict):
                        cont = msg.get("content")
                        if isinstance(cont, str) and cont.strip():
                            return cont.strip()
            except Exception as e:
                pass
        return ""

    def _make_embed(self, page_text: str, idx: int, total: int) -> tuple[discord.Embed, list[discord.File]]:
        palette = [
            discord.Color.from_rgb(47, 128, 237),
            discord.Color.from_rgb(52, 199, 89),
            discord.Color.from_rgb(255, 159, 64),
        ]
        color = palette[(idx - 1) % len(palette)]
        emb = discord.Embed(description=page_text, color=color)
        title = "IA Staff" + (f" • {idx}/{total}" if total > 1 else "")
        files: list[discord.File] = []
        if self.has_logo:
            files.append(discord.File(self.logo_path, filename="iastaff.png"))
            emb.set_author(name=title, icon_url="attachment://iastaff.png")
        else:
            emb.set_author(name=title)
        emb.timestamp = datetime.utcnow()
        return emb, files

    async def _send_long_reply(
        self,
        channel: discord.abc.Messageable,
        text: str,
        *,
        ctx: commands.Context | None = None,
        origin_message: discord.Message | None = None,
    ):
        if not text:
            target_ctx = ctx.reply if ctx is not None else None
            if target_ctx is not None:
                await target_ctx("Je n’ai rien reçu de l’API.", mention_author=False)
            elif origin_message is not None:
                await origin_message.reply("Je n’ai rien reçu de l’API.", mention_author=False)
            else:
                await channel.send("Je n’ai rien reçu de l’API.")
            return
        safe_text = _sanitize_discord_mentions(text)
        parts = chunk_text(safe_text, EMBED_SAFE_CHUNK)
        total = len(parts)
        allowed_mentions = _allowed_mentions()
        for i, part in enumerate(parts, start=1):
            emb, files = self._make_embed(part, i, total)
            if ctx is not None:
                await ctx.send(embed=emb, files=files or None, allowed_mentions=allowed_mentions)
            elif origin_message is not None and i == 1:
                await origin_message.reply(
                    embed=emb,
                    files=files or None,
                    mention_author=False,
                    allowed_mentions=allowed_mentions,
                )
            else:
                await channel.send(embed=emb, files=files or None, allowed_mentions=allowed_mentions)

    async def handle_staff_message(
        self,
        channel: discord.abc.Messageable,
        author: discord.abc.User,
        message: str,
        *,
        ctx: commands.Context | None = None,
        origin_message: discord.Message | None = None,
    ):
        if not self._ensure_client():
            reason = self._load_error or "Client OpenAI indisponible."
            if ctx is not None:
                await ctx.reply(f"[! ] IA Staff indisponible : {reason}", mention_author=False)
            elif origin_message is not None:
                await origin_message.reply(f"[! ] IA Staff indisponible : {reason}", mention_author=False)
            else:
                await channel.send(f"[! ] IA Staff indisponible : {reason}")
            return
        content = (message or "").strip()
        if not content:
            if ctx is not None:
                await ctx.reply("Donne un message après la commande, ex: `!iastaff Quel est le plan ?`", mention_author=False)
            elif origin_message is not None:
                await origin_message.reply("Message vide ignoré.", mention_author=False)
            else:
                await channel.send("Message vide ignoré.")
            return
        if isinstance(author, discord.Member):
            has_role = any(
                (role.name or "").lower() == (STAFF_ROLE_NAME or "").lower()
                for role in author.roles
            )
            if not has_role:
                if ctx is not None:
                    await ctx.reply("Accès réservé au Staff.", mention_author=False)
                elif origin_message is not None:
                    await origin_message.reply("Accès réservé au Staff.", mention_author=False)
                else:
                    await channel.send("Accès réservé au Staff.")
                return
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            if ctx is not None:
                await ctx.reply("Salon incompatible avec l’IA Staff.", mention_author=False)
            elif origin_message is not None:
                await origin_message.reply("Salon incompatible avec l’IA Staff.", mention_author=False)
            else:
                await channel.send("Salon incompatible avec l’IA Staff.")
            return
        lock = self._get_lock(channel_id)
        async with lock:
            typing_target = ctx if ctx is not None else channel
            async with typing_target.typing():
                channel_ctx = await self._build_channel_context(channel, origin_message if origin_message is not None else (ctx.message if ctx is not None else None))
                messages = self._make_messages(channel_ctx, channel_id, content)
                tool_ack: str | None = None
                if ctx is not None:
                    tool_ack = await self._try_chat_with_tools(ctx, messages)
                if tool_ack:
                    text = tool_ack
                else:
                    try:
                        text = await self._ask_openai(messages)
                    except Exception as e:
                        if ctx is not None:
                            await ctx.reply(f"❌ {e}", mention_author=False)
                        elif origin_message is not None:
                            await origin_message.reply(f"❌ {e}", mention_author=False)
                        else:
                            await channel.send(f"❌ {e}")
                        return
            if not text.strip():
                if ctx is not None:
                    await ctx.reply("La réponse de l'IA est vide. Réessaie en reformulant (ou `!iastaff reset`).", mention_author=False)
                elif origin_message is not None:
                    await origin_message.reply("La réponse de l'IA est vide. Réessaie plus tard.", mention_author=False)
                else:
                    await channel.send("La réponse de l'IA est vide. Réessaie plus tard.")
                return
            self._push_history(channel_id, "user", content)
            self._push_history(channel_id, "assistant", text)
        await self._send_long_reply(channel, text, ctx=ctx, origin_message=origin_message)

    @commands.command(name="iastaff", aliases=["staffia"])
    @commands.has_role(STAFF_ROLE_NAME)
    async def iastaff_cmd(self, ctx: commands.Context, *, message: str):
        msg = (message or "").strip()
        if not msg:
            await ctx.reply("Donne un message après la commande, ex: `!iastaff Quel est le plan ?`", mention_author=False)
            return
        low = msg.lower()
        channel_id = ctx.channel.id
        if low in {"reset", "clear", "new"}:
            self.history.pop(channel_id, None)
            await ctx.reply("Historique du salon effacé ✅", mention_author=False)
            return
        if low.startswith("confirm"):
            parts = msg.split(None, 1)
            if len(parts) < 2 or not parts[1].strip():
                await ctx.reply("Précise le nonce à confirmer : `!iastaff confirm <nonce>`.", mention_author=False)
                return
            ack = await self._confirm_pending_action(ctx, parts[1].strip())
            await ctx.reply(ack, mention_author=False)
            return
        if low in {"info", "config"}:
            details_lines = [
                f"Model: `{self.model}`",
                f"Timeout: {OPENAI_TIMEOUT}s | Max output tokens: {MAX_OUTPUT_TOKENS}",
                f"Température: {IASTAFF_TEMPERATURE} | Mentions: {'bloquées' if IASTAFF_SAFE_MENTIONS else 'autorisées'} | Règles: {IASTAFF_RULES_MODE}",
                f"Contexte canal: {CONTEXT_MESSAGES} msgs (≤{CONTEXT_MAX_CHARS} chars, {PER_MSG_TRUNC}/msg)",
                f"Mémoire IA (salon): {len(self.history.get(channel_id, []))} items (max {HISTORY_TURNS*2})",
                f"Web Search: {'activé' if ENABLE_WEB_SEARCH else 'désactivé'} | File Search: {'activé' if VECTOR_STORE_ID else 'désactivé'}",
                f"Confirmation actions sensibles: {'activée' if IASTAFF_CONFIRM_DESTRUCTIVE else 'désactivée'} (TTL {IASTAFF_CONFIRM_TTL}s, `!iastaff confirm <nonce>`)",
            ]
            details = "\n".join(details_lines)
            await ctx.reply(details, mention_author=False)
            return
        # Nouveau : !iastaff model <id> → switch du modèle à chaud (runtime)
        if low.startswith("model "):
            new_model = msg.split(None, 1)[1].strip()
            # normalisation basique côté bot (aliases ENV gérés par resolve_staff_model si besoin)
            normalized = new_model.lower().replace(" ", "-").replace("_", "-")
            self.model = normalized
            await ctx.reply(
                f"Modèle IA Staff mis à jour pour cette instance : `{self.model}`.\n"
                "💡 Pour rendre ce choix **persistant** après redémarrage Render, définis "
                "`OPENAI_STAFF_MODEL` dans les variables d'environnement.",
                mention_author=False,
            )
            return
        await self.handle_staff_message(ctx.channel, ctx.author, msg, ctx=ctx)

async def setup(bot: commands.Bot):
    await bot.add_cog(IAStaff(bot))
