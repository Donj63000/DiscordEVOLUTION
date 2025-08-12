#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import discord
from discord.ext import commands

try:
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover
    AsyncOpenAI = None

log = logging.getLogger("iastaff")

# ==== Config ====
STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")

DEFAULT_MODEL = os.getenv("OPENAI_STAFF_MODEL", "gpt-5-nano")
DEFAULT_PROMPT_ID = os.getenv("OPENAI_STAFF_PROMPT_ID", "pmpt_689900255180819686efd4ca8cebfc7706a0776e4dbf2240")
DEFAULT_PROMPT_VERSION = os.getenv("OPENAI_STAFF_PROMPT_VERSION", "5")  # v5

# Contexte canal
CONTEXT_MESSAGES = int(os.getenv("IASTAFF_CHANNEL_CONTEXT", "40"))
PER_MSG_TRUNC = int(os.getenv("IASTAFF_PER_MSG_CHARS", "200"))
CONTEXT_MAX_CHARS = int(os.getenv("IASTAFF_CONTEXT_MAX_CHARS", "6000"))

# Mémoire conversationnelle (par salon)
HISTORY_TURNS = int(os.getenv("IASTAFF_HISTORY_TURNS", "8"))

# Limites & timeouts
EMBED_DESC_LIMIT = 4096
EMBED_SAFE_CHUNK = 3800
OPENAI_TIMEOUT = float(os.getenv("IASTAFF_TIMEOUT", "120"))
MAX_OUTPUT_TOKENS = int(os.getenv("IASTAFF_MAX_OUTPUT_TOKENS", "1800"))
INPUT_MAX_CHARS = int(os.getenv("IASTAFF_INPUT_MAX_CHARS", "12000"))

# Autoriser l’outil Web Search (conforme à ton prompt). 0 pour désactiver si besoin.
ENABLE_WEB_SEARCH = os.getenv("IASTAFF_ENABLE_WEB", "1") != "0"

# Visuel
LOGO_FILENAME = os.getenv("IASTAFF_LOGO", "iastaff.png")


# ==== Utils ====
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


def extract_generated_text(resp_obj) -> str:
    """
    Extraction *sûre* du texte généré :
    - .output_text (voie standard)
    - .output items ('output_text') ou 'message' -> 'content' -> 'text'
    On IGNORE les champs du prompt (ex: 'instructions').
    """
    t = getattr(resp_obj, "output_text", None)
    if isinstance(t, str) and t.strip():
        return t.strip()

    data = None
    try:
        if hasattr(resp_obj, "model_dump"):
            data = resp_obj.model_dump()
    except Exception:
        data = None
    if not isinstance(data, dict):
        try:
            data = resp_obj.__dict__
        except Exception:
            data = None

    texts: list[str] = []
    if isinstance(data, dict):
        out = data.get("output") or data.get("outputs") or []
        if isinstance(out, list):
            for item in out:
                if not isinstance(item, dict):
                    continue
                typ = item.get("type")
                if typ == "output_text":
                    txt = item.get("text")
                    if isinstance(txt, str) and txt.strip():
                        texts.append(txt)
                elif typ == "message":
                    content = item.get("content") or []
                    if isinstance(content, list):
                        for c in content:
                            if not isinstance(c, dict):
                                continue
                            if c.get("type") in ("text", "output_text"):
                                txt = c.get("text")
                                if isinstance(txt, str) and txt.strip():
                                    texts.append(txt)

    return "\n".join([s for s in texts if s.strip()])


# ==== Cog ====
class IAStaff(commands.Cog):
    """Assistant IA réservé au staff, avec contexte canal+mémoire et logo en tête."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        if AsyncOpenAI is None:
            raise RuntimeError("La librairie 'openai' n'est pas installée. Ajoute 'openai' à requirements.txt.")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            log.warning("OPENAI_API_KEY manquante: !iastaff renverra une erreur tant que la clé n'est pas définie.")

        self.client = AsyncOpenAI(api_key=api_key, timeout=OPENAI_TIMEOUT)

        self.prompt_id = os.getenv("OPENAI_STAFF_PROMPT_ID", DEFAULT_PROMPT_ID)
        self.prompt_version = os.getenv("OPENAI_STAFF_PROMPT_VERSION", DEFAULT_PROMPT_VERSION)
        self.model = os.getenv("OPENAI_STAFF_MODEL", DEFAULT_MODEL)

        self.history: dict[int, list[dict[str, str]]] = {}
        self.locks: dict[int, asyncio.Lock] = {}

        # cache contexte canal: {channel_id: (last_msg_id, context_str)}
        self.channel_ctx_cache: dict[int, tuple[int | None, str]] = {}

        self.logo_path = os.path.join(os.path.dirname(__file__), LOGO_FILENAME)
        self.has_logo = os.path.exists(self.logo_path)

    async def cog_load(self):
        log.info(
            "IAStaff prêt (prompt_id=%s, version=%s | fallback model=%s | history=%d tours)",
            self.prompt_id, self.prompt_version, self.model, HISTORY_TURNS
        )

    # ---- mémoire ----
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

    def _render_history_block(self, channel_id: int) -> str:
        buf = self.history.get(channel_id, [])
        if not buf:
            return ""
        lines = ["Mémoire récente (échanges précédents avec l’assistant) :"]
        for item in buf:
            who = "Utilisateur" if item["role"] == "user" else "Assistant"
            lines.append(f"{who}: {item['text']}")
        return "\n".join(lines)

    # ---- contexte canal ----
    async def _build_channel_context(self, ctx: commands.Context) -> str:
        ch: discord.TextChannel = ctx.channel  # type: ignore
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

    # ---- OpenAI ----
    def _build_request(self, input_text: str) -> dict:
        base = {
            "input": input_text,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }
        if ENABLE_WEB_SEARCH:
            base["tools"] = [{"type": "web_search"}]  # <- autorise Web Search côté OpenAI
        if self.prompt_id:
            base["prompt"] = {"id": self.prompt_id, "version": self.prompt_version}
        else:
            base["model"] = self.model
        return base

    async def _ask_openai(self, final_input: str) -> str:
        try:
            req = self._build_request(final_input)
            resp = await self.client.responses.create(**req)
            text = extract_generated_text(resp)
            return text
        except Exception as e:
            raise RuntimeError(f"Erreur API OpenAI: {e}") from e

    # ---- Discord envoi ----
    def _make_embed(self, page_text: str, idx: int, total: int) -> tuple[discord.Embed, list[discord.File]]:
        emb = discord.Embed(description=page_text, color=discord.Color.teal())
        title = "IA Staff" + (f" • {idx}/{total}" if total > 1 else "")
        files: list[discord.File] = []
        if self.has_logo:
            files.append(discord.File(self.logo_path, filename="iastaff.png"))
            emb.set_author(name=title, icon_url="attachment://iastaff.png")
        else:
            emb.set_author(name=title)
        emb.set_footer(text=("Prompt Playground" if self.prompt_id else f"Modèle: {self.model}"))
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

    # ---- Commande principale ----
    @commands.command(name="iastaff", aliases=["staffia"])
    @commands.has_role(STAFF_ROLE_NAME)
    async def iastaff_cmd(self, ctx: commands.Context, *, message: str):
        if not os.environ.get("OPENAI_API_KEY"):
            await ctx.reply(
                "❌ `OPENAI_API_KEY` n'est pas configurée sur l'hébergement. Ajoute la variable puis redeploie.",
                mention_author=False
            )
            return

        msg = (message or "").strip()
        if not msg:
            await ctx.reply("Donne un message après la commande, ex: `!iastaff Quel est le plan ?`",
                            mention_author=False)
            return

        low = msg.lower()
        channel_id = ctx.channel.id

        if low in {"reset", "clear", "new"}:
            self.history.pop(channel_id, None)
            await ctx.reply("Historique du salon effacé ✅", mention_author=False)
            return

        if low in {"info", "config"}:
            details = (
                f"Prompt: `{self.prompt_id or '—'}`\n"
                f"Version: `{self.prompt_version if self.prompt_id else '—'}`\n"
                f"Model fallback: `{self.model}`\n"
                f"Timeout: {OPENAI_TIMEOUT}s | Max output tokens: {MAX_OUTPUT_TOKENS}\n"
                f"Contexte canal: {CONTEXT_MESSAGES} msgs (≤{CONTEXT_MAX_CHARS} chars, {PER_MSG_TRUNC}/msg)\n"
                f"Mémoire IA (salon): {len(self.history.get(channel_id, []))} items (max {HISTORY_TURNS*2})\n"
                f"Web Search: {'activé' if ENABLE_WEB_SEARCH else 'désactivé'}"
            )
            await ctx.reply(details, mention_author=False)
            return

        lock = self._get_lock(channel_id)
        async with lock:
            async with ctx.typing():
                channel_ctx = await self._build_channel_context(ctx)
                memory_ctx = self._render_history_block(channel_id)

                sections = []
                if channel_ctx:
                    sections.append(channel_ctx)
                if memory_ctx:
                    sections.append(memory_ctx)
                sections.append(f"Utilisateur: {msg}\nAssistant:")
                final_input = "\n\n".join(sections)

                if len(final_input) > INPUT_MAX_CHARS:
                    overflow = len(final_input) - INPUT_MAX_CHARS
                    trimmed = channel_ctx[:-min(overflow + 500, len(channel_ctx))]
                    channel_ctx = trimmed + "\n…"
                    sections = []
                    if channel_ctx:
                        sections.append(channel_ctx)
                    if memory_ctx:
                        sections.append(memory_ctx)
                    sections.append(f"Utilisateur: {msg}\nAssistant:")
                    final_input = "\n\n".join(sections)

                try:
                    text = await self._ask_openai(final_input)
                except Exception as e:
                    await ctx.reply(f"❌ {e}", mention_author=False)
                    return

            if not text.strip():
                await ctx.reply(
                    "La réponse de l'IA est vide. Réessaie en reformulant (ou `!iastaff reset`).",
                    mention_author=False
                )
                return

            self._push_history(channel_id, "user", msg)
            self._push_history(channel_id, "assistant", text)

        await self._send_long_reply(ctx, text)


async def setup(bot: commands.Bot):
    await bot.add_cog(IAStaff(bot))
