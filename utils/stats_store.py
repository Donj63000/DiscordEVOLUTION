#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import tempfile
import os
import discord

log = logging.getLogger("utils.stats_store")

class StatsStore:
    def __init__(self, bot, channel_name="console"):
        self.bot = bot
        self.channel_name = channel_name
        self._msg = None

    async def _get_channel(self):
        for g in self.bot.guilds:
            ch = discord.utils.get(g.text_channels, name=self.channel_name)
            if ch:
                return ch
        return None

    async def save(self, data):
        chan = await self._get_channel()
        if not chan:
            log.warning("Canal #console introuvable – persistance désactivée")
            return
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        content = f"```json\n{payload}\n```"
        if len(content) <= 2000:
            try:
                if self._msg:
                    await self._msg.edit(content=content)
                else:
                    self._msg = await chan.send(content)
            except:
                self._msg = await chan.send(content)
            return
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            if self._msg:
                try:
                    await self._msg.delete()
                except:
                    pass
                self._msg = None
            msg = await chan.send("===BOTSTATS=== (fichier)", file=discord.File(path, filename="stats_data.json"))
            self._msg = msg
        finally:
            try:
                os.remove(path)
            except:
                pass
