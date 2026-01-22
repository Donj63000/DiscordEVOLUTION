from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

import discord

log = logging.getLogger(__name__)

_HISTORY_LOCKS: dict[int, asyncio.Lock] = {}
_LAST_HISTORY_AT: dict[int, float] = {}


def _read_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _read_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _channel_key(channel: Any) -> int:
    key = getattr(channel, "id", None)
    if key is None:
        return id(channel)
    try:
        return int(key)
    except (TypeError, ValueError):
        return id(channel)


def _channel_label(channel: Any) -> str:
    name = getattr(channel, "name", None)
    if name:
        return str(name)
    cid = getattr(channel, "id", None)
    return str(cid) if cid is not None else "unknown"


def _is_rate_limited(exc: Exception) -> bool:
    if getattr(exc, "status", None) == 429:
        return True
    response = getattr(exc, "response", None)
    if response and getattr(response, "status", None) == 429:
        return True
    if getattr(exc, "code", None) == 429:
        return True
    return False


def _get_retry_after(exc: Exception) -> Optional[float]:
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            return None
    response = getattr(exc, "response", None)
    if response is not None:
        retry_after = getattr(response, "retry_after", None)
        if retry_after is not None:
            try:
                return float(retry_after)
            except (TypeError, ValueError):
                return None
    return None


async def fetch_channel_history(
    channel: discord.TextChannel,
    *,
    limit: int,
    oldest_first: bool = False,
    before: Optional[discord.abc.Snowflake] = None,
    after: Optional[discord.abc.Snowflake] = None,
    reason: str = "",
) -> list[discord.Message]:
    """Fetch a channel history snapshot with rate-limit backoff and per-channel locks."""
    if limit <= 0:
        return []
    retries = max(_read_int_env("DISCORD_HISTORY_RETRIES", 2), 0)
    backoff = max(_read_float_env("DISCORD_HISTORY_BACKOFF", 0.5), 0.0)
    max_backoff = max(_read_float_env("DISCORD_HISTORY_MAX_BACKOFF", 8.0), 0.0)
    min_interval = max(_read_float_env("DISCORD_HISTORY_MIN_INTERVAL", 0.0), 0.0)

    key = _channel_key(channel)
    label = _channel_label(channel)
    lock = _HISTORY_LOCKS.setdefault(key, asyncio.Lock())

    async with lock:
        last = _LAST_HISTORY_AT.get(key)
        if last is not None and min_interval > 0:
            wait = min_interval - (time.monotonic() - last)
            if wait > 0:
                log.debug(
                    "History throttle for %s reason=%s wait=%.2f",
                    label,
                    reason or "unspecified",
                    wait,
                )
                await asyncio.sleep(wait)
        delay = backoff
        attempt = 0
        while True:
            _LAST_HISTORY_AT[key] = time.monotonic()
            try:
                messages: list[discord.Message] = []
                history_kwargs: dict[str, Any] = {"limit": limit}
                if oldest_first:
                    history_kwargs["oldest_first"] = True
                if before is not None:
                    history_kwargs["before"] = before
                if after is not None:
                    history_kwargs["after"] = after
                try:
                    async for msg in channel.history(**history_kwargs):
                        messages.append(msg)
                except TypeError:
                    async for msg in channel.history(limit=limit):
                        messages.append(msg)
                return messages
            except Exception as exc:
                if _is_rate_limited(exc) and attempt < retries:
                    retry_after = _get_retry_after(exc)
                    wait = retry_after if retry_after is not None else delay
                    log.debug(
                        "History rate limited for %s reason=%s attempt=%s wait=%.2f",
                        label,
                        reason or "unspecified",
                        attempt + 1,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    if delay <= 0:
                        delay = 0.5
                    else:
                        delay = min(delay * 2, max_backoff)
                    attempt += 1
                    continue
                log.warning(
                    "History fetch failed for %s reason=%s: %s",
                    label,
                    reason or "unspecified",
                    exc,
                )
                return []


async def fetch_channel_message(
    channel: discord.TextChannel, message_id: int, *, reason: str = ""
) -> Optional[discord.Message]:
    """Fetch a single message with rate-limit backoff and per-channel locks."""
    retries = max(_read_int_env("DISCORD_HISTORY_RETRIES", 2), 0)
    backoff = max(_read_float_env("DISCORD_HISTORY_BACKOFF", 0.5), 0.0)
    max_backoff = max(_read_float_env("DISCORD_HISTORY_MAX_BACKOFF", 8.0), 0.0)

    key = _channel_key(channel)
    label = _channel_label(channel)
    lock = _HISTORY_LOCKS.setdefault(key, asyncio.Lock())

    async with lock:
        delay = backoff
        attempt = 0
        while True:
            try:
                return await channel.fetch_message(message_id)
            except discord.NotFound:
                return None
            except Exception as exc:
                if _is_rate_limited(exc) and attempt < retries:
                    retry_after = _get_retry_after(exc)
                    wait = retry_after if retry_after is not None else delay
                    log.debug(
                        "Message fetch rate limited for %s reason=%s attempt=%s wait=%.2f",
                        label,
                        reason or "unspecified",
                        attempt + 1,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    if delay <= 0:
                        delay = 0.5
                    else:
                        delay = min(delay * 2, max_backoff)
                    attempt += 1
                    continue
                log.warning(
                    "Message fetch failed for %s reason=%s: %s",
                    label,
                    reason or "unspecified",
                    exc,
                )
                return None
