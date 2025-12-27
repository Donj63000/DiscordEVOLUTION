from __future__ import annotations

import os
import re
import unicodedata

import discord

_ID_RE = re.compile(r"\d+")


def _take_digits(value: str | None) -> int | None:
    if not value:
        return None
    match = _ID_RE.search(value)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    filtered = []
    for char in normalized:
        if unicodedata.combining(char):
            continue
        category = unicodedata.category(char)
        if category.startswith("So"):
            continue
        if char.isalnum():
            filtered.append(char)
            continue
        if char in {"-", "_", " "}:
            filtered.append(" ")
            continue
        filtered.append(" ")
    text = "".join(filtered).strip().lower()
    text = text.replace("’", "'")
    text = re.sub(r"\s+", "-", text)
    text = text.replace("_", "-")
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def resolve_text_channel(
    guild: discord.Guild,
    *,
    id_env: str | None = None,
    name_env: str | None = None,
    default_name: str | None = None,
) -> discord.TextChannel | None:
    channel_id = None
    if id_env:
        channel_id = _take_digits(os.getenv(id_env))
    if channel_id:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        if channel and getattr(channel, "id", None) is not None:
            return channel
    candidates: list[str] = []
    if name_env:
        env_value = os.getenv(name_env)
        if env_value:
            candidates.append(env_value)
    if default_name:
        candidates.append(default_name)
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        candidate_id = _take_digits(candidate)
        if candidate_id:
            channel = guild.get_channel(candidate_id)
            if isinstance(channel, discord.TextChannel):
                return channel
            if channel and getattr(channel, "id", None) is not None:
                return channel
        channel = discord.utils.get(guild.text_channels, name=candidate)
        if isinstance(channel, discord.TextChannel):
            return channel
        if channel and getattr(channel, "id", None) is not None:
            return channel
        target = _normalize(candidate)
        if not target:
            continue
        for text_channel in guild.text_channels:
            if _normalize(text_channel.name) == target:
                return text_channel
    return None
