"""Je synchronise l'identité du bot et mémorise les images appliquées dans #console."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path

import discord

from utils.console_json_store import ConsoleJSONSnapshotStore

log = logging.getLogger(__name__)
BOT_DISPLAY_NAME = "Evolution BOT"
BRANDING_MARKER = "===BOTBRANDING==="
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _asset_key(asset) -> str | None:
    return getattr(asset, "key", None)


async def sync_bot_branding(bot) -> bool:
    name = os.getenv("BOT_DISPLAY_NAME", BOT_DISPLAY_NAME).strip() or BOT_DISPLAY_NAME
    path = Path(os.getenv("BOT_AVATAR_PATH", "assets/evolution-bot.png"))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    store = ConsoleJSONSnapshotStore(
        bot, marker=BRANDING_MARKER, filename="bot_branding.json", pin_messages=True
    )
    if await store.resolve_channel() is None:
        log.warning("Branding: console unavailable; identity sync deferred until next startup.")
        return False
    try:
        image = await asyncio.to_thread(path.read_bytes)
        image_hash = hashlib.sha256(image).hexdigest()
        message, saved = await store.load_latest()
        state = dict(saved) if isinstance(saved, dict) else {}
        if state.get("application_id") != bot.user.id:
            state = {"application_id": bot.user.id}
        application = await bot.application_info()
        if application.name != name:
            log.warning(
                "Branding: set application name to %r in Discord Developer Portal > General Information "
                "(application_id=%s); the bot API cannot edit this field.",
                name, bot.user.id,
            )
        user_changes = {}
        if bot.user.name != name:
            user_changes["username"] = name
        if state.get("avatar_source") != image_hash or state.get("avatar_key") != _asset_key(bot.user.avatar):
            user_changes["avatar"] = image
        complete = True
        if user_changes:
            try:
                updated_user = await bot.user.edit(**user_changes)
                state.update(avatar_source=image_hash, avatar_key=_asset_key(updated_user.avatar))
                log.debug("Branding: bot profile updated application_id=%s", bot.user.id)
            except discord.HTTPException:
                complete = False
                log.warning("Branding: bot profile update failed.", exc_info=True)
        if state.get("icon_source") != image_hash or state.get("icon_key") != _asset_key(application.icon):
            try:
                updated_application = await application.edit(icon=image)
                state.update(icon_source=image_hash, icon_key=_asset_key(updated_application.icon))
                log.debug("Branding: application icon updated application_id=%s", bot.user.id)
            except discord.HTTPException:
                complete = False
                log.warning("Branding: application icon update failed.", exc_info=True)
        if state != saved:
            snapshot = await store.save(state, current_message_id=getattr(message, "id", None))
            if snapshot is None:
                log.warning("Branding: identity checkpoint could not be saved to console.")
                return False
        log.debug("Branding: sync completed application_id=%s success=%s", bot.user.id, complete)
        return complete
    except (OSError, discord.HTTPException):
        log.warning("Branding: identity sync failed; commands remain available.", exc_info=True)
        return False
