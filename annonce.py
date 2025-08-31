#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import discord
from discord.ext import commands

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")
ANNONCE_CHANNEL = "📣 annonces 📣"
DEFAULT_MODEL = os.getenv("OPENAI_STAFF_MODEL", "gpt-5")
OPENAI_TIMEOUT = float(os.getenv("IASTAFF_TIMEOUT", "120"))
MAX_OUTPUT_TOKENS = int(os.getenv("IASTAFF_MAX_OUTPUT_TOKENS", "1800"))
DM_TIMEOUT = int(os.getenv("ANNONCE_DM_TIMEOUT", "300"))


def extract_generated_text(resp_obj) -> str:
    if isinstance(resp_obj, str):
        return resp_obj
    txt = ""
    try:
        data = resp_obj
        if hasattr(resp_obj, "to_dict"):
            data = resp_obj.to_dict()
        elif hasattr(resp_obj, "dict"):
            data = resp_obj.dict()
        if isinstance(data, dict):
            if "content" in data:
                parts = data.get("content")
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, dict):
                            t = part.get("text") or part.get("content")
                            if isinstance(t, str):
                                txt += t
                elif isinstance(data.get("content"), str):
                    txt = data["content"]
            elif isinstance(data.get("data"), list):
                for item in data["data"]:
                    txt += extract_generated_text(item)
    except Exception:
        pass
    return txt.strip()


class AnnonceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.model = DEFAULT_MODEL
        self.client = AsyncOpenAI(timeout=OPENAI_TIMEOUT) if AsyncOpenAI else None

    @commands.command(name="annoncestaff", aliases=["*annonce"])
    @commands.has_role(STAFF_ROLE_NAME)
    async def annonce_cmd(self, ctx: commands.Context):
        if not self.client or not os.environ.get("OPENAI_API_KEY"):
            await ctx.reply(
                "❌ `OPENAI_API_KEY` n'est pas configurée sur l'hébergement. Ajoute la variable puis redeploie.",
                mention_author=False,
            )
            return

        dm = await ctx.author.create_dm()
        await ctx.reply("📨 Je t'ai envoyé un DM pour préparer l'annonce.", mention_author=False)

        system_prompt = (
            "Tu es EvolutionBOT, assistant du staff de la guilde Évolution. "
            "Pose successivement 7 questions brèves pour préparer une annonce. "
            "Après la 7e réponse, rédige l'annonce finale en français, polie, stylée, "
            "et commence par '@everyone'."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Commence par la première question."},
        ]
        try:
            resp = await self.client.responses.create(
                model=self.model, messages=messages, max_output_tokens=MAX_OUTPUT_TOKENS
            )
            text = extract_generated_text(resp)
        except Exception as e:
            await dm.send(f"Erreur IA initiale : {e}")
            return

        await dm.send(text or "(aucune question)")

        answers = 0
        while answers < 7:
            try:
                user_msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == dm,
                    timeout=DM_TIMEOUT,
                )
            except asyncio.TimeoutError:
                await dm.send("⏰ Temps écoulé, annulation.")
                return
            messages.append({"role": "user", "content": user_msg.content})
            try:
                resp = await self.client.responses.create(
                    model=self.model, messages=messages, max_output_tokens=MAX_OUTPUT_TOKENS
                )
                text = extract_generated_text(resp)
            except Exception as e:
                await dm.send(f"Erreur IA : {e}")
                return
            answers += 1
            if answers < 7:
                await dm.send(text or "(aucune question)")
            else:
                final_announce = text.strip()
                break

        channel = discord.utils.get(ctx.guild.text_channels, name=ANNONCE_CHANNEL)
        if not channel:
            await dm.send(f"❌ Canal '{ANNONCE_CHANNEL}' introuvable.")
            return
        await channel.send(final_announce or "Annonce vide.")
        await dm.send("✅ Annonce publiée dans #📣 annonces 📣.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AnnonceCog(bot))
