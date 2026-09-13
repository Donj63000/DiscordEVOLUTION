"""Interactions réelles discord.py ; seuls les transports HTTP/Discord sont simulés."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import dofus_wiki
from dofus_wiki import ProspectingModal, RecipeQuantityModal
from slash_commands import SlashCommandsCog
from utils.drop_calculator import ProspectingSettings
from utils.xixou_api import ItemEnrichment
from test_dofus_wiki_commands import ITEMS, MONSTERS, discord_error, interaction
from test_slash_commands import make_interaction, slash_bot
from test_wiki_image_navigation import image_cog, png
from test_xixou_embeds import text_of
from test_xixou_navigation import enriched_cog, enrichment


async def drops_view(cog, quantity=1):
    view = await cog.detail_view(42, ITEMS[0], "item", quantity)
    await view.choose_section(interaction(values=["drops"]))
    return view


async def open_form(view, personal="435", group=""):
    clicked = interaction()
    await view.prospecting_button.callback(clicked)
    clicked.response.defer.assert_not_awaited()
    clicked.response.send_modal.assert_awaited_once()
    modal = clicked.response.send_modal.call_args.args[0]
    assert isinstance(modal, ProspectingModal)
    modal.personal_pp._value, modal.group_pp._value = personal, group
    return modal


@pytest.mark.asyncio
async def test_button_only_exists_in_nonempty_item_drop_section(enriched_cog):
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    button = view.prospecting_button
    assert button.label == "Prospection personnalisée" and button not in view.children
    await view.choose_section(interaction(values=["drops"]))
    assert button in view.children
    await view.toggle_action(interaction())
    assert button not in view.children
    await view.toggle_action(interaction())
    assert button in view.children
    await view.choose_section(interaction(values=["zones"]))
    assert button not in view.children and view.map_link in view.children
    rows = view.to_components()
    assert len(rows) <= 5
    for row in rows:
        components = row["components"]
        if components[0]["type"] == 3:
            assert len(components) == 1
        else:
            assert 1 <= len(components) <= 5
            assert all(component["type"] == 2 for component in components)
    enriched_cog.enrichment_client.enrich.return_value = ItemEnrichment()
    empty = await enriched_cog.detail_view(42, ITEMS[0], "item")
    await empty.choose_section(interaction(values=["drops"]))
    assert empty.prospecting_button not in empty.children
    monster = await enriched_cog.detail_view(42, MONSTERS[0], "monster")
    assert not hasattr(monster, "prospecting_button")
    enriched_cog.enrichment_client.enrich.return_value = None
    plain = await enriched_cog.detail_view(42, ITEMS[0], "item")
    assert not hasattr(plain, "prospecting_button")


@pytest.mark.asyncio
async def test_submit_reuses_sources_and_only_rebuilds_drops(enriched_cog):
    view = await drops_view(enriched_cog)
    original = view.sections
    modal = await open_form(view, "435", "1000")
    assert modal in view.modals and modal.timeout == 180
    assert all(len(child.label) <= 45 for child in modal.children)
    assert len(modal.to_dict()["components"]) == 2
    submitted = interaction()
    await modal.on_submit(submitted)
    submitted.response.defer.assert_awaited_once()
    submitted.edit_original_response.assert_awaited_once()
    assert view.prospecting == ProspectingSettings(435, 1000)
    assert "0,1305 % à 0,3045 %" in text_of(view.sections["drops"])
    for name in original:
        assert (view.sections[name] is original[name]) == (name != "drops")
    assert not view.modals and modal.is_finished()
    payload = submitted.edit_original_response.call_args.kwargs
    assert payload["view"] is view and payload["allowed_mentions"].everyone is False
    assert "2026-09-01" in payload["embed"].fields[-1].value
    enriched_cog.client.detail.assert_awaited_once()
    enriched_cog.enrichment_client.enrich.assert_awaited_once()
    enriched_cog.map_renderer.render.assert_not_awaited()


@pytest.mark.asyncio
async def test_modal_prefill_and_blank_reset_restore_exact_original_pages(enriched_cog):
    view = await drops_view(enriched_cog)
    original = [page.embed.to_dict() for page in view.sections["drops"]]
    await (await open_form(view, "435", "1000")).on_submit(interaction())
    clicked = interaction()
    await view.open_prospecting(clicked)
    modal = clicked.response.send_modal.call_args.args[0]
    assert modal.personal_pp.default == "435" and modal.group_pp.default == "1000"
    modal.personal_pp._value = modal.group_pp._value = ""
    await modal.on_submit(interaction())
    assert view.prospecting is None
    assert [page.embed.to_dict() for page in view.sections["drops"]] == original
    assert view.prospecting_button in view.children


@pytest.mark.asyncio
@pytest.mark.parametrize("personal,group", [
    ("abc", ""), ("-1", ""), ("10001", ""), ("435", "434"), ("", "1000"),
])
async def test_invalid_input_has_private_feedback_and_leaves_the_fiche_unchanged(
    enriched_cog, personal, group,
):
    view = await drops_view(enriched_cog)
    before, pages = view.sections, view.pages
    modal = await open_form(view, personal, group)
    submitted = interaction()
    await modal.on_submit(submitted)
    submitted.edit_original_response.assert_not_awaited()
    assert submitted.followup.send.call_args.kwargs == {"ephemeral": True}
    assert view.sections is before and view.pages is pages and view.prospecting is None
    assert modal.is_finished() and modal not in view.modals


@pytest.mark.asyncio
async def test_button_and_modal_use_the_existing_owner_restriction(enriched_cog):
    view = await drops_view(enriched_cog)
    outsider = interaction(author=99)
    assert not await view.interaction_check(outsider)
    assert outsider.response.send_message.call_args.kwargs["ephemeral"]
    modal = await open_form(view)
    outsider = interaction(author=99)
    assert not await modal.interaction_check(outsider)
    assert outsider.response.send_message.call_args.kwargs["ephemeral"]
    assert await modal.interaction_check(interaction())


@pytest.mark.asyncio
async def test_configuration_survives_sections_recipe_quantity_and_pagination(enriched_cog, enrichment):
    enriched_cog.enrichment_client.enrich.return_value = replace(
        enrichment, drops=tuple(replace(enrichment.drops[0], name=f"Monstre {i}") for i in range(10)),
    )
    view = await drops_view(enriched_cog, quantity=3)
    await (await open_form(view, "435", "1000")).on_submit(interaction())
    assert len(view.pages) > 1
    await view.go_next(interaction())
    assert view.page == 1
    await view.choose_section(interaction(values=["zones"]))
    await view.go_next(interaction())
    await view.choose_section(interaction(values=["drops"]))
    assert view.page == 1
    await view.toggle_action(interaction())
    quantity = RecipeQuantityModal(view)
    quantity.quantity._value = "7"
    await quantity.on_submit(interaction())
    await view.toggle_action(interaction())
    assert view.quantity == 7 and view.page == 1
    assert view.prospecting == ProspectingSettings(435, 1000)
    await (await open_form(view, "500", "1000")).on_submit(interaction())
    assert view.page == 0 and view.quantity == 7
    await view.choose_section(interaction(values=["zones"]))
    assert view.page == 1


@pytest.mark.asyncio
async def test_separate_views_do_not_share_the_personal_configuration(enriched_cog):
    first, second = await drops_view(enriched_cog), await drops_view(enriched_cog)
    assert first.enrichment is second.enrichment
    await (await open_form(first)).on_submit(interaction())
    assert first.prospecting == ProspectingSettings(435)
    assert second.prospecting is None
    assert "Ta prospection" not in text_of(second.sections["drops"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [discord_error(), asyncio.CancelledError()])
async def test_failed_publication_restores_previous_config_pages_and_navigation(enriched_cog, failure):
    view = await drops_view(enriched_cog)
    await (await open_form(view, "300", "1000")).on_submit(interaction())
    before = view.sections, view.pages, view.section_positions.copy()
    modal = await open_form(view, "435", "1000")
    submitted = interaction()
    submitted.edit_original_response.side_effect = failure
    with pytest.raises(type(failure)):
        await modal.on_submit(submitted)
    assert view.prospecting == ProspectingSettings(300, 1000)
    assert view.sections is before[0] and view.pages is before[1]
    assert view.section_positions == before[2] and view.section == "drops"
    assert view.prospecting_button in view.children and not view.lock.locked()
    assert not view.modals


@pytest.mark.asyncio
async def test_render_failure_is_transactional_and_delegates_to_existing_error_handler(
    enriched_cog, monkeypatch,
):
    view = await drops_view(enriched_cog)
    before, pages = view.sections, view.pages
    modal = await open_form(view)
    monkeypatch.setattr(dofus_wiki, "drop_pages", Mock(side_effect=ValueError("rendu")))
    submitted = interaction()
    with pytest.raises(ValueError) as raised:
        await modal.on_submit(submitted)
    assert view.sections is before and view.pages is pages and view.prospecting is None
    submitted.edit_original_response.assert_not_awaited()
    view.on_error = AsyncMock()
    await modal.on_error(submitted, raised.value)
    view.on_error.assert_awaited_once_with(submitted, raised.value, modal)
    assert not view.modals


@pytest.mark.asyncio
async def test_expiry_during_update_rolls_back_and_keeps_controls_disabled(enriched_cog, monkeypatch):
    view = await drops_view(enriched_cog)
    before = view.sections

    async def expire(_interaction):
        view.stop()
        return False

    monkeypatch.setattr(view, "edit_page", expire)
    await (await open_form(view)).on_submit(interaction())
    assert view.prospecting is None and view.sections is before
    assert view.prospecting_button.disabled and not view.modals


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["stop", "timeout", "unload"])
async def test_expired_or_unloaded_fiche_cannot_be_reopened_by_a_modal(enriched_cog, finish):
    view = await drops_view(enriched_cog)
    modal = await open_form(view)
    if finish == "stop":
        view.stop()
    elif finish == "timeout":
        await view.on_timeout()
    else:
        await enriched_cog.cog_unload()
    assert modal.is_finished() and not view.modals
    submitted = interaction()
    await modal.on_submit(submitted)
    assert submitted.followup.send.call_args.kwargs["ephemeral"]
    submitted.edit_original_response.assert_not_awaited()
    assert view.prospecting is None


@pytest.mark.asyncio
async def test_modal_timeout_releases_it_without_expiring_its_fiche(enriched_cog):
    view = await drops_view(enriched_cog)
    modal = await open_form(view)
    await modal.on_timeout()
    assert modal.is_finished() and modal not in view.modals
    assert not view.is_finished()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [discord_error(), asyncio.CancelledError()])
async def test_opening_failure_does_not_leak_a_modal(enriched_cog, failure):
    view = await drops_view(enriched_cog)
    clicked = interaction()
    clicked.response.send_modal.side_effect = failure
    with pytest.raises(type(failure)):
        await view.open_prospecting(clicked)
    assert not view.modals


@pytest.mark.asyncio
async def test_expiry_while_opening_a_form_also_closes_the_form(enriched_cog):
    view = await drops_view(enriched_cog)
    clicked = interaction()

    async def send(modal):
        view.stop()

    clicked.response.send_modal.side_effect = send
    await view.open_prospecting(clicked)
    assert not view.modals
    assert clicked.response.send_modal.call_args.args[0].is_finished()


@pytest.mark.asyncio
async def test_expired_wrong_section_or_busy_view_gets_an_immediate_private_reply(enriched_cog):
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    for state in ("wrong-section", "busy", "expired"):
        if state == "busy":
            await view.lock.acquire()
        elif state == "expired":
            view.stop()
        clicked = interaction()
        try:
            await view.open_prospecting(clicked)
            clicked.response.send_modal.assert_not_awaited()
            clicked.response.defer.assert_not_awaited()
            assert clicked.response.send_message.call_args.kwargs["ephemeral"]
        finally:
            if view.lock.locked():
                view.lock.release()
    assert not view.modals


@pytest.mark.asyncio
async def test_submission_is_acknowledged_before_waiting_on_existing_view_lock(enriched_cog):
    view = await drops_view(enriched_cog)
    modal = await open_form(view)
    submitted = interaction()
    acknowledged = asyncio.Event()

    async def defer():
        acknowledged.set()

    submitted.response.defer.side_effect = defer
    await view.lock.acquire()
    task = asyncio.create_task(modal.on_submit(submitted))
    try:
        await asyncio.wait_for(acknowledged.wait(), timeout=1)
        submitted.edit_original_response.assert_not_awaited()
        assert not task.done()
    finally:
        view.lock.release()
        await asyncio.wait_for(task, timeout=1)
    assert view.prospecting == ProspectingSettings(435)


@pytest.mark.asyncio
async def test_two_pending_submissions_are_serialized_without_mutating_shared_pages(enriched_cog):
    view = await drops_view(enriched_cog)
    first, second = await open_form(view, "435"), await open_form(view, "500")
    blocked, release = asyncio.Event(), asyncio.Event()
    one, two = interaction(), interaction()

    async def edit(**kwargs):
        blocked.set()
        await release.wait()
        assert view.prospecting == ProspectingSettings(435)
        return SimpleNamespace(edit=AsyncMock())

    one.edit_original_response.side_effect = edit
    task_one = asyncio.create_task(first.on_submit(one))
    await asyncio.wait_for(blocked.wait(), timeout=1)
    task_two = asyncio.create_task(second.on_submit(two))
    try:
        await asyncio.sleep(0)
        two.response.defer.assert_awaited_once()
        two.edit_original_response.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(task_one, task_two), timeout=1)
    assert view.prospecting == ProspectingSettings(500)
    assert "Ta prospection : 500 PP" in view.embed().description
    assert not view.modals and not view.lock.locked()


@pytest.mark.asyncio
async def test_cancellation_while_waiting_does_not_leak_the_form(enriched_cog):
    view = await drops_view(enriched_cog)
    modal = await open_form(view)
    await view.lock.acquire()
    task = asyncio.create_task(modal.on_submit(interaction()))
    try:
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        view.lock.release()
    assert modal.is_finished() and not view.modals and view.prospecting is None


@pytest.mark.asyncio
async def test_calculation_reuses_thumbnail_and_preserves_attachment_fallback(image_cog):
    view = await drops_view(image_cog)
    submitted = interaction()
    submitted.edit_original_response.side_effect = [
        discord_error(), SimpleNamespace(edit=AsyncMock()),
    ]
    await (await open_form(view, "435", "1000")).on_submit(submitted)
    assert submitted.edit_original_response.await_count == 2
    first, fallback = submitted.edit_original_response.call_args_list
    assert first.kwargs["embed"].thumbnail.url == "attachment://objet.png"
    assert first.kwargs["attachments"][0].fp.closed
    assert fallback.kwargs["attachments"] == []
    assert fallback.kwargs["embed"].thumbnail.url == view.item_image.source_url
    assert view.prospecting == ProspectingSettings(435, 1000)
    image_cog.image_client.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_slash_object_reaches_the_calculator_without_registering_new_commands(
    slash_bot, enriched_cog,
):
    await slash_bot.add_cog(enriched_cog)
    catalog = SlashCommandsCog(slash_bot)
    try:
        catalog.register_commands()
        command = slash_bot.tree.get_command("objet")
        selected = make_interaction(slash_bot, command)
        await command.callback(selected, nom="item:1")
        view = selected.followup.send.call_args.kwargs["view"]
        await view.choose_section(interaction(values=["drops"]))
        await (await open_form(view, "435", "1000")).on_submit(interaction())
        assert view.prospecting == ProspectingSettings(435, 1000)
        assert "0,3045 %" in text_of(view.sections["drops"])
    finally:
        catalog.cog_unload()
        await slash_bot.remove_cog("DofusWikiCog")
