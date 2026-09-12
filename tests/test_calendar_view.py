import asyncio
import copy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

from test_calendar_data import record
from utils.calendar_data import CalendarState
from utils.calendar_view import (
    ActivityDetailView, CalendarDateModal, CalendrierView, close_files,
)
from utils.datetime_utils import PARIS

NOW = datetime(2026, 9, 11, 21, tzinfo=PARIS)


def interaction(user_id=7, guild_id=100):
    response = SimpleNamespace(is_done=Mock(return_value=False), send_message=AsyncMock(),
                               send_modal=AsyncMock())

    async def defer(**kwargs):
        response.is_done.return_value = True

    response.defer = AsyncMock(side_effect=defer)
    message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id), guild_id=guild_id, response=response,
        message=message, followup=SimpleNamespace(send=AsyncMock(return_value=message)),
        edit_original_response=AsyncMock(return_value=message),
        original_response=AsyncMock(return_value=message),
    )


@pytest_asyncio.fixture
async def agenda():
    records = {"1": record()}
    guild = SimpleNamespace(id=100, get_member=Mock(return_value=SimpleNamespace(display_name="Héros")))
    view = CalendrierView(
        SimpleNamespace(id=7), {}, source=lambda: records, guild=guild, clock=lambda: NOW,
    )
    yield SimpleNamespace(view=view, records=records, guild=guild)
    view.stop()


@pytest.mark.asyncio
async def test_default_week_is_native_text_with_bounded_components(agenda):
    agenda.records.update({str(i): record(titre=f"Activité {i}") for i in range(2, 41)})
    embed, files = await agenda.view.build_payload()
    assert not files
    assert "Semaine du" in embed.title
    assert len(embed.fields) == 6
    assert len(agenda.view.choose_event.options) == 6
    assert not agenda.view.next_page.disabled
    assert len(agenda.view.children) == 8
    assert all(0 <= item.row <= 4 for item in agenda.view.children)
    assert len(embed) < 6000


@pytest.mark.asyncio
async def test_refresh_reloads_mutations_and_removes_cancelled_events(agenda):
    view = agenda.view
    await view.build_payload()
    agenda.records["1"]["titre"] = "Nouveau titre"
    agenda.records["1"]["participants"] = [7, 8, 9]
    agenda.records["2"] = record(cancelled=True)
    click = interaction()
    await view.refresh(click)
    embed = click.edit_original_response.await_args.kwargs["embed"]
    assert len(embed.fields) == 1
    assert "Nouveau titre" in embed.fields[0].name
    assert "3/8" in embed.fields[0].value
    assert [option.value for option in view.choose_event.options] == ["1"]


@pytest.mark.asyncio
async def test_defer_happens_before_render_and_month_to_week_clears_attachment(agenda):
    view = agenda.view
    click = interaction()

    async def render(*args):
        assert click.response.is_done()
        return b"fake-png"

    view.renderer.render = AsyncMock(side_effect=render)
    await view.change(click, lambda state: replace(state, mode="mois"))
    assert click.edit_original_response.await_args.kwargs["embed"].image.url.startswith("attachment://")
    files = click.edit_original_response.await_args.kwargs["attachments"]
    assert files[0].fp.closed
    next_click = interaction()
    await view.change(next_click, lambda state: replace(state, mode="semaine"))
    payload = next_click.edit_original_response.await_args.kwargs
    assert payload["attachments"] == []
    assert not payload["embed"].image.url


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["renderer", "permission", "size"])
async def test_monthly_failure_keeps_a_fully_usable_text_agenda(agenda, reason):
    view = agenda.view
    view.state = replace(view.state, mode="mois")
    if reason == "renderer":
        view.renderer.render = AsyncMock(side_effect=OSError("font missing"))
    elif reason == "permission":
        view.attach_files = False
    else:
        view.attachment_limit = 2
        view.renderer.render = AsyncMock(return_value=b"large")
    embed, files = await view.build_payload()
    assert not files
    assert "Donjon Blop" in embed.fields[0].name
    assert not view.choose_event.disabled


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,guild_id", [(8, 100), (7, 200)])
async def test_session_ownership_and_guild_are_checked(agenda, user_id, guild_id):
    click = interaction(user_id, guild_id)
    assert not await agenda.view.interaction_check(click)
    click.response.send_message.assert_awaited_once()
    assert click.response.send_message.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_timeout_disables_controls_and_never_deletes_activities(agenda):
    original = copy.deepcopy(agenda.records)
    await agenda.view.build_payload()
    message = SimpleNamespace(edit=AsyncMock())
    agenda.view.message = message
    await agenda.view.on_timeout()
    assert all(child.disabled for child in agenda.view.children)
    assert message.edit.await_args.kwargs["view"] is None
    assert agenda.view.is_finished()
    assert "Session expirée" in message.edit.await_args.kwargs["embed"].footer.text
    assert agenda.records == original


@pytest.mark.asyncio
async def test_close_removes_controls_not_data(agenda):
    original = copy.deepcopy(agenda.records)
    await agenda.view.build_payload()
    click = interaction()
    await agenda.view.finish(click)
    assert agenda.view.is_finished()
    assert click.edit_original_response.await_args.kwargs["view"] is None
    click.message.delete.assert_not_awaited()
    assert agenda.records == original


@pytest.mark.asyncio
async def test_expired_parent_rejects_modal_submission(agenda):
    modal = CalendarDateModal(agenda.view)
    try:
        modal.target._value = "12/09/2026"
        agenda.view.stop()
        click = interaction()
        await modal.on_submit(click)
        assert "fermée" in click.response.send_message.await_args.args[0]
        click.edit_original_response.assert_not_awaited()
    finally:
        modal.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["31/02/2026", "11/09/2027"])
async def test_date_modal_validates_then_navigates(agenda, value):
    modal = CalendarDateModal(agenda.view)
    try:
        modal.target._value = value
        click = interaction()
        await modal.on_submit(click)
        if value == "31/02/2026":
            assert agenda.view.state.anchor == NOW.date()
            click.edit_original_response.assert_not_awaited()
        else:
            assert agenda.view.state.anchor == date(2027, 9, 11)
            click.edit_original_response.assert_awaited_once()
    finally:
        modal.stop()


@pytest.mark.asyncio
async def test_concurrent_navigation_is_serialized(agenda):
    calls = []
    first, second = interaction(), interaction()

    async def edit(**kwargs):
        calls.append(kwargs["embed"].title)
        await asyncio.sleep(0.01)
        return first.message

    first.edit_original_response.side_effect = edit
    second.edit_original_response.side_effect = edit
    await asyncio.gather(
        agenda.view.next_period.callback(first),
        agenda.view.next_period.callback(second),
    )
    assert agenda.view.state.anchor == NOW.date() + timedelta(days=14)
    assert len(calls) == 2
    assert first.response.is_done() and second.response.is_done()


@pytest.mark.asyncio
async def test_failed_edit_restores_navigation_state_and_reports_error(agenda):
    click = interaction()
    click.edit_original_response.side_effect = discord.HTTPException(
        SimpleNamespace(status=500, reason="error"), "unavailable",
    )
    original = agenda.view.state
    await agenda.view.next_period.callback(click)
    assert agenda.view.state == original
    click.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleted_message_stops_session(agenda):
    click = interaction()
    click.edit_original_response.side_effect = discord.NotFound(
        SimpleNamespace(status=404, reason="missing"), "Unknown message",
    )
    await agenda.view.next_period.callback(click)
    assert agenda.view.is_finished()
    click.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_malformed_record_does_not_hide_valid_events(agenda):
    agenda.records["bad"] = record(date_str="bad date")
    embed, files = await agenda.view.build_payload()
    assert "1 entrée(s) illisible(s)" in embed.description
    assert "Donjon Blop" in embed.fields[0].name


@pytest.mark.asyncio
async def test_long_text_mentions_and_emoji_stay_below_discord_limits(agenda):
    agenda.records.update({
        str(i): record(titre="😀" * 800, description="@everyone **" + "🎉" * 3000)
        for i in range(1, 8)
    })
    embed, files = await agenda.view.build_payload()
    assert len(embed) < 6000
    for field in embed.fields:
        assert len(field.name.encode("utf-16-le")) // 2 <= 256
        assert len(field.value.encode("utf-16-le")) // 2 <= 1024
        assert "@everyone" not in field.value
    assert all(len(o.label.encode("utf-16-le")) // 2 <= 100
               for o in agenda.view.choose_event.options)


@pytest.mark.asyncio
async def test_selection_opens_private_details_from_latest_record(agenda):
    agenda.view.choose_event._values = ["1"]
    agenda.records["1"]["titre"] = "Titre modifié"
    click = interaction()
    await agenda.view.choose_event.callback(click)
    detail = click.followup.send.await_args.kwargs["view"]
    try:
        assert click.followup.send.await_args.kwargs["ephemeral"] is True
        assert click.followup.send.await_args.kwargs["embed"].title == "Titre modifié"
    finally:
        detail.stop()


@pytest.mark.asyncio
async def test_stale_selection_cannot_open_cancelled_activity(agenda):
    agenda.view.choose_event._values = ["1"]
    agenda.records["1"]["cancelled"] = True
    click = interaction()
    await agenda.view.choose_event.callback(click)
    assert "annulée" in click.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_detail_buttons_follow_capacity_membership_and_live_cancellation(agenda):
    action = AsyncMock()
    detail = ActivityDetailView(8, agenda.guild, lambda: agenda.records, "1", action, lambda: NOW)
    try:
        detail.build_embed()
        assert not detail.join.disabled
        assert detail.leave.disabled
        agenda.records["1"]["participants"] = list(range(8))
        detail.build_embed()
        assert not detail.join.disabled
        assert detail.join.label == "Liste d’attente"
        agenda.records["1"]["cancelled"] = True
        click = interaction(user_id=8)
        await detail.join.callback(click)
        action.assert_not_awaited()
        assert "indisponible" in click.edit_original_response.await_args.kwargs["embed"].title
    finally:
        detail.stop()


@pytest.mark.asyncio
async def test_detail_reuses_action_callback_and_refreshes(agenda):
    action = AsyncMock()
    detail = ActivityDetailView(8, agenda.guild, lambda: agenda.records, "1", action, lambda: NOW)
    try:
        click = interaction(user_id=8)
        await detail.join.callback(click)
        action.assert_awaited_once_with(click, "1", "join")
        click.edit_original_response.assert_awaited_once()
    finally:
        detail.stop()


@pytest.mark.asyncio
async def test_past_activity_cannot_be_joined_from_stale_button(agenda):
    agenda.records["1"]["date_str"] = "2026-09-11 20:00:00"
    action = AsyncMock()
    detail = ActivityDetailView(8, agenda.guild, lambda: agenda.records, "1", action, lambda: NOW)
    try:
        click = interaction(user_id=8)
        await detail.join.callback(click)
        action.assert_not_awaited()
        assert "passé" in click.followup.send.await_args.args[0]
    finally:
        detail.stop()


@pytest.mark.asyncio
async def test_full_text_export_preserves_long_description_and_closes_file(agenda):
    agenda.records["1"]["description"] = "Texte " * 1000
    detail = ActivityDetailView(7, agenda.guild, lambda: agenda.records, "1", None, lambda: NOW)
    captured = []

    async def send(**kwargs):
        captured.append(kwargs["file"].fp.read().decode("utf-8"))

    try:
        click = interaction()
        click.followup.send.side_effect = send
        await detail.full_text.callback(click)
        assert agenda.records["1"]["description"].strip() in captured[0]
        assert click.followup.send.await_args.kwargs["file"].fp.closed
    finally:
        detail.stop()


@pytest.mark.asyncio
async def test_today_recomputes_the_paris_date_after_midnight(agenda):
    clock = [NOW]
    agenda.view.clock = lambda: clock[0]
    clock[0] = datetime(2026, 9, 30, 22, 30, tzinfo=timezone.utc)
    click = interaction()
    await agenda.view.go_today.callback(click)
    assert agenda.view.highlight_date == date(2026, 10, 1)
    assert agenda.view.state.anchor == date(2026, 10, 1)


@pytest.mark.asyncio
async def test_upcoming_button_selects_the_page_containing_the_event(agenda):
    agenda.records.update({
        str(i): record("2026-09-11 20:00:00") for i in range(2, 21)
    })
    click = interaction()
    await agenda.view.upcoming(click)
    assert agenda.view.state.page == 3
    assert "1" in [option.value for option in agenda.view.choose_event.options]


@pytest.mark.asyncio
async def test_monthly_edit_upload_refusal_falls_back_to_text_and_closes_file(agenda):
    view = agenda.view
    view.renderer.render = AsyncMock(return_value=b"png")
    click = interaction()
    failure = discord.HTTPException(SimpleNamespace(status=413, reason="Too large"), "upload refused")
    click.edit_original_response.side_effect = [failure, click.message]
    await view.change(click, lambda state: replace(state, mode="mois"))
    assert view.state.mode == "mois"
    assert click.edit_original_response.await_count == 2
    first, second = click.edit_original_response.await_args_list
    assert first.kwargs["attachments"][0].fp.closed
    assert second.kwargs["attachments"] == []
    assert not second.kwargs["embed"].image.url
    assert "Aperçu indisponible" in second.kwargs["embed"].fields[-1].name
    assert not view.choose_event.disabled
    assert view.message is click.message
