#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import datetime
import re
import asyncio
import unicodedata
import discord
from discord.ext import commands
import ia

WARNINGS_FILE = os.path.join(os.path.dirname(__file__), "warnings_data.json")
STAFF_CHANNEL_NAME = "𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥-staff"
STAFF_ROLE_NAME = "Staff"
TIMEOUT_THRESHOLD = 2
TIMEOUT_DURATION = datetime.timedelta(hours=1)
SAVE_INTERVAL = 60

def _strip_accents(t:str)->str:
    return ''.join(c for c in unicodedata.normalize("NFD",t) if unicodedata.category(c)!="Mn")

class ModerationCog(commands.Cog):
    def __init__(self,bot:commands.Bot):
        self.bot=bot
        self.warnings:dict[str,int]={}
        self.patterns:dict[str,re.Pattern[str]]={}
        self._dirty=False
        self._lock=asyncio.Lock()
        self._load_warnings()
        self._compile_patterns()
        asyncio.create_task(self._periodic_save())

    async def _periodic_save(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(SAVE_INTERVAL)
            if self._dirty:
                await self._save_warnings()

    def _compile_patterns(self):
        sets={"serious_insult":ia.SERIOUS_INSULT_KEYWORDS,"discrimination":ia.DISCRIMINATION_KEYWORDS,"threat":ia.THREAT_KEYWORDS}
        for label,keyw in sets.items():
            esc=[re.escape(_strip_accents(k)).replace(r'\ ',r'\s+') for k in keyw if len(k)>=3]
            self.patterns[label]=re.compile(r'(?<!\w)(?:'+('|'.join(esc))+r')(?!\w)',re.I|re.S)

    def _load_warnings(self):
        if os.path.isfile(WARNINGS_FILE):
            try:
                with open(WARNINGS_FILE,"r",encoding="utf-8") as f:
                    self.warnings=json.load(f)
            except Exception:
                self.warnings={}

    async def _save_warnings(self):
        async with self._lock:
            try:
                with open(WARNINGS_FILE,"w",encoding="utf-8") as f:
                    json.dump(self.warnings,f,indent=2,ensure_ascii=False)
                self._dirty=False
            except Exception:
                pass

    def _increment_warning(self,uid:str)->int:
        self.warnings[uid]=self.warnings.get(uid,0)+1
        self._dirty=True
        return self.warnings[uid]

    def _clean(self,content:str)->str:
        content=re.sub(r'```.*?```','',content,flags=re.S)
        content=re.sub(r'https?://\S+','',content)
        return _strip_accents(content)

    def _classify(self,msg:str)->str|None:
        msg=self._clean(msg)
        for label,pat in self.patterns.items():
            if pat.search(msg):
                return label
        return None

    async def _timeout(self,member:discord.Member)->bool:
        until=datetime.datetime.now(datetime.timezone.utc)+TIMEOUT_DURATION
        try:
            await member.edit(timed_out_until=until,reason="Avertissements multiples")
            return True
        except Exception:
            try:
                await member.timeout(until,reason="Avertissements multiples")
                return True
            except Exception:
                return False

    @commands.Cog.listener("on_message")
    async def _listener(self,message:discord.Message):
        if not message.guild or message.author.bot:
            return
        if discord.utils.get(message.author.roles,name=STAFF_ROLE_NAME):
            return
        cat=self._classify(message.content)
        if not cat:
            return
        try:
            await message.delete()
        except discord.DiscordException:
            pass
        count=self._increment_warning(str(message.author.id))
        try:
            await message.author.send("⚠️ Votre message a été supprimé car il enfreint le règlement du serveur.")
        except discord.Forbidden:
            pass
        staff_ch=discord.utils.get(message.guild.text_channels,name=STAFF_CHANNEL_NAME)
        if staff_ch:
            await staff_ch.send(f"Infraction {cat} par {message.author.mention} (avertissement {count}/{TIMEOUT_THRESHOLD})\n> {message.content}")
        if count>=TIMEOUT_THRESHOLD:
            if await self._timeout(message.author) and staff_ch:
                await staff_ch.send(f"{message.author.mention} a été mute {TIMEOUT_DURATION} après plusieurs avertissements.")

    @commands.has_role(STAFF_ROLE_NAME)
    @commands.command(name="warnings")
    async def _cmd_warnings(self,ctx:commands.Context,member:discord.Member):
        await ctx.send(f"{member.display_name} a {self.warnings.get(str(member.id),0)} avertissement(s).")

    @commands.has_role(STAFF_ROLE_NAME)
    @commands.command(name="resetwarnings")
    async def _cmd_reset(self,ctx:commands.Context,member:discord.Member):
        if str(member.id) in self.warnings:
            del self.warnings[str(member.id)]
            self._dirty=True
        await ctx.send(f"Les avertissements de {member.display_name} ont été réinitialisés.")

async def setup(bot:commands.Bot):
    await bot.add_cog(ModerationCog(bot))
