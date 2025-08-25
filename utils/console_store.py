from __future__ import annotations
import json
import logging
import discord
from copy import deepcopy

log = logging.getLogger(__name__)
CODEBLOCK = "```event"


class ConsoleStore:
    """Petite « base » : chaque événement est sauvegardé dans #🎮 console 🎮."""

    def __init__(self, bot: discord.Client, channel_name: str = "🎮 console 🎮"):
        self.bot = bot
        self.channel_name = channel_name
        self._cache: dict[int, dict] = {}          # event_id -> dict enrichi + _msg

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    async def _channel(self) -> discord.TextChannel | None:
        chan = discord.utils.get(self.bot.get_all_channels(), name=self.channel_name)
        if chan is None:
            log.warning("Canal #%s introuvable – persistance désactivée", self.channel_name)
        return chan

    def _serialisable(self, data: dict) -> str:
        """Renvoie la chaîne JSON sans les clés transient (_msg…)."""
        payload = {k: v for k, v in data.items() if not k.startswith("_")}
        return json.dumps(payload, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # Lecture                                                            #
    # ------------------------------------------------------------------ #
    async def load_all(self) -> dict[int, dict]:
        if self._cache:
            return self._cache
        chan = await self._channel()
        if chan is None:
            return self._cache

        async for msg in chan.history(limit=200):
            if msg.content.startswith(CODEBLOCK):
                try:
                    payload = json.loads(msg.content[len(CODEBLOCK):].strip("` \n"))
                    payload["_msg"] = msg                       # attache le Message
                    self._cache[payload["event_id"]] = payload
                except Exception:
                    log.warning("Message #%s mal formé (id=%s)", self.channel_name, msg.id)
        return self._cache

    # ------------------------------------------------------------------ #
    # Création / mise à jour                                             #
    # ------------------------------------------------------------------ #
    async def upsert(self, data: dict) -> None:
        """
        Crée ou met à jour le message épinglé correspondant à event_id.

        *data* peut contenir des objets non‑sérialisables (ex. discord.Message) ;
        ils sont retirés avant l'appel à json.dumps().
        """
        cache = await self.load_all()
        eid = data["event_id"]

        # Prépare la chaîne JSON sans les clefs internes
        json_block = f"{CODEBLOCK}\n{self._serialisable(data)}\n```"

        if eid in cache:                        # mise à jour
            msg: discord.Message = cache[eid]["_msg"]
            try:
                await msg.edit(content=json_block)
            except discord.NotFound:            # message supprimé manuellement
                cache.pop(eid, None)
                return await self.upsert(data)  # ré‑essaye en mode création
            cache[eid].update(data)
        else:                                   # création
            chan = await self._channel()
            if chan is None:
                return                          # persistance désactivée
            msg = await chan.send(json_block)
            try:
                await msg.pin(reason="Persistance événements")
            except discord.Forbidden:
                log.warning("Impossible d'épingler le message #%s (permissions).", self.channel_name)
            data["_msg"] = msg
            cache[eid] = data

    # ------------------------------------------------------------------ #
    # Suppression                                                        #
    # ------------------------------------------------------------------ #
    async def delete(self, event_id: int) -> None:
        cache = await self.load_all()
        if event_id in cache:
            try:
                await cache[event_id]["_msg"].delete()
            except discord.HTTPException:
                pass
            cache.pop(event_id, None)
