#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import discord
from discord.ext import commands

try:
    # Evite un crash si la lib n'est pas importable: on retarde l'erreur à l'appel
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover
    AsyncOpenAI = None  # sera vérifié à l'exécution

log = logging.getLogger("iastaff")

STAFF_ROLE_NAME = "Staff"
DEFAULT_MODEL = "gpt-5-nano"
DEFAULT_PROMPT_ID = "pmpt_689900255180819686efd4ca8cebfc7706a0776e4dbf2240"
DEFAULT_PROMPT_VERSION = "2"


def chunk(text: str, limit: int = 3900):
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            parts.append(buf)
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf:
        parts.append(buf)
    return parts or [""]


class IAStaff(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.model = os.getenv("OPENAI_STAFF_MODEL", DEFAULT_MODEL)
        self.prompt_id = os.getenv("OPENAI_STAFF_PROMPT_ID", DEFAULT_PROMPT_ID)
        self.prompt_version = os.getenv("OPENAI_STAFF_PROMPT_VERSION", DEFAULT_PROMPT_VERSION)
        api_key = os.environ.get("OPENAI_API_KEY")

        if AsyncOpenAI is None:
            raise RuntimeError("La librairie 'openai' n'est pas disponible. Ajoute 'openai' dans requirements.txt.")

        if not api_key:
            # On ne plante pas l'import: on laisse un message clair à la 1ère utilisation
            log.warning("OPENAI_API_KEY manquante: !iastaff répondra avec une erreur tant que la clé n'est pas définie.")
        self.client = AsyncOpenAI(api_key=api_key)

    async def cog_load(self):
        log.info("IAStaff prêt (modèle=%s, prompt_id=%s)", self.model, self.prompt_id)

    def _build_request(self, user_text: str):
        if self.prompt_id:
            return {"prompt": {"id": self.prompt_id, "version": self.prompt_version}, "input": user_text}
        return {"model": self.model, "input": user_text}

    @commands.command(name="iastaff", aliases=["staffia"])
    @commands.has_role(STAFF_ROLE_NAME)
    async def cmd_iastaff(self, ctx: commands.Context, *, prompt: str):
        if not prompt.strip():
            await ctx.reply("Donne une question après la commande, ex: `!iastaff Quel est le plan ?`",
                            mention_author=False)
            return

        if not os.environ.get("OPENAI_API_KEY"):
            await ctx.reply("❌ `OPENAI_API_KEY` n'est pas configurée sur Render. Ajoute-la puis redeploie.",
                            mention_author=False)
            return

        async with ctx.typing():
            try:
                req = self._build_request(prompt)
                resp = await self.client.responses.create(**req)
                text = getattr(resp, "output_text", None) or ""
            except Exception as e:
                await ctx.reply(f"❌ Erreur API OpenAI: {e}", mention_author=False)
                return

        if not text.strip():
            await ctx.reply("Je n’ai rien reçu de l’API.", mention_author=False)
            return

        for i, part in enumerate(chunk(text), 1):
            title = "IA Staff" + (f" ({i}/{len(chunk(text))})" if i > 1 else "")
            emb = discord.Embed(title=title, description=part, color=discord.Color.teal())
            emb.set_footer(text=("Prompt Playground" if self.prompt_id else f"Modèle: {self.model}"))
            await ctx.send(embed=emb)


async def setup(bot: commands.Bot):
    await bot.add_cog(IAStaff(bot))
