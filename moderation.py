#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import os
import re
import unicodedata

import discord
from discord.ext import commands

import ia
from utils.console_json_store import ConsoleJSONSnapshotStore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WARNINGS_FILE = os.path.join(BASE_DIR, "warnings_data.json")
STAFF_CHANNEL_NAME = "📚 Général-staff 📚"
STAFF_ROLE_NAME = "Staff"
SAVE_INTERVAL = 60
WARNINGS_MARKER = "===WARNINGS==="
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", "console")


def _strip_accents(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.warnings: dict[str, int] = {}
        self.patterns: dict[str, re.Pattern[str]] = {}
        self._dirty = False
        self._lock = asyncio.Lock()
        self._save_task: asyncio.Task | None = None
        self._init_task: asyncio.Task | None = None
        self.console_message_id: int | None = None
        self.store = ConsoleJSONSnapshotStore(
            bot,
            marker=WARNINGS_MARKER,
            filename="warnings_data.json",
            default_channel_name=CONSOLE_CHANNEL_NAME,
            history_limit_env="WARNINGS_HISTORY_LIMIT",
        )
        self._load_warnings_local()
        self._compile_patterns()

    async def cog_load(self):
        if hasattr(self.bot, "wait_until_ready"):
            if self._save_task is None or self._save_task.done():
                self._save_task = asyncio.create_task(self._periodic_save())
            if self._init_task is None or self._init_task.done():
                self._init_task = asyncio.create_task(self._post_ready_init())

    def cog_unload(self):
        for task in (self._save_task, self._init_task):
            if task and not task.done():
                task.cancel()

    async def _post_ready_init(self):
        await self.bot.wait_until_ready()
        message, payload = await self.store.load_latest(current_message_id=self.console_message_id)
        if isinstance(payload, dict):
            raw = payload.get("warnings") if "warnings" in payload else payload
            if isinstance(raw, dict):
                self.warnings = {str(k): int(v) for k, v in raw.items()}
                self.console_message_id = getattr(message, "id", None)

    async def _periodic_save(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(SAVE_INTERVAL)
            if self._dirty:
                await self._save_warnings()

    def _compile_patterns(self):
        base_sets = {
            "serious_insult": ia.SERIOUS_INSULT_KEYWORDS,
            "discrimination": ia.DISCRIMINATION_KEYWORDS,
            "threat": ia.THREAT_KEYWORDS,
        }
        for label, keyw in base_sets.items():
            esc = [re.escape(_strip_accents(k)).replace(r"\ ", r"\s+") for k in keyw if len(k) >= 3]
            if esc:
                self.patterns[label] = re.compile(r"(?<!\w)(?:" + ("|".join(esc)) + r")(?!\w)", re.I | re.S)
        amb_words = [re.escape(_strip_accents(k)).replace(r"\ ", r"\s+") for k in ia.AMBIGUOUS_INSULT_KEYWORDS if k]
        amb_qual = [re.escape(_strip_accents(k)).replace(r"\ ", r"\s+") for k in ia.AMBIGUOUS_INSULT_QUALIFIERS if k]
        if amb_words and amb_qual:
            qual_group = "(?:" + ("|".join(amb_qual)) + ")"
            word_group = "(?:" + ("|".join(amb_words)) + ")"
            pattern = r"(?<!\w)" + qual_group + r"(?:\s+\w+){0,2}?\s+" + word_group + r"(?!\w)"
            self.patterns["ambiguous_insult"] = re.compile(pattern, re.I | re.S)

    def _load_warnings_local(self):
        if os.path.isfile(WARNINGS_FILE):
            try:
                with open(WARNINGS_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    self.warnings = {str(k): int(v) for k, v in raw.items()}
            except Exception:
                self.warnings = {}

    async def _save_warnings(self):
        async with self._lock:
            try:
                with open(WARNINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.warnings, f, indent=2, ensure_ascii=False, sort_keys=True)
                message = await self.store.save({"warnings": self.warnings}, current_message_id=self.console_message_id)
                if message is not None:
                    self.console_message_id = message.id
                self._dirty = False
            except Exception:
                pass

    def _increment_warning(self, uid: str) -> int:
        self.warnings[uid] = self.warnings.get(uid, 0) + 1
        self._dirty = True
        return self.warnings[uid]

    def _clean(self, content: str) -> str:
        content = re.sub(r"```.*?```", "", content, flags=re.S)
        content = re.sub(r"https?://\S+", "", content)
        return _strip_accents(content)

    def _classify(self, msg: str) -> str | None:
        msg = self._clean(msg)
        for label, pat in self.patterns.items():
            if pat.search(msg):
                return label
        return None

    @commands.Cog.listener("on_message")
    async def _listener(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if discord.utils.get(message.author.roles, name=STAFF_ROLE_NAME):
            return
        cat = self._classify(message.content)
        if not cat:
            return
        try:
            await message.delete()
        except discord.DiscordException:
            pass
        count = self._increment_warning(str(message.author.id))
        await self._save_warnings()
        try:
            await message.author.send("⚠️ Votre message a été supprimé car il enfreint le règlement du serveur.")
        except discord.Forbidden:
            pass
        staff_ch = discord.utils.get(message.guild.text_channels, name=STAFF_CHANNEL_NAME)
        if staff_ch:
            await staff_ch.send(
                f"Infraction {cat} par {message.author.mention} (avertissement {count})\n> {message.content}"
            )

    @commands.has_role(STAFF_ROLE_NAME)
    @commands.command(name="warnings")
    async def _cmd_warnings(self, ctx: commands.Context, member: discord.Member):
        await ctx.send(f"{member.display_name} a {self.warnings.get(str(member.id), 0)} avertissement(s).")

    @commands.has_role(STAFF_ROLE_NAME)
    @commands.command(name="resetwarnings")
    async def _cmd_reset(self, ctx: commands.Context, member: discord.Member):
        if str(member.id) in self.warnings:
            del self.warnings[str(member.id)]
            self._dirty = True
            await self._save_warnings()
        await ctx.send(f"Les avertissements de {member.display_name} ont été réinitialisés.")


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
