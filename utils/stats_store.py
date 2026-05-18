from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Optional

import discord

from utils.console_json_store import ConsoleJSONSnapshotStore

log = logging.getLogger(__name__)

STATS_MARKER = "===BOTSTATS==="
STATS_FILENAME = "stats_data.json"


def _json_digest(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(body.encode("utf-8")).hexdigest()


def _read_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _read_bool_env(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


class StatsStore:
    """
    Persistance des statistiques dans #console.

    Points importants :
    - ne lit que les messages marqués ===BOTSTATS=== ou l'attachment stats_data.json ;
    - évite de confondre les snapshots stats avec jobs/profils/welcome/etc. ;
    - épingle le snapshot pour faciliter la reprise après redémarrage ;
    - conserve une sauvegarde fichier via ConsoleJSONSnapshotStore si le JSON dépasse la limite Discord.
    """

    def __init__(self, bot: discord.Client, channel_name: str = "console") -> None:
        self.bot = bot
        self.channel_name = channel_name
        self._message_id: Optional[int] = None
        self._etag: Optional[str] = None
        self._last_save = 0.0
        self.min_interval = max(_read_int_env("STATS_MIN_INTERVAL", 900), 0)

        self._store = ConsoleJSONSnapshotStore(
            bot,
            marker=STATS_MARKER,
            filename=STATS_FILENAME,
            default_channel_name=channel_name,
            channel_id_env="CHANNEL_CONSOLE_ID",
            channel_name_env="CHANNEL_CONSOLE",
            history_limit_env="STATS_CONSOLE_HISTORY_LIMIT",
            history_limit_default=max(_read_int_env("CONSOLE_HISTORY_LIMIT", 300), 50),
            pin_messages=_read_bool_env("STATS_PIN_MESSAGES", True),
        )

    async def save(self, data: dict[str, Any]) -> bool:
        if not isinstance(data, dict):
            log.warning("StatsStore.save ignoré : payload non-dict (%s).", type(data).__name__)
            return False

        digest = _json_digest(data)
        now = time.monotonic()

        if self._etag == digest and (now - self._last_save) < self.min_interval:
            return True

        try:
            message = await self._store.save(data, current_message_id=self._message_id)
        except Exception:
            log.exception("Impossible d'enregistrer les stats dans #%s.", self.channel_name)
            return False

        if message is None:
            log.warning("Canal #%s introuvable : persistance stats Discord désactivée.", self.channel_name)
            return False

        self._message_id = getattr(message, "id", None)
        self._etag = digest
        self._last_save = now
        return True

    async def load(self) -> Optional[dict[str, Any]]:
        try:
            message, payload = await self._store.load_latest(current_message_id=self._message_id)
        except Exception:
            log.exception("Impossible de charger les stats depuis #%s.", self.channel_name)
            return None

        if not isinstance(payload, dict):
            return None

        self._message_id = getattr(message, "id", None)
        self._etag = _json_digest(payload)
        self._last_save = time.monotonic()
        return payload
