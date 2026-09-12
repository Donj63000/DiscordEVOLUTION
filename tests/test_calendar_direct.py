"""Vérifie les parcours sans formulaire, les copies privées et la sobriété de l'interface."""

import copy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from PIL import ImageDraw

import calendrier
from test_calendar_data import record
from test_calendar_view import NOW, agenda, interaction
from utils.calendar_data import snapshot_events
from utils.calendar_view import ActivityDetailView, close_files


async def choose(view, value, *, user_id=7):
    """Simule le contrôle d'accès Discord avant d'appeler le menu."""
    click = interaction(user_id=user_id)
    click.data = {"custom_id": view.choose_options.custom_id, "values": [value]}
    view.choose_options._values = [value]
    assert await view.interaction_check(click)
    await view.choose_options.callback(click)
    return click


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 6, 7, 40])
async def test_compact_controls_only_show_useful_rows(agenda, count):
    view = agenda.view
    agenda.records.clear()
    agenda.records.update({str(i): record() for i in range(count)})
    await view.build_payload()
    rows = view.to_components()
    assert len(rows) == (2 if count == 0 else 3 if count <= 6 else 4)
    assert len(rows[0]["components"]) == 4
    assert (view.choose_event in view.children) is bool(count)
    assert (view.previous_page in view.children) is (count > 6)
    assert (view.next_page in view.children) is (count > 6)
    assert len({child.custom_id for child in view.children}) == len(view.children)
    assert all(len(row["components"]) <= 5 for row in rows)
    for item in view.children:
        if isinstance(item, discord.ui.Select):
            assert 1 <= len(item.options) <= 25
            assert len(item.placeholder) <= 150
            assert all(len(option.label.encode("utf-16-le")) <= 200 for option in item.options)


@pytest.mark.asyncio
async def test_pagination_disappears_again_after_events_are_removed(agenda):
    view = agenda.view
    agenda.records.update({str(i): record() for i in range(2, 30)})
    await view.build_payload()
    await view.next_page.callback(interaction())
    assert view.state.page == 1
    agenda.records.clear()
    agenda.records["1"] = record()
    await view.refresh(interaction())
    assert view.state.page == 0
    assert view.previous_page not in view.children
    assert view.next_page not in view.children


@pytest.mark.asyncio
async def test_week_month_toggle_is_one_click_and_never_opens_a_modal(agenda):
    view = agenda.view
    view.renderer.render = AsyncMock(return_value=b"png")
    first = interaction()
    await view.switch_mode.callback(first)
    assert view.state.mode == "mois"
    assert view.switch_mode.label == "Semaine"
    assert first.edit_original_response.await_args.kwargs["attachments"][0].fp.closed
    second = interaction()
    await view.switch_mode.callback(second)
    assert view.state.mode == "semaine"
    assert view.switch_mode.label == "Mois"
    assert second.edit_original_response.await_args.kwargs["attachments"] == []
    first.response.send_modal.assert_not_awaited()
    second.response.send_modal.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["toutes", "inscrit", "disponibles"])
async def test_private_filters_update_same_message_without_another_form(agenda, value):
    agenda.view.private = True
    click = await choose(agenda.view, value)
    assert agenda.view.state.filter == value
    assert agenda.view.state.page == 0
    click.edit_original_response.assert_awaited_once()
    click.response.send_modal.assert_not_awaited()
    click.followup.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [7, 8])
@pytest.mark.parametrize("value", ["private", "inscrit"])
async def test_private_copy_belongs_to_clicker_without_modifying_public_view(agenda, user_id, value):
    view = agenda.view
    original = copy.deepcopy(agenda.records)
    previous_state = view.state
    click = await choose(view, value, user_id=user_id)
    child = click.followup.send.await_args.kwargs["view"]
    try:
        assert child is not view
        assert child.author_id == user_id
        assert child.private
        assert child.renderer is view.renderer
        assert child.source is view.source
        assert child.state.filter == ("inscrit" if value == "inscrit" else previous_state.filter)
        assert view.state == previous_state
        assert agenda.records == original
        assert "private" not in {option.value for option in child.choose_options.options}
        assert click.followup.send.await_args.kwargs["ephemeral"] is True
        assert click.followup.send.await_args.kwargs["allowed_mentions"].everyone is False
        click.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        click.edit_original_response.assert_not_awaited()
        await child.next_period.callback(interaction(user_id=user_id))
        assert view.state == previous_state
        assert child.state.anchor != view.state.anchor
    finally:
        child.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("control,value", [
    ("next_period", None), ("previous_period", None), ("switch_mode", None),
    ("choose_options", "close"), ("choose_options", "date"),
    ("choose_options", "toutes"), ("choose_options", "disponibles"),
    ("choose_options", "refresh"), ("choose_options", "upcoming"),
])
async def test_another_member_cannot_mutate_or_close_public_calendar(agenda, control, value):
    view = agenda.view
    click = interaction(user_id=8)
    click.data = {"custom_id": getattr(view, control).custom_id, "values": [value]}
    assert not await view.interaction_check(click)
    assert click.response.send_message.await_args.kwargs["ephemeral"] is True
    assert "réservée" in click.response.send_message.await_args.args[0]
    assert not view.is_finished()


@pytest.mark.asyncio
@pytest.mark.parametrize("control,value", [
    ("choose_event", "1"), ("choose_options", "private"), ("choose_options", "inscrit"),
])
async def test_private_calendar_rejects_every_other_viewer(agenda, control, value):
    view = agenda.view
    view.private = True
    click = interaction(user_id=8)
    click.data = {"custom_id": getattr(view, control).custom_id, "values": [value]}
    assert not await view.interaction_check(click)
    click.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_detail_and_join_use_visitors_identity_not_calendar_author(agenda):
    view = agenda.view
    seen = []

    async def action(click, event_id, operation):
        seen.append((click.user.id, event_id, operation))
        agenda.records[event_id]["participants"].append(click.user.id)

    view.action = action
    view.choose_event._values = ["1"]
    click = interaction(user_id=8)
    click.data = {"custom_id": view.choose_event.custom_id, "values": ["1"]}
    assert await view.interaction_check(click)
    await view.choose_event.callback(click)
    detail = click.followup.send.await_args.kwargs["view"]
    try:
        assert detail.author_id == 8
        assert detail.join in detail.children
        await detail.join.callback(interaction(user_id=8))
        assert seen == [(8, "1", "join")]
        assert detail.leave in detail.children
        assert detail.join not in detail.children
        assert agenda.records["1"]["participants"] == [7, 8]
        assert view.author_id == 7
        assert view.state.filter == "toutes"
    finally:
        detail.stop()


@pytest.mark.asyncio
async def test_public_status_never_claims_that_every_viewer_is_registered(agenda):
    embed, _ = await agenda.view.build_payload()
    assert "**Inscrit**" not in embed.fields[0].value
    agenda.view.private = True
    embed, _ = await agenda.view.build_payload()
    assert "**Inscrit**" in embed.fields[0].value


@pytest.mark.asyncio
async def test_date_form_only_appears_after_explicit_menu_choice(agenda):
    await agenda.view.build_payload()
    click = await choose(agenda.view, "date")
    modal = click.response.send_modal.await_args.args[0]
    try:
        assert modal.title == "Aller à une date"
        assert modal.owner is agenda.view
        click.response.defer.assert_not_awaited()
        click.edit_original_response.assert_not_awaited()
    finally:
        modal.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["unknown", ""])
async def test_invalid_option_returns_private_notice_without_editing(agenda, value):
    click = await choose(agenda.view, value)
    click.edit_original_response.assert_not_awaited()
    assert "inconnue" in click.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_close_is_available_in_menu_and_never_deletes_data(agenda):
    original = copy.deepcopy(agenda.records)
    await agenda.view.build_payload()
    click = await choose(agenda.view, "close")
    assert agenda.view.is_finished()
    assert click.edit_original_response.await_args.kwargs["view"] is None
    click.message.delete.assert_not_awaited()
    assert agenda.records == original


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["expired", "other_guild"])
async def test_private_copy_checks_scope_before_deferring_or_rendering(agenda, failure):
    click = interaction(guild_id=200 if failure == "other_guild" else 100)
    if failure == "expired":
        agenda.view.stop()
    await agenda.view.open_private(click)
    click.response.defer.assert_not_awaited()
    click.followup.send.assert_not_awaited()
    assert click.response.send_message.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_source_failure_restores_state_without_reading_broken_source_again(agenda):
    view = agenda.view
    await view.build_payload()
    previous = view.state, view.snapshot, view.page
    calls = 0

    def broken_source():
        nonlocal calls
        calls += 1
        raise RuntimeError("storage temporarily unavailable")

    view.source = broken_source
    click = interaction()
    await view.next_period.callback(click)
    assert (view.state, view.snapshot, view.page) == previous
    assert calls == 1
    assert len(view.choose_event.options) == 1
    assert "impossible" in click.followup.send.await_args.args[0]
    assert not view.is_finished()


@pytest.mark.asyncio
async def test_same_monthly_png_keeps_existing_discord_attachment(agenda):
    view = agenda.view
    view.state = replace(view.state, mode="mois")
    view.renderer.render = AsyncMock(return_value=b"unchanged-png")
    _, first_files = await view.build_payload()
    retained = SimpleNamespace(id=123, filename=first_files[0].filename)
    close_files(first_files)
    view.message = SimpleNamespace(attachments=[retained])
    embed, files = await view.build_payload()
    click = interaction()
    await view.publish_edit(click, embed, files)
    assert click.edit_original_response.await_args.kwargs["attachments"] == [retained]
    assert files[0].fp.closed


@pytest.mark.asyncio
async def test_changed_monthly_png_uploads_new_file(agenda):
    view = agenda.view
    view.state = replace(view.state, mode="mois")
    view.renderer.render = AsyncMock(return_value=b"new-png")
    view.message = SimpleNamespace(attachments=[SimpleNamespace(id=123, filename="old.png")])
    embed, files = await view.build_payload()
    click = interaction()
    await view.publish_edit(click, embed, files)
    assert click.edit_original_response.await_args.kwargs["attachments"] == files
    assert files[0].fp.closed


@pytest.mark.asyncio
async def test_failed_private_send_stops_only_child_and_closes_its_file(agenda):
    view = agenda.view
    view.state = replace(view.state, mode="mois")
    view.renderer.render = AsyncMock(return_value=b"png")
    click = interaction()
    click.followup.send.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "denied",
    )
    with pytest.raises(discord.Forbidden):
        await view.open_private(click)
    child = click.followup.send.await_args_list[0].kwargs["view"]
    assert child.is_finished()
    assert not view.is_finished()
    assert click.followup.send.await_args_list[0].kwargs["files"][0].fp.closed


@pytest.mark.asyncio
async def test_public_timeout_uses_component_message_instead_of_old_webhook(agenda):
    view = agenda.view
    await view.build_payload()
    stale_message = SimpleNamespace(edit=AsyncMock())
    view.message = stale_message
    click = interaction()
    assert await view.interaction_check(click)
    await view.on_timeout()
    stale_message.edit.assert_not_awaited()
    assert click.message.edit.await_args.kwargs["view"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("description,visible", [
    ("Une sortie conviviale.", False), ("😀" * 1600, True), ("*" * 1600, True),
])
async def test_detail_shows_only_useful_actions_including_utf16_overflow(agenda, description, visible):
    agenda.records["1"]["description"] = description
    detail = ActivityDetailView(8, agenda.guild, lambda: agenda.records, "1", AsyncMock(), lambda: NOW)
    try:
        embed = detail.build_embed()
        assert detail.join in detail.children
        assert detail.leave not in detail.children
        assert (detail.full_text in detail.children) is visible
        assert len(detail.children) == (4 if visible else 3)
        assert all(field.name != "Commandes alternatives" for field in embed.fields)
        assert len(embed) <= 6000
        assert len(embed.description.encode("utf-16-le")) <= 5000
    finally:
        detail.stop()


@pytest.mark.asyncio
async def test_empty_period_points_to_next_event_without_dead_select(agenda):
    agenda.records["1"]["date_str"] = "2026-10-15 21:00:00"
    embed, files = await agenda.view.build_payload()
    assert agenda.view.choose_event not in agenda.view.children
    assert "15/10 à 21:00" in embed.description
    assert "upcoming" in {option.value for option in agenda.view.choose_options.options}
    click = await choose(agenda.view, "upcoming")
    assert agenda.view.state.contains(date(2026, 10, 15))
    assert agenda.view.choose_event in agenda.view.children
    click.edit_original_response.assert_awaited_once()


def test_monthly_grid_uses_counts_and_first_time_not_tiny_user_titles(monkeypatch):
    captured = []
    original = ImageDraw.ImageDraw.text

    def draw_text(self, xy, text, *args, **kwargs):
        captured.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", draw_text)
    events = snapshot_events({
        "1": record("2026-09-11 23:00:00", titre="Un titre très long qui ne doit pas être dessiné"),
        "2": record("2026-09-11 20:30:00", titre="Une autre activité"),
        "3": record("2026-09-12 21:15:00"),
    }).events
    data = calendrier.render_month(events, 2026, 9, date(2026, 9, 11))
    assert data.startswith(b"\x89PNG")
    assert "2 activités" in captured
    assert "1 activité" in captured
    assert "20:30" in captured
    assert "21:15" in captured
    assert "23:00" not in captured
    assert all(event.title not in captured for event in events)


@pytest.mark.asyncio
async def test_image_cache_ignores_text_changes_but_refreshes_when_schedule_changes(monkeypatch):
    calls = []

    def render(*args):
        calls.append(args)
        return b"png"

    monkeypatch.setattr(calendrier, "render_month", render)
    renderer = calendrier.MonthlyRenderer()
    records = {"1": record()}
    today = date(2026, 9, 11)
    await renderer.render(snapshot_events(records).events, 2026, 9, today)
    records["1"]["titre"] = "Titre modifié"
    records["1"]["description"] = "Description modifiée"
    records["1"]["participants"].append(8)
    await renderer.render(snapshot_events(records).events, 2026, 9, today)
    assert len(calls) == 1
    records["1"]["date_str"] = "2026-09-11 21:30:00"
    await renderer.render(snapshot_events(records).events, 2026, 9, today)
    assert len(calls) == 2
    records["2"] = record()
    await renderer.render(snapshot_events(records).events, 2026, 9, today)
    assert len(calls) == 3
    records["2"]["cancelled"] = True
    await renderer.render(snapshot_events(records).events, 2026, 9, today)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_public_status_has_no_fictitious_member_identifier(agenda):
    """Même une ancienne entrée avec un identifiant nul ne produit pas un faux statut personnel."""
    agenda.records["1"]["participants"] = [0, 7]
    embed, _ = await agenda.view.build_payload()
    assert "**Inscrit**" not in embed.fields[0].value
