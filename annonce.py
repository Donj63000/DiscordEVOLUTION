#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import discord
from discord.ext import commands
from utils.openai_config import resolve_staff_model, build_async_openai_client

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")
ANNONCE_CHANNEL = os.getenv("ANNONCE_CHANNEL_NAME", "organisation")

DEFAULT_MODEL = resolve_staff_model()

OPENAI_TIMEOUT = float(os.getenv("IASTAFF_TIMEOUT", "120"))
MAX_OUTPUT_TOKENS = int(os.getenv("IASTAFF_MAX_OUTPUT_TOKENS", "1800"))
DM_TIMEOUT = int(os.getenv("ANNONCE_DM_TIMEOUT", "300"))


def extract_generated_text(resp_obj) -> str:
    if not resp_obj:
        return ""
    if isinstance(resp_obj, str):
        return resp_obj.strip()

    direct = getattr(resp_obj, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    def _gather_from_output(output) -> list[str]:
        chunks: list[str] = []
        if not output:
            return chunks
        for item in output:
            content_list = getattr(item, "content", None)
            if content_list is None and isinstance(item, dict):
                content_list = item.get("content")
            if not content_list:
                continue
            for content in content_list:
                if content is None:
                    continue
                ctype = getattr(content, "type", "") or (content.get("type") if isinstance(content, dict) else "")
                if ctype in {"text", "output_text"}:
                    candidate = getattr(content, "text", None)
                    if candidate is None and isinstance(content, dict):
                        candidate = content.get("text") or content.get("content")
                    if isinstance(candidate, str):
                        chunks.append(candidate)
                elif ctype in {"json", "json_schema"}:
                    candidate = getattr(content, "json", None) or getattr(content, "json_schema", None)
                    if candidate is None and isinstance(content, dict):
                        candidate = content.get("json") or content.get("json_schema")
                    if isinstance(candidate, str):
                        chunks.append(candidate)
        return chunks

    pieces = []
    pieces.extend(_gather_from_output(getattr(resp_obj, "output", None)))

    data = resp_obj
    if hasattr(resp_obj, "to_dict"):
        data = resp_obj.to_dict()
    elif hasattr(resp_obj, "dict"):
        data = resp_obj.dict()

    if isinstance(data, dict):
        pieces.extend(_gather_from_output(data.get("output")))
        if not pieces and isinstance(data.get("content"), list):
            for part in data["content"]:
                if isinstance(part, dict):
                    maybe_text = part.get("text") or part.get("content")
                    if isinstance(maybe_text, str):
                        pieces.append(maybe_text)
        if not pieces and isinstance(data.get("data"), list):
            for item in data["data"]:
                nested = extract_generated_text(item)
                if nested:
                    pieces.append(nested)

    if not pieces:
        content_attr = getattr(resp_obj, "content", None)
        if isinstance(content_attr, list):
            for part in content_attr:
                if isinstance(part, dict):
                    maybe_text = part.get("text") or part.get("content")
                    if isinstance(maybe_text, str):
                        pieces.append(maybe_text)

    if not pieces:
        return ""
    return "".join(pieces).strip()




QUESTIONS = [
    "Quel est le sujet principal de l'annonce ?",
    "Quels sont les détails importants à inclure (dates, heures, lieu, lien, etc.) ?",
    "Quel est le public visé (tous les membres, un rôle précis, nouveau joueur, etc.) ?",
    "Quel ton souhaites-tu adopter (enthousiaste, sérieux, professionnel, motivant, etc.) ?",
    "Y a-t-il un appel à l'action ou des instructions à transmettre ?",
    "Faut-il mentionner des récompenses, avantages ou conséquences ?",
    "Autre chose à ajouter pour aider à rédiger l'annonce parfaite ?",
]


class AnnonceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.model = DEFAULT_MODEL
        self.client = build_async_openai_client(AsyncOpenAI, timeout=OPENAI_TIMEOUT)

    def _build_prompt(self, answers: list[str], author: discord.Member) -> list[dict]:
        staff_context = [
            f"Préparé par : {author.display_name} (ID: {author.id})",
        ]
        for question, answer in zip(QUESTIONS, answers):
            staff_context.append(f"- {question}\n  Réponse : {answer.strip() or '(aucune précision)'}")

        user_prompt = (
            "Tu dois rédiger une annonce Discord claire, engageante et professionnelle pour la guilde Évolution.\n"
            "Commence impérativement par '@everyone'.\n"
            "Structure l'annonce avec des paragraphes courts et, si pertinent, des listes à puces.\n"
            "Reste en français, conserve le ton souhaité et assure-toi que l'annonce est prête à être publiée sans ajout supplémentaire.\n"
            "Voici les informations fournies par le membre du staff:\n"
            + "\n".join(staff_context)
        )

        return [
            {
                "role": "system",
                "content": (
                    "Tu es EvolutionBOT, l'assistant du staff Discord Évolution. "
                    "Ton rôle est de transformer les informations en annonces impeccables et compréhensibles pour la communauté."
                ),
            },
            {"role": "user", "content": user_prompt},
        ]

    def _find_announcement_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        target_raw = (ANNONCE_CHANNEL or "").strip()
        if not target_raw:
            return None

        def _channel_id_from(value: str) -> int | None:
            value = (value or "").strip()
            if value.startswith("<#") and value.endswith(">"):
                value = value[2:-1]
            return int(value) if value.isdigit() else None

        def _normalize(value: str) -> str:
            value = (value or "").strip().casefold()
            cleaned = []
            for ch in value:
                if ch in {" ", "-", "_"}:
                    continue
                cleaned.append(ch)
            return "".join(cleaned)

        target_id = _channel_id_from(target_raw)
        if target_id is not None:
            channel = getattr(guild, "get_channel", lambda _id: None)(target_id)
            if channel is not None:
                return channel

        target_norm = _normalize(target_raw)
        target_cf = target_raw.casefold()

        for channel in guild.text_channels:
            name_cf = channel.name.casefold()
            if name_cf == target_cf:
                return channel

        for channel in guild.text_channels:
            name_norm = _normalize(channel.name)
            if name_norm == target_norm:
                return channel

        if target_norm:
            for channel in guild.text_channels:
                if target_norm in _normalize(channel.name):
                    return channel

        for channel in guild.text_channels:
            if target_cf in channel.name.casefold():
                return channel

        return None

    @commands.command(name="annonce", aliases=["annoncestaff", "*annonce", "annonces"])
    @commands.has_role(STAFF_ROLE_NAME)
    async def annonce_cmd(self, ctx: commands.Context):
        if not self.client or not os.environ.get("OPENAI_API_KEY"):
            await ctx.reply(
                "❌ `OPENAI_API_KEY` n'est pas configurée sur l'hébergement. Ajoute la variable puis redeploie.",
                mention_author=False,
            )
            return

        dm = await ctx.author.create_dm()
        await ctx.reply("📨 Je t'ai envoyé un DM pour préparer ton annonce.", mention_author=False)

        await dm.send(
            "Salut ! Réponds aux questions qui suivent. Tu peux écrire `annule` à tout moment pour arrêter."
        )

        answers: list[str] = []
        for question in QUESTIONS:
            await dm.send(question)
            try:
                reply = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == dm,
                    timeout=DM_TIMEOUT,
                )
            except asyncio.TimeoutError:
                await dm.send("⏰ Temps écoulé, opération annulée.")
                return

            content = reply.content.strip()
            if content.lower() == "annule":
                await dm.send("🚫 Annonce annulée à ta demande.")
                return

            answers.append(content)

        messages = self._build_prompt(answers, ctx.author)

        try:
            response = await self.client.responses.create(
                model=self.model,
                input=messages,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
        except Exception as e:
            await dm.send(f"❌ Erreur lors de la génération de l'annonce : {e}")
            return

        final_announce = extract_generated_text(response).strip()
        if not final_announce:
            await dm.send("❌ L'IA n'a pas réussi à générer d'annonce.")
            return

        if not final_announce.lower().startswith("@everyone"):
            final_announce = "@everyone " + final_announce

        channel = self._find_announcement_channel(ctx.guild)
        if not channel:
            await dm.send(
                f"❌ Canal d'annonces introuvable. Vérifie la variable `ANNONCE_CHANNEL_NAME` (valeur actuelle : {ANNONCE_CHANNEL!r})."
            )
            return

        await channel.send(final_announce)
        await dm.send(f"✅ Annonce publiée dans #{channel.name}.")


async def setup(bot: commands.Bot):
    # Remplace l'ancienne commande !annonce pour éviter les doubles enregistrements.
    bot.remove_command("annonce")
    await bot.add_cog(AnnonceCog(bot))
