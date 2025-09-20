#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from typing import Optional

import discord

log = logging.getLogger("utils.stats_store")

CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(?P<body>.+?)```", re.DOTALL)


def _json_digest(obj) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


class StatsStore:
    def __init__(self, bot, channel_name: str = "console"):
        self.bot = bot
        self.channel_name = channel_name
        self._msg: discord.Message | None = None
        self._etag: str | None = None
        self._last_save: float = 0.0
        self.min_interval = int(os.getenv("STATS_MIN_INTERVAL", "900"))

    async def _get_channel(self):
        chan = discord.utils.get(self.bot.get_all_channels(), name=self.channel_name)
        if not chan:
            log.warning("Canal #%s introuvable – persistance désactivée", self.channel_name)
        return chan

    async def save(self, data) -> bool:
        chan = await self._get_channel()
        if not chan:
            return False
        digest = _json_digest(data)
        now = time.time()
        if self._etag == digest and (now - self._last_save) < self.min_interval:
            return True
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        content = f"```json\n{payload}\n```"
        if len(content) <= 2000:
            try:
                if self._msg:
                    await self._msg.edit(content=content)
                else:
                    self._msg = await chan.send(content)
            except Exception:
                try:
                    self._msg = await chan.send(content)
                except Exception:
                    log.exception("Impossible d'enregistrer les stats dans #%s", self.channel_name)
                    return False
            self._etag = digest
            self._last_save = now
            return True
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            try:
                if self._msg:
                    try:
                        await self._msg.edit(
                            content="===BOTSTATS=== (fichier)",
                            attachments=[discord.File(path, filename="stats_data.json")],
                        )
                    except Exception:
                        try:
                            await self._msg.delete()
                        except Exception:
                            pass
                        self._msg = await chan.send(
                            "===BOTSTATS=== (fichier)",
                            file=discord.File(path, filename="stats_data.json"),
                        )
                else:
                    self._msg = await chan.send(
                        "===BOTSTATS=== (fichier)",
                        file=discord.File(path, filename="stats_data.json"),
                    )
            except Exception:
                log.exception("Impossible d'envoyer le fichier de stats dans #%s", self.channel_name)
                return False
        finally:
            try:
                os.remove(path)
            except:
                pass
        self._etag = digest
        self._last_save = now
        return True

    async def load(self) -> Optional[dict]:
        chan = await self._get_channel()
        if not chan:
            return None

        checked: set[int] = set()

        async def iter_candidates():
            try:
                for msg in await chan.pins():
                    mid = getattr(msg, "id", id(msg))
                    if mid in checked:
                        continue
                    checked.add(mid)
                    yield msg
            except Exception:
                log.debug("Aucun pin exploitable pour #%s", self.channel_name)
            async for msg in chan.history(limit=50):
                mid = getattr(msg, "id", id(msg))
                if mid in checked:
                    continue
                checked.add(mid)
                yield msg

        async for msg in iter_candidates():
            data = await self._extract_payload(msg)
            if data is not None:
                self._msg = msg
                self._etag = _json_digest(data)
                return data
        return None

    async def _extract_payload(self, msg) -> Optional[dict]:
        content = getattr(msg, "content", "") or ""
        bot_user = getattr(self.bot, "user", None)
        is_bot_message = bot_user is None or getattr(msg, "author", None) == bot_user
        if not is_bot_message and "===BOTSTATS===" not in content:
            return None

        # Attachments first
        for att in getattr(msg, "attachments", []) or []:
            filename = getattr(att, "filename", "")
            if not filename or not filename.endswith(".json"):
                continue
            try:
                raw = await att.read()
                return json.loads(raw.decode("utf-8"))
            except Exception:
                log.warning("Lecture JSON impossible depuis %s", filename, exc_info=True)

        if not content:
            return None
        match = CODE_BLOCK_RE.search(content.strip())
        if not match:
            return None
        body = match.group("body").strip()
        try:
            return json.loads(body)
        except Exception:
            log.warning("Contenu JSON invalide dans le message de stats", exc_info=True)
            return None
