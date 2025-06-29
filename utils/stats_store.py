import json
import logging
import discord

log = logging.getLogger(__name__)
CODEBLOCK = "```stats"


class StatsStore:
    """Persist stats data in a pinned message inside #console."""

    def __init__(self, bot: discord.Client, file_path: str, channel_name: str = "console"):
        self.bot = bot
        self.file_path = file_path
        self.channel_name = channel_name
        self._msg: discord.Message | None = None

    async def _channel(self) -> discord.TextChannel | None:
        chan = discord.utils.get(self.bot.get_all_channels(), name=self.channel_name)
        if chan is None:
            log.warning("Canal #%s introuvable – persistance désactivée", self.channel_name)
        return chan

    async def load(self) -> dict | None:
        chan = await self._channel()
        if chan is None:
            return None
        async for msg in chan.history(limit=200):
            if msg.author == self.bot.user and msg.content.startswith(CODEBLOCK):
                try:
                    data = json.loads(msg.content[len(CODEBLOCK):].strip("` \n"))
                    self._msg = msg
                    return data
                except Exception:
                    log.warning("Message stats mal formé (id=%s)", msg.id)
        return None

    async def save(self, data: dict) -> None:
        chan = await self._channel()
        if chan is None:
            return
        json_block = f"{CODEBLOCK}\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```"
        if self._msg is None:
            self._msg = await chan.send(json_block)
            try:
                await self._msg.pin(reason="Persistance statistiques")
            except discord.Forbidden:
                log.warning("Impossible d'épingler le message #console (permissions).")
        else:
            try:
                await self._msg.edit(content=json_block)
            except discord.NotFound:
                self._msg = None
                return await self.save(data)
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.warning("Erreur sauvegarde fichier stats: %s", e)
