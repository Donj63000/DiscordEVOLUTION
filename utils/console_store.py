from __future__ import annotations
import json
import logging
import os
import discord

from utils.channel_resolver import resolve_text_channel
from utils.discord_history import fetch_channel_history
from utils.guild_scope import allowed_guilds, require_guild, is_allowed_guild

log = logging.getLogger(__name__)
CODEBLOCK = "```event"


def _read_bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


class ConsoleStore:
    """Petite « base » : chaque événement est sauvegardé dans #console."""

    def __init__(self, bot: discord.Client, channel_name: str = "console", pin_messages: bool | None = None):
        self.bot = bot
        self.channel_name = channel_name
        self.pin_messages = (
            _read_bool_env("CONSOLE_PIN_EVENTS", _read_bool_env("CONSOLE_PIN_SNAPSHOTS", False))
            if pin_messages is None
            else pin_messages
        )
        self._cache: dict[int, dict] = {}          # event_id -> dict enrichi + _msg

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    async def _channel(self) -> discord.TextChannel | None:
        for guild in allowed_guilds(self.bot):
            chan = resolve_text_channel(
                guild,
                id_env="CHANNEL_CONSOLE_ID",
                name_env="CHANNEL_CONSOLE",
                default_name=self.channel_name,
            )
            if chan is not None:
                return chan
        log.warning("Canal #%s introuvable – persistance désactivée", self.channel_name)
        return None

    def _serialisable(self, data: dict) -> str:
        """Renvoie la chaîne JSON sans les clés transient (_msg…)."""
        payload = {k: v for k, v in data.items() if not k.startswith("_")}
        return json.dumps(payload, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # Lecture                                                            #
    # ------------------------------------------------------------------ #
    async def load_all(self) -> dict[int, dict]:
        if self._cache:
            for record in self._cache.values():
                require_guild(record.get("guild_id"), self.bot)
            return self._cache
        chan = await self._channel()
        if chan is None:
            return self._cache
        require_guild(getattr(chan, "guild", None), self.bot)

        messages = await fetch_channel_history(chan, limit=200, reason="console_store.load")
        for msg in messages:
            if (getattr(msg.author, "id", None) == getattr(self.bot.user, "id", None)
                    and getattr(self.bot.user, "id", None) is not None
                    and getattr(msg, "webhook_id", None) is None
                    and msg.content.startswith(CODEBLOCK)):
                try:
                    payload = json.loads(msg.content[len(CODEBLOCK):].strip("` \n"))
                    if not isinstance(payload, dict):
                        continue
                    payload.setdefault("guild_id", chan.guild.id)
                    if not is_allowed_guild(payload.get("guild_id"), self.bot):
                        continue
                    payload["_msg"] = msg                       # attache le Message
                    eid = int(payload["event_id"])
                    if eid > 0:
                        # History is newest first: old records never supersede it.
                        self._cache.setdefault(eid, payload)
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
        require_guild(data.get("guild_id"), self.bot)
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
                raise RuntimeError("Console des événements indisponible : sauvegarde refusée.")
            msg = await chan.send(json_block)
            if self.pin_messages:
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
            require_guild(cache[event_id].get("guild_id"), self.bot)
            try:
                await cache[event_id]["_msg"].delete()
            except discord.HTTPException:
                pass
            cache.pop(event_id, None)
