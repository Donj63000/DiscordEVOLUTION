from __future__ import annotations
import json
import logging
import discord

log = logging.getLogger(__name__)

CODEBLOCK = "```event"  # signature pour repérer vos messages


class ConsoleStore:
    """Stocke et relit les données d'événement via des messages #console."""

    def __init__(self, bot: discord.Client, channel_name: str = "console"):
        self.bot = bot
        self.channel_name = channel_name
        self._cache: dict[int, dict] = {}

    # -- helpers ----------------------------------------------------------- #

    async def _channel(self) -> discord.TextChannel:
        chan = discord.utils.get(self.bot.get_all_channels(), name=self.channel_name)
        if chan is None:
            raise RuntimeError(f"Canal #{self.channel_name} introuvable.")
        return chan  # type: ignore[return-value]

    # -- lecture ----------------------------------------------------------- #

    async def load_all(self) -> dict[int, dict]:
        """Charge tous les événements futurs stockés dans #console."""
        if self._cache:
            return self._cache  # already cached
        chan = await self._channel()
        async for msg in chan.history(limit=200):
            if msg.content.startswith(CODEBLOCK):
                try:
                    data = json.loads(msg.content[len(CODEBLOCK):].strip("` \n"))
                    data["_msg"] = msg
                    self._cache[data["event_id"]] = data
                except Exception:
                    log.warning("Message #console mal formé (id=%s)", msg.id)
        return self._cache

    # -- écriture / mise à jour ------------------------------------------- #

    async def upsert(self, data: dict) -> None:
        """Crée ou met à jour le message pin contenant *data*."""
        cache = await self.load_all()
        eid = data["event_id"]
        if eid in cache:
            msg: discord.Message = cache[eid]["_msg"]
            await msg.edit(content=f"{CODEBLOCK}\n{json.dumps(data, indent=2)}\n```")
            cache[eid].update(data)
        else:
            chan = await self._channel()
            msg = await chan.send(f"{CODEBLOCK}\n{json.dumps(data, indent=2)}\n```")
            await msg.pin(reason="Persistance événements")
            data["_msg"] = msg
            cache[eid] = data

    # -- suppression ------------------------------------------------------- #

    async def delete(self, event_id: int) -> None:
        cache = await self.load_all()
        if event_id in cache:
            try:
                await cache[event_id]["_msg"].delete()
            except discord.HTTPException:
                pass
            cache.pop(event_id, None)
