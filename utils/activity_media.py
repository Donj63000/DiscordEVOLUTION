"""Local announcement artwork, reusable Discord attachments, no external image host."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import discord

log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_FILENAME = "activite.png"


def announcement_image(channel=None, *, retained=()) -> tuple[object | None, str | None]:
    """Return an existing Attachment or a new File; callers own and close new Files."""
    existing = next((item for item in retained if item.filename == IMAGE_FILENAME), None)
    if existing is not None:
        return existing, None
    guild = getattr(channel, "guild", None)
    me = getattr(guild, "me", None)
    if channel is not None and me is not None and not channel.permissions_for(me).attach_files:
        return None, "Image non jointe : le bot doit pouvoir « Joindre des fichiers »."
    configured = Path(os.getenv("ACTIVITE_IMAGE_PATH", IMAGE_FILENAME))
    path = configured if configured.is_absolute() else PROJECT_ROOT / configured
    try:
        size = path.stat().st_size
        if size > min(getattr(guild, "filesize_limit", 8 * 1024 * 1024), 8 * 1024 * 1024):
            return None, "Image trop volumineuse : réduire activite.png à moins de 8 Mo."
        return discord.File(path, filename=IMAGE_FILENAME), None
    except OSError:
        log.warning("Activity artwork unavailable path=%s", path, exc_info=True)
        return None, "Image indisponible : vérifier activite.png à la racine du projet."


def close_artwork(attachment) -> None:
    if isinstance(attachment, discord.File):
        attachment.close()
        attachment.fp.close()
