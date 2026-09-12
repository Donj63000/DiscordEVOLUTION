"""Adaptateur fin entre la presentation testable et les embeds Discord."""

from __future__ import annotations

import discord

from utils.exo_presentation import build_payload, number, percent


def build_embed(session) -> discord.Embed:
    return discord.Embed.from_dict(build_payload(session))
