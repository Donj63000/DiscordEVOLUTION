#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re

import discord
from discord.utils import escape_markdown, escape_mentions

TICKET_FIELD_MAX_CHARS = int(os.getenv("TICKET_FIELD_MAX_CHARS", "950"))
TICKET_ATTACHMENT_LIMIT = int(os.getenv("TICKET_ATTACHMENT_LIMIT", "3"))


def sanitize_ticket_text(content: str) -> str:
    if not content:
        return ""
    cleaned = escape_mentions(escape_markdown(content.strip()))
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def format_ticket_body(message: discord.Message) -> str:
    parts: list[str] = []
    text = sanitize_ticket_text(message.content or "")
    if text:
        parts.append(text)
    attachments: list[str] = []
    for attachment in message.attachments[:TICKET_ATTACHMENT_LIMIT]:
        name = attachment.filename or "pièce jointe"
        attachments.append(f"- {name}: {attachment.url}")
    overflow = max(0, len(message.attachments) - TICKET_ATTACHMENT_LIMIT)
    if overflow > 0:
        suffix = "s" if overflow > 1 else ""
        attachments.append(f"- … (+{overflow} pièce{suffix} jointe{suffix} supplémentaire{suffix})")
    if attachments:
        parts.append("Pièces jointes:\n" + "\n".join(attachments))
    combined = "\n\n".join(parts).strip()
    if combined and len(combined) > TICKET_FIELD_MAX_CHARS:
        combined = combined[: TICKET_FIELD_MAX_CHARS - 3].rstrip() + "..."
    return combined
