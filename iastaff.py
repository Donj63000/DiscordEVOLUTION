#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import discord
from discord.ext import commands

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

log = logging.getLogger("iastaff")

STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")
DEFAULT_MODEL = os.getenv("OPENAI_STAFF_MODEL", "gpt-5")

CONTEXT_MESSAGES = int(os.getenv("IASTAFF_CHANNEL_CONTEXT", "40"))
PER_MSG_TRUNC = int(os.getenv("IASTAFF_PER_MSG_CHARS", "200"))
CONTEXT_MAX_CHARS = int(os.getenv("IASTAFF_CONTEXT_MAX_CHARS", "6000"))
HISTORY_TURNS = int(os.getenv("IASTAFF_HISTORY_TURNS", "8"))

EMBED_SAFE_CHUNK = 3800
OPENAI_TIMEOUT = float(os.getenv("IASTAFF_TIMEOUT", "120"))
MAX_OUTPUT_TOKENS = int(os.getenv("IASTAFF_MAX_OUTPUT_TOKENS", "1800"))
INPUT_MAX_CHARS = int(os.getenv("IASTAFF_INPUT_MAX_CHARS", "12000"))

ENABLE_WEB_SEARCH = os.getenv("IASTAFF_ENABLE_WEB", "1") != "0"
VECTOR_STORE_ID = os.getenv("IASTAFF_VECTOR_STORE_ID", "").strip()

LOGO_FILENAME = os.getenv("IASTAFF_LOGO", "iastaff.png")

GUILD_RULES = """Règlement Officiel de la Guilde Evolution – Édition du 19/02/2025

“Ensemble, nous évoluerons plus vite que seuls.”

Bienvenue au sein de la guilde Evolution ! Ce règlement assure une ambiance conviviale, respectueuse et motivante.

Nos Valeurs & Notre Vision
Convivialité & Partage
Respecte tes camarades, valorise leurs progrès et encourage-les.
Progression Collective
L’évolution de la guilde passe par la réussite de chaque membre.
Transparence & Communication
Les annonces et décisions importantes sont expliquées sur Discord (#📣 annonces 📣). Ouvre un ticket (!ticket) en cas de souci.

Respect & Convivialité
Aucun harcèlement, insulte, diffamation ou discrimination. Sanctions possibles. Politesse et bienveillance attendues. Gestion des conflits par dialogue privé ou médiation Staff.

Discord Obligatoire & Communication
Discord indispensable. Paramètre tes notifications. #📄 Général 📄 pour discuter, #📣 annonces 📣 pour infos officielles, #👌 astuce 👌 pour questions, #🌈 organisation 🌈 pour planifier. !ticket ouvre un salon privé avec le Staff.

Participation & Vie de Guilde
Présence régulière appréciée. Propose/participe aux événements. Entraide encouragée.

Percepteurs & Ressources
Droit de pose après 500 000 XP guilde. Rotation en cas de demande forte, éviter monopolisation. Défense collective encouragée. Communique pour éviter de gêner d’autres percepteurs.

Contribution d’XP à la Guilde
Taux flexible 1% à 99%. 0% interdit sauf accord temporaire via !ticket. 1% minimum pour l’élan collectif.

Recrutement & Nouveaux Membres
Recrutement par Staff et vétérans. Proposition via Staff. Période d’essai possible 2–3 jours.

Organisation Interne & Staff
Anciens rôles fusionnés sous “Staff”. Meneurs: Thalata et Coca-Coca. Décisions collégiales. Identifiables sur Discord.

Sanctions & Discipline
Rappels progressifs, sanctions collégiales si nécessaire. Transparence avec la personne concernée.

Multi-Guilde
Second personnage ailleurs toléré si engagement chez Evolution intact. Staff fidèle à Evolution.

Événements, Sondages & Animations
Sondages via !sondage. Activités via !activite creer. Concours et récompenses ponctuels.

Conclusion & Avenir
Respect, soutien mutuel et plaisir de jeu au centre des interactions. Règlement en vigueur 21/02/2025.

Rappels
Droit perco: 2 percepteurs par personne et pas sur la même zone.
"""

SYSTEM_PROMPT_DEFAULT = (
    "Tu es EvolutionPRO, IA du staff de la guilde Évolution (Dofus Retro 1.29). "
    "Tu réponds en français, clair, utile, cordial et efficace. "
    "Tu appliques strictement le règlement interne fourni et aides le Staff à modérer, organiser, planifier et analyser. "
    "Ne fabrique jamais de chiffres ni de faits sensibles. Si l’information est inconnue ou variable (prix HDV, recette modifiée, disponibilité), demande les données minimales ou propose un protocole pour les obtenir (capture, serveur, ressources et quantités, prix unitaire). "
    "Quand on demande du code, rends un bloc complet, sans commentaires, prêt à l’emploi, robuste et optimisé. "
    "Structure les réponses longues avec des listes concises et des étapes actionnables. "
    "En cas d’agression ou d’insulte, recadre fermement mais poliment et propose de continuer de manière constructive. "
    "N’évoque aucune configuration de playground. Tu fonctionnes uniquement via l’API. "
    "Si une tâche dépend du web et que l’outil de recherche n’est pas disponible, propose un plan ou les champs à renseigner pour faire le calcul hors-ligne."
)

MODERATION_PROMPT = (
    "Règles de modération : "
    "Maintiens le respect, rappelle poliment le règlement si nécessaire, refuse les propos discriminatoires. "
    "Si conflit : propose d’ouvrir !ticket et d’échanger calmement."
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
        if AsyncOpenAI is None:
            raise RuntimeError("La librairie 'openai' n'est pas installée. Ajoute 'openai' à requirements.txt.")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            log.warning("OPENAI_API_KEY manquante: !iastaff renverra une erreur tant que la clé n'est pas définie.")
        self.client = AsyncOpenAI(api_key=api_key, timeout=OPENAI_TIMEOUT)
        self.model = os.getenv("OPENAI_STAFF_MODEL", DEFAULT_MODEL)
        self.system_prompt = os.getenv("IASTAFF_SYSTEM_PROMPT", SYSTEM_PROMPT_DEFAULT)
        self.history: dict[int, list[dict[str, str]]] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.channel_ctx_cache: dict[int, tuple[int | None, str]] = {}
        self.logo_path = os.path.join(os.path.dirname(__file__), LOGO_FILENAME)
        self.has_logo = os.path.exists(self.logo_path)

    async def cog_load(self):
        log.info("IAStaff prêt (model=%s | history=%d tours | web=%s | files=%s)", self.model, HISTORY_TURNS, "on" if ENABLE_WEB_SEARCH else "off", "on" if VECTOR_STORE_ID else "off")

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

    async def _build_channel_context(self, ctx: commands.Context) -> str:
        ch: discord.TextChannel = ctx.channel
        last_id = ch.last_message_id
        cached = self.channel_ctx_cache.get(ch.id)
        if cached and cached[0] == last_id:
            return cached[1]
        lines = ["Contexte du canal (jusqu’aux 40 derniers messages) :"]
        gathered_chars = 0
        msgs = []
        async for m in ch.history(limit=CONTEXT_MESSAGES, before=ctx.message, oldest_first=True):
            msgs.append(m)
        for m in msgs:
            txt = (m.clean_content or "").strip()
            if not txt:
                continue
            if len(txt) > PER_MSG_TRUNC:
                txt = txt[:PER_MSG_TRUNC] + "…"
            line = f"- {txt}"
            if gathered_chars + len(line) + 1 > CONTEXT_MAX_CHARS:
                lines.append("…")
                break
            lines.append(line)
            gathered_chars += len(line) + 1
        block = "\n".join(lines)
        self.channel_ctx_cache[ch.id] = (last_id, block)
        return block

    def _make_messages(self, channel_ctx: str, channel_id: int, user_msg: str) -> list[dict]:
        messages: list[dict] = []
        messages.append({"role": "system", "content": [{"type": "input_text", "text": self.system_prompt}]})
        messages.append({"role": "developer", "content": [{"type": "input_text", "text": MODERATION_PROMPT}]})
        messages.append({"role": "developer", "content": [{"type": "input_text", "text": GUILD_RULES}]})
        if channel_ctx:
            messages.append({"role": "developer", "content": [{"type": "input_text", "text": channel_ctx}]})
        hist = self.history.get(channel_id, [])
        for item in hist:
            if item["role"] == "user":
                messages.append({"role": "user", "content": [{"type": "input_text", "text": item["text"]}]} )
            else:
                messages.append({"role": "assistant", "content": [{"type": "output_text", "text": item["text"]}]} )
        messages.append({"role": "user", "content": [{"type": "input_text", "text": user_msg}]})
        approx_chars = 0
        for m in messages:
            for c in m.get("content", []):
                t = c.get("text")
                if isinstance(t, str):
                    approx_chars += len(t)
        if approx_chars > INPUT_MAX_CHARS and channel_ctx:
            overflow = approx_chars - INPUT_MAX_CHARS
            trimmed = channel_ctx[:-min(overflow + 500, len(channel_ctx))]
            channel_ctx = trimmed + "\n…"
            messages = [messages[0], messages[1], messages[2]]
            messages.append({"role": "developer", "content": [{"type": "input_text", "text": channel_ctx}]})
            for item in hist:
                if item["role"] == "user":
                    messages.append({"role": "user", "content": [{"type": "input_text", "text": item["text"]}]} )
                else:
                    messages.append({"role": "assistant", "content": [{"type": "output_text", "text": item["text"]}]} )
            messages.append({"role": "user", "content": [{"type": "input_text", "text": user_msg}]})
        return messages

    def _build_request(self, messages: list[dict]) -> dict:
        base = {"model": self.model, "input": messages, "max_output_tokens": MAX_OUTPUT_TOKENS, "store": False}
        tools = []
        if ENABLE_WEB_SEARCH:
            tools.append({"type": "web_search"})
        if VECTOR_STORE_ID:
            tools.append({"type": "file_search"})
            base["tool_resources"] = {"file_search": {"vector_store_ids": [VECTOR_STORE_ID]}}
        if tools:
            base["tools"] = tools
        return base

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
        emb = discord.Embed(description=page_text, color=discord.Color.teal())
        title = "IA Staff" + (f" • {idx}/{total}" if total > 1 else "")
        files: list[discord.File] = []
        if self.has_logo:
            files.append(discord.File(self.logo_path, filename="iastaff.png"))
            emb.set_author(name=title, icon_url="attachment://iastaff.png")
        else:
            emb.set_author(name=title)
        emb.set_footer(text=f"Modèle: {self.model}")
        return emb, files

    async def _send_long_reply(self, ctx: commands.Context, text: str):
        if not text:
            await ctx.reply("Je n’ai rien reçu de l’API.", mention_author=False)
            return
        parts = chunk_text(text, EMBED_SAFE_CHUNK)
        total = len(parts)
        for i, part in enumerate(parts, start=1):
            emb, files = self._make_embed(part, i, total)
            await ctx.send(embed=emb, files=files or None)

    @commands.command(name="iastaff", aliases=["staffia"])
    @commands.has_role(STAFF_ROLE_NAME)
    async def iastaff_cmd(self, ctx: commands.Context, *, message: str):
        if not os.environ.get("OPENAI_API_KEY"):
            await ctx.reply("❌ `OPENAI_API_KEY` n'est pas configurée sur l'hébergement. Ajoute la variable puis redeploie.", mention_author=False)
            return
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
        if low in {"info", "config"}:
            details = (
                f"Model: `{self.model}`\n"
                f"Timeout: {OPENAI_TIMEOUT}s | Max output tokens: {MAX_OUTPUT_TOKENS}\n"
                f"Contexte canal: {CONTEXT_MESSAGES} msgs (≤{CONTEXT_MAX_CHARS} chars, {PER_MSG_TRUNC}/msg)\n"
                f"Mémoire IA (salon): {len(self.history.get(channel_id, []))} items (max {HISTORY_TURNS*2})\n"
                f"Web Search: {'activé' if ENABLE_WEB_SEARCH else 'désactivé'} | File Search: {'activé' if VECTOR_STORE_ID else 'désactivé'}"
            )
            await ctx.reply(details, mention_author=False)
            return
        lock = self._get_lock(channel_id)
        async with lock:
            async with ctx.typing():
                channel_ctx = await self._build_channel_context(ctx)
                messages = self._make_messages(channel_ctx, channel_id, msg)
                try:
                    text = await self._ask_openai(messages)
                except Exception as e:
                    await ctx.reply(f"❌ {e}", mention_author=False)
                    return
            if not text.strip():
                await ctx.reply("La réponse de l'IA est vide. Réessaie en reformulant (ou `!iastaff reset`).", mention_author=False)
                return
            self._push_history(channel_id, "user", msg)
            self._push_history(channel_id, "assistant", text)
        await self._send_long_reply(ctx, text)

async def setup(bot: commands.Bot):
    await bot.add_cog(IAStaff(bot))
