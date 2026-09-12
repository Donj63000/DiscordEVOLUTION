import asyncio
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aioresponses import aioresponses
from PIL import Image, ImageDraw
import pytest
import pytest_asyncio

import dofus_wiki
from dofus_wiki import DofusWikiCog, RecipeQuantityModal, ResultView
from utils.dofus_wiki import DofusWikiClient, INDEX_PATHS, WIKI_ORIGIN, WikiError
from utils.wiki_images import ItemImage, WikiImageClient
from test_dofus_wiki_commands import ITEMS, MONSTERS, context, discord_error, interaction
from test_slash_commands import make_interaction, slash_bot
from test_xixou_navigation import enriched_cog, enrichment
from slash_commands import SlashCommandsCog


XIXOU_IMAGE = "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png"
MOON_IMAGE = WIKI_ORIGIN + "/icons/item_9_47.png"


@pytest.fixture
def png():
    output = BytesIO()
    with Image.new("RGBA", (256, 256), (0, 0, 0, 0)) as image:
        ImageDraw.Draw(image).ellipse((32, 32, 224, 224), fill=(255, 190, 60, 255))
        image.save(output, format="PNG")
    return output.getvalue()


@pytest_asyncio.fixture
async def image_cog(enriched_cog, enrichment, png):
    enriched_cog.enrichment_client.enrich.return_value = replace(
        enrichment, image_candidates=(XIXOU_IMAGE,),
    )
    enriched_cog.image_client = SimpleNamespace(
        resolve=AsyncMock(return_value=ItemImage(png, XIXOU_IMAGE)), close=AsyncMock(),
    )
    enriched_cog.client.item_icon = AsyncMock(return_value=MOON_IMAGE)
    yield enriched_cog


@pytest.mark.asyncio
async def test_initial_prefix_uses_files_and_preserves_verified_fallback(image_cog, png):
    ctx = context()
    uploaded = []

    async def send(**kwargs):
        uploaded.extend(file.fp.read() for file in kwargs.get("files", []))
        return SimpleNamespace(edit=AsyncMock())

    ctx.send.side_effect = send
    await image_cog.objet_command.callback(image_cog, ctx, nom="Gelano")
    payload = ctx.send.call_args.kwargs
    assert payload["embed"].thumbnail.url == "attachment://objet.png"
    assert [file.filename for file in payload["files"]] == ["objet.png"]
    assert uploaded == [png]
    assert all(file.fp.closed for file in payload["files"])
    assert "attachments" not in payload
    assert payload["view"].detail.icon == XIXOU_IMAGE
    image_cog.client.item_icon.assert_not_awaited()
    image_cog.image_client.resolve.assert_awaited_once_with((XIXOU_IMAGE,))


@pytest.mark.asyncio
async def test_selected_equipment_uses_attachments_for_message_edit(image_cog):
    results = ResultView(image_cog, 42, ITEMS, "item")
    selected = interaction(values=["0"])
    await results.choose(selected)
    payload = selected.edit_original_response.call_args.kwargs
    assert payload["embed"].thumbnail.url == "attachment://objet.png"
    assert [file.filename for file in payload["attachments"]] == ["objet.png"]
    assert "files" not in payload


@pytest.mark.asyncio
async def test_slash_initial_response_uploads_object_image(slash_bot, image_cog):
    await slash_bot.add_cog(image_cog)
    catalog = SlashCommandsCog(slash_bot)
    catalog.register_commands()
    command = slash_bot.tree.get_command("objet")
    selected = make_interaction(slash_bot, command)
    await command.callback(selected, nom="item:1")
    payload = selected.followup.send.call_args.kwargs
    assert payload["embed"].thumbnail.url == "attachment://objet.png"
    assert [file.filename for file in payload["files"]] == ["objet.png"]
    catalog.cog_unload()
    await slash_bot.remove_cog("DofusWikiCog")


@pytest.mark.asyncio
async def test_every_section_and_recipe_reuploads_thumbnail_with_independent_map(image_cog):
    view = await image_cog.detail_view(42, ITEMS[0], "item", 3)
    for section in ("summary", "details", "drops", "zones", "harvest", "uses"):
        selected = interaction(values=[section])
        await view.choose_section(selected)
        payload = selected.edit_original_response.call_args.kwargs
        names = [file.filename for file in payload["attachments"]]
        assert payload["embed"].thumbnail.url == "attachment://objet.png"
        assert names == (["objet.png", "carte-xixou.png"] if section in {"zones", "harvest"}
                         else ["objet.png"])
        assert bool(payload["embed"].image.url) == (section in {"zones", "harvest"})
    await view.choose_section(interaction(values=["zones"]))
    await view.go_next(interaction())
    assert view.page == 1
    recipe = interaction()
    await view.toggle_action(recipe)
    payload = recipe.edit_original_response.call_args.kwargs
    assert [file.filename for file in payload["attachments"]] == ["objet.png"]
    assert not payload["embed"].image.url
    assert payload["embed"].thumbnail.url == "attachment://objet.png"
    modal = RecipeQuantityModal(view)
    modal.quantity._value = "7"
    submitted = interaction()
    await modal.on_submit(submitted)
    assert "700 ×" in submitted.edit_original_response.call_args.kwargs["embed"].fields[0].value
    assert submitted.edit_original_response.call_args.kwargs["embed"].thumbnail.url == "attachment://objet.png"
    await view.toggle_action(interaction())
    assert view.section == "zones" and view.page == 1
    image_cog.image_client.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_returning_to_results_removes_both_attachments(image_cog):
    results = ResultView(image_cog, 42, ITEMS, "item")
    view = await image_cog.detail_view(42, ITEMS[0], "item", results=results.snapshot())
    results.stop()
    await view.choose_section(interaction(values=["zones"]))
    selected = interaction()
    await view.return_to_results(selected)
    payload = selected.edit_original_response.call_args.kwargs
    assert payload["attachments"] == []
    assert not payload["embed"].thumbnail.url and not payload["embed"].image.url


@pytest.mark.asyncio
@pytest.mark.parametrize("editing", [False, True])
async def test_rejected_attachments_fall_back_to_verified_public_thumbnail(image_cog, editing):
    view = await image_cog.detail_view(42, ITEMS[0], "item")
    sender = AsyncMock(side_effect=[discord_error(), SimpleNamespace(edit=AsyncMock())])
    await view.publish(sender, editing=editing)
    assert sender.await_count == 2
    fallback = sender.call_args.kwargs
    assert fallback["embed"].thumbnail.url == XIXOU_IMAGE
    assert "files" not in fallback
    if editing:
        assert fallback["attachments"] == []
    else:
        assert "attachments" not in fallback
    assert not view.is_finished()
    assert all(file.fp.closed for file in sender.call_args_list[0].kwargs[
        "attachments" if editing else "files"
    ])


@pytest.mark.asyncio
async def test_rejected_map_keeps_remote_object_thumbnail_and_map_link(image_cog):
    view = await image_cog.detail_view(42, ITEMS[0], "item")
    selected = interaction(values=["zones"])
    selected.edit_original_response.side_effect = [discord_error(), SimpleNamespace(edit=AsyncMock())]
    await view.choose_section(selected)
    payload = selected.edit_original_response.call_args.kwargs
    assert payload["attachments"] == []
    assert payload["embed"].thumbnail.url == XIXOU_IMAGE
    assert not payload["embed"].image.url
    assert view.map_link is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [discord_error(), asyncio.CancelledError()])
async def test_failed_initial_send_releases_views_and_image_handles(image_cog, error):
    view = await image_cog.detail_view(42, ITEMS[0], "item")
    sender = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await view.publish(sender)
    assert not image_cog.views
    assert all(file.fp.closed for file in sender.call_args_list[0].kwargs["files"])


@pytest.mark.asyncio
async def test_failed_edit_rolls_back_section_with_thumbnail_ready_for_retry(image_cog):
    view = await image_cog.detail_view(42, ITEMS[0], "item")
    selected = interaction(values=["zones"])
    selected.edit_original_response.side_effect = discord_error()
    with pytest.raises(type(discord_error())):
        await view.choose_section(selected)
    assert view.section == "summary" and view.item_image is not None
    retry = interaction(values=["zones"])
    await view.choose_section(retry)
    assert retry.edit_original_response.call_args.kwargs["embed"].thumbnail.url == "attachment://objet.png"


@pytest.mark.asyncio
@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("transition", ["shutdown", "expiry"])
async def test_attachment_fallback_never_retries_after_shutdown_or_expiry(
    image_cog, editing, transition,
):
    view = await image_cog.detail_view(42, ITEMS[0], "item", 3)
    error = discord_error()

    async def rejected_send(**kwargs):
        if transition == "shutdown":
            await image_cog.cog_unload()
        else:
            view._dispatch_timeout()
        raise error

    sender = AsyncMock(side_effect=rejected_send)
    selected = interaction(values=["zones"])
    selected.edit_original_response = sender
    with pytest.raises(type(error)) as raised:
        if editing:
            await view.choose_section(selected)
        else:
            await view.publish(sender)
    await asyncio.sleep(0)

    assert raised.value is error
    sender.assert_awaited_once()
    uploaded = sender.call_args.kwargs["attachments" if editing else "files"]
    assert [file.filename for file in uploaded] == (
        ["objet.png", "carte-xixou.png"] if editing else ["objet.png"]
    )
    assert all(file.fp.closed for file in uploaded)
    assert view.is_finished()
    assert view not in image_cog.views
    if editing:
        assert view.section == "summary" and view.page == 0 and view.quantity == 3
        assert view.map_link is None
        assert all(child.disabled for child in view.children if not getattr(child, "url", None))


@pytest.mark.asyncio
async def test_moon_index_is_requested_only_after_xixou_fails(image_cog, png):
    image_cog.image_client.resolve.side_effect = [None, ItemImage(png, MOON_IMAGE)]
    view = await image_cog.detail_view(42, ITEMS[0], "item")
    image_cog.client.item_icon.assert_awaited_once_with(ITEMS[0])
    assert image_cog.image_client.resolve.await_args_list[1].args == ((MOON_IMAGE,),)
    assert view.detail.icon == MOON_IMAGE


@pytest.mark.asyncio
async def test_missing_images_remove_unverified_original_url_and_keep_fiche(image_cog):
    image_cog.image_client.resolve.return_value = None
    view = await image_cog.detail_view(42, ITEMS[0], "item")
    assert view.item_image is None
    assert not view.embed().thumbnail.url
    sender = AsyncMock()
    await view.publish(sender)
    assert "files" not in sender.call_args.kwargs
    assert view.sections


@pytest.mark.asyncio
async def test_stale_thumbnail_and_source_are_visible_even_without_enriched_sections(image_cog, png, monkeypatch):
    image_cog.image_client.resolve.return_value = ItemImage(png, XIXOU_IMAGE, stale=True)
    monkeypatch.setattr(dofus_wiki, "enriched_item_sections", Mock(side_effect=ValueError("bad data")))
    view = await image_cog.detail_view(42, ITEMS[0], "recipe")
    assert not view.sections
    assert any(getattr(child, "url", "") == "https://xixou.io" for child in view.children)
    sender = AsyncMock()
    await view.publish(sender)
    assert "Illustration en cache" in sender.call_args.kwargs["embed"].footer.text


@pytest.mark.asyncio
async def test_no_image_download_for_autocomplete_or_monsters(image_cog):
    await image_cog.autocomplete("wiki_items", "gel")
    await image_cog.detail_view(42, MONSTERS[0], "monster")
    image_cog.image_client.resolve.assert_not_awaited()
    image_cog.client.item_icon.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_image_timeout_returns_base_fiche_without_leaking_errors(image_cog, monkeypatch):
    original_timeout = asyncio.timeout
    monkeypatch.setattr(dofus_wiki.asyncio, "timeout", lambda delay: original_timeout(0.02 if delay == 5 else delay))

    async def delayed(candidates):
        await asyncio.Event().wait()

    image_cog.image_client.resolve.side_effect = delayed
    view = await image_cog.detail_view(42, ITEMS[0], "item")
    assert view.item_image is None and view.sections
    image_cog.client.item_icon.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_while_resolving_image_cannot_register_a_view(image_cog, png):
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(candidates):
        entered.set()
        await release.wait()
        return ItemImage(png, XIXOU_IMAGE)

    image_cog.image_client.resolve.side_effect = delayed
    task = asyncio.create_task(image_cog.detail_view(42, ITEMS[0], "item"))
    await entered.wait()
    await image_cog.cog_unload()
    release.set()
    with pytest.raises(WikiError, match="redémarre"):
        await task
    assert not image_cog.views
    image_cog.image_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_clients_use_cold_moon_index_and_validated_png_without_xixou(png):
    moon, images = DofusWikiClient(), WikiImageClient()
    cog = DofusWikiCog(SimpleNamespace(), client=moon, enrichment_client=None, image_client=images)
    entry = ITEMS[0]
    try:
        with aioresponses() as http:
            http.get(WIKI_ORIGIN + "/api/item/1.json", payload={
                "id": 1, "name": "Gelano", "url": entry.url,
                "level": 60, "weight": 5, "recipe": [],
            })
            http.get(WIKI_ORIGIN + INDEX_PATHS[2], payload=[{"u": entry.path, "i": MOON_IMAGE}])
            http.get(MOON_IMAGE, body=png, content_type="image/png")
            view = await cog.detail_view(42, entry, "item")
        assert view.item_image is not None and view.item_image.source_url == MOON_IMAGE
        sender = AsyncMock()
        await view.publish(sender)
        assert sender.call_args.kwargs["embed"].thumbnail.url == "attachment://objet.png"
    finally:
        await cog.cog_unload()
