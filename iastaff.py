#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import discord
from discord.ext import commands

try:
    # Client asynchrone de la nouvelle API OpenAI
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover
    AsyncOpenAI = None  # vérifié au runtime

log = logging.getLogger("iastaff")

# ---- Configuration par défaut ----
STAFF_ROLE_NAME = "Staff"

DEFAULT_MODEL = "gpt-5-nano"  # utilisé seulement si aucun prompt Playground n'est fourni
DEFAULT_PROMPT_ID = "pmpt_689900255180819686efd4ca8cebfc7706a0776e4dbf2240"
DEFAULT_PROMPT_VERSION = "4"  # <-- version Playground à jour

# Limites Discord
EMBED_DESC_LIMIT = 4096
SAFE_EMBED_CHUNK = 3800  # marge de sécu sous 4096
MSG_CONTENT_LIMIT = 2000

# Mémoire de conversation
DEFAULT_HISTORY_TURNS = int(os.getenv("IASTAFF_HISTORY_TURNS", "8"))  # nb d'échanges (user+assistant) conservés


def chunk_text(text: str, limit: int) -> list[str]:
    """Découpe du texte en morceaux <= limit (respecte les fins de lignes autant que possible)."""
    parts, buf = [], ""
    for line in text.split("\n"):
        # +1 pour le '\n' qu'on rajoute
        if len(buf) + len(line) + 1 > limit:
            if buf:
                parts.append(buf)
            # si la ligne elle-même est énorme, on la coupe brutalement
            while len(line) > limit:
                parts.append(line[:limit])
                line = line[limit:]
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf:
        parts.append(buf)
    return parts or [""]


def deep_collect_text(node) -> str:
    """
    Certains retours de la Responses API n'exposent pas output_text.
    On parcourt récursivement la structure pour récupérer tous les champs 'text'.
    """
    out = []

    def _walk(x):
        if isinstance(x, dict):
            # Plusieurs structures possibles: 'text', 'value', 'content'...
            if "text" in x and isinstance(x["text"], str):
                out.append(x["text"])
            for v in x.values():
                _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)

    try:
        # Les objets OpenAI sont Pydantic v2
        data = node.model_dump() if hasattr(node, "model_dump") else None
    except Exception:
        data = None

    if data is None:
        try:
            data = node.dict()  # vieux fallback
        except Exception:
            data = None

    if data is None:
        # Dernier recours: introspection grossière
        try:
            data = node.__dict__
        except Exception:
            data = None

    if data is not None:
        _walk(data)

    return "\n".join([t for t in out if t.strip()])


class IAStaff(commands.Cog):
    """Assistant IA réservé au Staff, avec mémoire courte par salon."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        if AsyncOpenAI is None:
            raise RuntimeError("La librairie 'openai' n'est pas installée. Ajoutez 'openai' à requirements.txt.")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            log.warning("OPENAI_API_KEY manquante: !iastaff affichera une erreur tant que la clé n'est pas définie.")

        self.client = AsyncOpenAI(api_key=api_key)

        # Playground prompt prioritaire, sinon modèle
        self.prompt_id = os.getenv("OPENAI_STAFF_PROMPT_ID", DEFAULT_PROMPT_ID)
        self.prompt_version = os.getenv("OPENAI_STAFF_PROMPT_VERSION", DEFAULT_PROMPT_VERSION)
        self.model = os.getenv("OPENAI_STAFF_MODEL", DEFAULT_MODEL)

        # Mémoire: {channel_id: [{"role":"user"/"assistant", "text": "..."}]}
        self.history: dict[int, list[dict[str, str]]] = {}

        # Verrous par salon (éviter les courses si 2 staff lancent en même temps)
        self.locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self):
        log.info(
            "IAStaff prêt (prompt_id=%s, version=%s | fallback model=%s | history=%d tours)",
            self.prompt_id, self.prompt_version, self.model, DEFAULT_HISTORY_TURNS
        )

    # --------------- Outils mémoire ---------------

    def _get_lock(self, channel_id: int) -> asyncio.Lock:
        lock = self.locks.get(channel_id)
        if not lock:
            lock = asyncio.Lock()
            self.locks[channel_id] = lock
        return lock

    def _push_history(self, channel_id: int, role: str, text: str):
        buf = self.history.setdefault(channel_id, [])
        buf.append({"role": role, "text": text})
        # On garde uniquement les N derniers tours (user+assistant = 2 items)
        max_items = DEFAULT_HISTORY_TURNS * 2
        if len(buf) > max_items:
            self.history[channel_id] = buf[-max_items:]

    def _render_history_as_context(self, channel_id: int) -> str:
        """
        On sérialise l'historique au format simple:
        Utilisateur: ...
        Assistant: ...
        Cela marche très bien avec un prompt Playground.
        """
        buf = self.history.get(channel_id, [])
        lines = []
        if buf:
            lines.append(
                "Contexte de la conversation (messages récents). "
                "Réponds uniquement au dernier message de l'utilisateur en tenant compte de ce contexte.\n"
            )
        for item in buf:
            if item["role"] == "user":
                lines.append(f"Utilisateur: {item['text']}")
            else:
                lines.append(f"Assistant: {item['text']}")
        return "\n".join(lines).strip()

    # --------------- OpenAI call ---------------

    def _build_request(self, final_input: str) -> dict:
        """
        Construit la requête Responses API:
        - si un prompt Playground est fourni -> on l'utilise en priorité
        - sinon on passe par 'model'
        """
        if self.prompt_id:
            return {
                "prompt": {"id": self.prompt_id, "version": self.prompt_version},
                "input": final_input,
            }
        return {"model": self.model, "input": final_input}

    async def _ask_openai(self, final_input: str) -> str:
        """Appelle l'API et retourne un texte robuste (avec fallback deep scan)."""
        try:
            req = self._build_request(final_input)
            resp = await self.client.responses.create(**req)

            # 1) voie normale
            text = getattr(resp, "output_text", None)
            if text and text.strip():
                return text.strip()

            # 2) fallback: deep scan
            text = deep_collect_text(resp)
            if text and text.strip():
                return text.strip()

            # 3) dernier recours
            return ""
        except Exception as e:
            raise RuntimeError(f"Erreur API OpenAI: {e}") from e

    # --------------- Envoi Discord ---------------

    async def _send_long_reply(self, ctx: commands.Context, text: str):
        """
        Envoie une ou plusieurs embeds sans dépasser les limites Discord.
        """
        if not text:
            await ctx.reply("Je n’ai rien reçu de l’API.", mention_author=False)
            return

        parts = chunk_text(text, SAFE_EMBED_CHUNK)
        total = len(parts)
        for idx, part in enumerate(parts, start=1):
            title = "IA Staff" + (f" ({idx}/{total})" if total > 1 else "")
            emb = discord.Embed(title=title, description=part, color=discord.Color.teal())
            emb.set_footer(
                text=("Prompt Playground" if self.prompt_id else f"Modèle: {self.model}")
            )
            await ctx.send(embed=emb)

    # --------------- Commande principale ---------------

    @commands.command(name="iastaff", aliases=["staffia"])
    @commands.has_role(STAFF_ROLE_NAME)
    async def iastaff_cmd(self, ctx: commands.Context, *, message: str):
        """
        Staff‑only. Exemples:
        - !iastaff Donne la strat du Bouftou Royal
        - !iastaff reset   (efface la mémoire du salon)
        - !iastaff info    (affiche la config)
        """
        msg = (message or "").strip()
        channel_id = ctx.channel.id

        if not os.environ.get("OPENAI_API_KEY"):
            await ctx.reply(
                "❌ `OPENAI_API_KEY` n'est pas configurée sur l'hébergement. "
                "Ajoute la variable d'environnement puis redeploie.",
                mention_author=False
            )
            return

        # Petites sous-commandes
        low = msg.lower()
        if low in {"reset", "clear", "new"}:
            self.history.pop(channel_id, None)
            await ctx.reply("Historique du salon effacé ✅", mention_author=False)
            return

        if low in {"info", "config"}:
            t = (
                f"Prompt: `{self.prompt_id or '—'}`\n"
                f"Version: `{self.prompt_version if self.prompt_id else '—'}`\n"
                f"Model fallback: `{self.model}`\n"
                f"History (salon): {len(self.history.get(channel_id, []))} messages "
                f"(max {DEFAULT_HISTORY_TURNS*2})"
            )
            await ctx.reply(t, mention_author=False)
            return

        if not msg:
            await ctx.reply(
                "Donne un message après la commande, ex: `!iastaff Quel est le plan ?`",
                mention_author=False
            )
            return

        # Conversation: mémoire courante du salon
        lock = self._get_lock(channel_id)
        async with lock:
            # Contexte + nouveau message utilisateur
            context = self._render_history_as_context(channel_id)
            if context:
                final_input = f"{context}\n\nUtilisateur: {msg}\nAssistant:"
            else:
                final_input = msg

            async with ctx.typing():
                try:
                    text = await self._ask_openai(final_input)
                except Exception as e:
                    await ctx.reply(f"❌ {e}", mention_author=False)
                    return

            if not text.strip():
                # On évite le message ambigu – parfois la sortie est vide pour raisons de sûreté
                await ctx.reply(
                    "La réponse de l'IA est vide. Réessaie en reformulant (ou tape `!iastaff reset` pour repartir).",
                    mention_author=False
                )
                return

            # Mise à jour de la mémoire
            self._push_history(channel_id, "user", msg)
            self._push_history(channel_id, "assistant", text)

        # Envoi en chunks
        await self._send_long_reply(ctx, text)


async def setup(bot: commands.Bot):
    await bot.add_cog(IAStaff(bot))
