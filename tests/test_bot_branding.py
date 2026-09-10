import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from utils import bot_branding


@pytest.fixture
def branding(monkeypatch, tmp_path):
    image = b"image de test"
    path = tmp_path / "avatar.png"
    path.write_bytes(image)
    monkeypatch.setenv("BOT_AVATAR_PATH", str(path))
    monkeypatch.setenv("BOT_DISPLAY_NAME", "Evolution BOT")
    store = SimpleNamespace(
        resolve_channel=AsyncMock(return_value=SimpleNamespace(id=50)),
        load_latest=AsyncMock(return_value=(None, None)),
        save=AsyncMock(return_value=SimpleNamespace(id=60)),
    )
    monkeypatch.setattr(bot_branding, "ConsoleJSONSnapshotStore", lambda *args, **kwargs: store)
    user = SimpleNamespace(id=123, name="Ancien bot", avatar=SimpleNamespace(key="old-avatar"))
    user.edit = AsyncMock(return_value=SimpleNamespace(avatar=SimpleNamespace(key="new-avatar")))
    application = SimpleNamespace(name="Evolution BOT", icon=SimpleNamespace(key="old-icon"))
    application.edit = AsyncMock(return_value=SimpleNamespace(icon=SimpleNamespace(key="new-icon")))
    bot = SimpleNamespace(user=user, application_info=AsyncMock(return_value=application))
    return SimpleNamespace(bot=bot, store=store, application=application, image=image, path=path)


@pytest.mark.asyncio
async def test_branding_updates_profile_icon_and_console_checkpoint(branding):
    assert await bot_branding.sync_bot_branding(branding.bot)
    branding.bot.user.edit.assert_awaited_once_with(username="Evolution BOT", avatar=branding.image)
    branding.application.edit.assert_awaited_once_with(icon=branding.image)
    state = branding.store.save.call_args.args[0]
    assert state["application_id"] == 123
    assert state["avatar_source"] == hashlib.sha256(branding.image).hexdigest()
    assert state["avatar_key"] == "new-avatar"
    assert state["icon_key"] == "new-icon"


@pytest.mark.asyncio
async def test_branding_does_not_reupload_images_after_restart(branding):
    image_hash = hashlib.sha256(branding.image).hexdigest()
    branding.bot.user.name = "Evolution BOT"
    saved = {
        "application_id": 123, "avatar_source": image_hash, "avatar_key": "old-avatar",
        "icon_source": image_hash, "icon_key": "old-icon",
    }
    branding.store.load_latest.return_value = (SimpleNamespace(id=60), saved)

    assert await bot_branding.sync_bot_branding(branding.bot)

    branding.bot.user.edit.assert_not_awaited()
    branding.application.edit.assert_not_awaited()
    branding.store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_branding_records_partial_success_for_retry(branding):
    branding.bot.user.edit.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Test refus"
    )
    assert not await bot_branding.sync_bot_branding(branding.bot)
    state = branding.store.save.call_args.args[0]
    assert state["icon_key"] == "new-icon"
    assert "avatar_source" not in state


@pytest.mark.asyncio
async def test_branding_reports_application_name_setting_without_unsupported_api(branding, caplog):
    branding.application.name = "Ancienne application"
    assert await bot_branding.sync_bot_branding(branding.bot)
    assert "General Information" in caplog.text
    branding.application.edit.assert_awaited_once_with(icon=branding.image)


@pytest.mark.asyncio
async def test_branding_does_not_change_remote_identity_without_console(branding):
    branding.store.resolve_channel.return_value = None
    assert not await bot_branding.sync_bot_branding(branding.bot)
    branding.bot.user.edit.assert_not_awaited()
    branding.bot.application_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_branding_missing_asset_does_not_prevent_bot_startup(branding):
    branding.path.unlink()
    assert not await bot_branding.sync_bot_branding(branding.bot)
    branding.bot.user.edit.assert_not_awaited()
    branding.store.save.assert_not_awaited()
