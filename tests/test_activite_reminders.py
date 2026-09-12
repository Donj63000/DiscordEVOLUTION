import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import activite
from test_activite_init import FakeConsoleChannel, FakeMessage


class RecordingConsoleChannel(FakeConsoleChannel):
    """Je conserve les snapshots envoyés pour simuler leur restauration sans Discord."""

    def __init__(self, author):
        super().__init__([])
        self.author = author

    async def send(self, content, *, file=None, **kwargs):
        message = FakeMessage(self.author, content)
        if file is not None:
            file.fp.seek(0)
            message.attachments.append(
                SimpleNamespace(filename=file.filename, read=AsyncMock(return_value=file.fp.read()))
            )
        self._messages.insert(0, message)
        return message

    async def latest_payload(self):
        message = self._messages[0]
        if message.attachments:
            return json.loads(await message.attachments[0].read())
        return json.loads(message.content.split("```json\n", 1)[1].rsplit("\n```", 1)[0])


@pytest.fixture
def clock(monkeypatch):
    class FrozenDateTime(datetime):
        """Je simule un serveur en UTC tout en laissant le code choisir son fuseau."""

        current = datetime(2026, 9, 11, 19, 32, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.current.replace(tzinfo=None)
            return cls.current.astimezone(tz)

    monkeypatch.setattr(activite, "datetime", FrozenDateTime)
    return FrozenDateTime


@pytest.fixture
def activity_env(monkeypatch, tmp_path, clock):
    role = SimpleNamespace(id=42, delete=AsyncMock())
    guild = SimpleNamespace(id=100, get_role=Mock(return_value=role),
                            get_member=Mock(return_value=None), get_channel=Mock(return_value=None))
    bot = SimpleNamespace(user=object(), guilds=[guild], is_ready=lambda: True, add_view=Mock())
    organisation = SimpleNamespace(id=300, guild=guild, send=AsyncMock())
    console = RecordingConsoleChannel(bot.user)
    path = tmp_path / "activities_data.json"
    monkeypatch.setattr(activite, "DATA_FILE", str(path))
    monkeypatch.setenv("DISCORD_HISTORY_MIN_INTERVAL", "0")
    monkeypatch.setenv("ACTIVITE_HISTORY_LIMIT", "200")
    cog = activite.ActiviteCog(bot)
    cog.initialized = True
    cog._resolve_organisation_channel = lambda _guild: organisation
    cog._resolve_console_channel = lambda _guild: console
    return SimpleNamespace(
        cog=cog, bot=bot, guild=guild, role=role, organisation=organisation,
        console=console, path=path,
    )


def add_activity(env, start=None, *, event_id="1", flags=(False, False), cancelled=False):
    event = activite.ActiviteData(
        event_id, "Donjon Blop", start or datetime(2026, 9, 11, 23), "Description", 7, 42,
        reminder_24_sent=flags[0], reminder_1_sent=flags[1],
    )
    event.participants = [7, 8]
    event.cancelled = cancelled
    env.cog.activities_data["events"][event_id] = event.to_dict()
    env.cog.activities_data["next_id"] = max(int(event_id) + 1, env.cog.activities_data["next_id"])
    return event


@pytest.mark.asyncio
async def test_reported_evening_reminder_uses_actual_paris_start(activity_env, clock, caplog):
    env = activity_env
    event = add_activity(env)
    assert (activite._activity_datetime_utc(event.date_obj) - clock.current).total_seconds() == 5280

    with caplog.at_level(logging.DEBUG, logger="activite"):
        await env.cog.check_events_loop()

    env.organisation.send.assert_awaited_once()
    sent = env.organisation.send.await_args
    assert "Début le 11/09/2026 à 23:00 (heure de Paris) • <t:1789160400:R>" in sent.args[0]
    assert "<@7> <@8>" in sent.args[0]
    assert sent.kwargs["allowed_mentions"].roles is False
    assert sent.kwargs["allowed_mentions"].everyone is False
    payload = await env.console.latest_payload()
    assert payload["events"]["1"]["reminder_24_sent"] is True
    assert payload["events"]["1"]["reminder_1_sent"] is False
    assert payload["events"]["1"]["date_str"] == "2026-09-11 23:00:00"
    assert "event_id=1 echeance=24h time_left_seconds=5280" in caplog.text
    assert "rappel envoye event_id=1 echeance=24h" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remaining_seconds,expected_flags",
    [
        (86401, (False, False)),
        (86400, (True, False)),
        (86399, (True, False)),
        (3601, (True, False)),
        (3600, (True, True)),
        (3599, (True, True)),
        (1, (True, True)),
        (0, None),
        (-1, None),
    ],
)
async def test_reminder_boundaries(activity_env, clock, remaining_seconds, expected_flags):
    env = activity_env
    start = (clock.current + timedelta(seconds=remaining_seconds)).astimezone(activite.PARIS)
    add_activity(env, start.replace(tzinfo=None))

    await env.cog.check_events_loop()

    if expected_flags is None:
        env.organisation.send.assert_not_awaited()
        env.role.delete.assert_awaited_once_with(reason="Activité terminée")
        stored = env.cog.activities_data["events"]["1"]
        assert stored["closed"] is True
        assert stored["participants"] == [7, 8]
        assert (await env.console.latest_payload())["events"]["1"] == stored
    else:
        event = env.cog.activities_data["events"]["1"]
        assert (event["reminder_24_sent"], event["reminder_1_sent"]) == expected_flags
        assert env.organisation.send.await_count == int(any(expected_flags))
        env.role.delete.assert_not_awaited()
        if any(expected_flags):
            assert json.loads(env.path.read_text(encoding="utf-8")) == env.cog.activities_data
            assert await env.console.latest_payload() == env.cog.activities_data
        else:
            assert not env.path.exists()
            assert env.console._messages == []


@pytest.mark.asyncio
async def test_first_reminder_then_only_one_final_reminder(activity_env, clock):
    env = activity_env
    add_activity(env)
    await env.cog.check_events_loop()
    await env.cog.check_events_loop()
    assert env.organisation.send.await_count == 1

    clock.current = datetime(2026, 9, 11, 20, 4, tzinfo=timezone.utc)
    await env.cog.check_events_loop()
    await env.cog.check_events_loop()

    assert env.organisation.send.await_count == 2
    assert len(env.console._messages) == 1
    assert (await env.console.latest_payload())["events"]["1"]["reminder_1_sent"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [(False, False), (True, False), (False, True), (True, True)])
async def test_last_hour_never_catches_up_both_reminders(activity_env, clock, flags):
    env = activity_env
    clock.current = datetime(2026, 9, 11, 20, 30, tzinfo=timezone.utc)
    add_activity(env, flags=flags)

    await env.cog.check_events_loop()
    await env.cog.check_events_loop()

    assert env.organisation.send.await_count == int(not flags[1])
    if not flags[1]:
        event = (await env.console.latest_payload())["events"]["1"]
        assert event["reminder_24_sent"] is event["reminder_1_sent"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes_remaining", [30, 88])
async def test_failed_send_is_retried_before_marking_and_persisting(
    activity_env, clock, caplog, minutes_remaining,
):
    env = activity_env
    start = (clock.current + timedelta(minutes=minutes_remaining)).astimezone(activite.PARIS)
    original = add_activity(env, start.replace(tzinfo=None)).to_dict()
    env.organisation.send.side_effect = RuntimeError("Discord indisponible")

    with caplog.at_level(logging.WARNING, logger="activite"):
        await env.cog.check_events_loop()

    assert env.cog.activities_data["events"]["1"] == original
    assert not env.path.exists()
    assert env.console._messages == []
    assert "rappel echoue event_id=1 echeance=" in caplog.text

    env.organisation.send.side_effect = None
    clock.current += timedelta(minutes=5)
    await env.cog.check_events_loop()
    await env.cog.check_events_loop()

    assert env.organisation.send.await_count == 2
    event = (await env.console.latest_payload())["events"]["1"]
    assert event["reminder_24_sent"] is True
    assert event["reminder_1_sent"] is (minutes_remaining <= 60)
    assert json.loads(env.path.read_text(encoding="utf-8")) == env.cog.activities_data


@pytest.mark.asyncio
async def test_retry_after_crossing_last_hour_sends_only_final_reminder(activity_env, clock):
    env = activity_env
    add_activity(env)
    env.organisation.send.side_effect = RuntimeError("Discord indisponible")
    await env.cog.check_events_loop()
    env.organisation.send.reset_mock(side_effect=True)
    clock.current = datetime(2026, 9, 11, 20, 30, tzinfo=timezone.utc)

    await env.cog.check_events_loop()
    await env.cog.check_events_loop()

    env.organisation.send.assert_awaited_once()
    event = (await env.console.latest_payload())["events"]["1"]
    assert event["reminder_24_sent"] is event["reminder_1_sent"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes_remaining", [30, 88])
@pytest.mark.parametrize("attachment", [False, True])
async def test_console_restoration_preserves_reminders_and_historical_data(
    activity_env, clock, minutes_remaining, attachment,
):
    env = activity_env
    start = (clock.current + timedelta(minutes=minutes_remaining)).astimezone(activite.PARIS)
    event = add_activity(env, start.replace(tzinfo=None))
    if attachment:
        event.description = "Description " * 200
    original = event.to_dict()
    env.cog.activities_data["events"]["1"] = original.copy()
    await env.cog.check_events_loop()
    assert bool(env.console._messages[0].attachments) is attachment
    saved = await env.console.latest_payload()
    assert {key: value for key, value in saved["events"]["1"].items() if "reminder" not in key} == {
        key: value for key, value in original.items() if "reminder" not in key
    }
    env.path.write_text('{"next_id": 1, "events": {}}', encoding="utf-8")
    restored = activite.ActiviteCog(env.bot)
    restored._resolve_console_channel = env.cog._resolve_console_channel
    restored._resolve_organisation_channel = env.cog._resolve_organisation_channel

    await restored.initialize_data()
    await restored.check_events_loop()

    assert restored.initialized is True
    assert restored.activities_data == activite.migrate_snapshot(saved, env.guild.id)
    env.organisation.send.assert_awaited_once()
    assert len(env.console._messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled_start", [datetime(2026, 9, 11, 23), datetime(2026, 9, 10, 23)])
async def test_cancelled_activity_keeps_history_and_cleans_legacy_role(activity_env, cancelled_start):
    env = activity_env
    original = add_activity(env, cancelled_start, cancelled=True).to_dict()

    await env.cog.check_events_loop()

    env.organisation.send.assert_not_awaited()
    env.role.delete.assert_awaited_once()
    retained = env.cog.activities_data["events"]["1"]
    assert retained["participants"] == original["participants"]
    assert retained["cancelled"] and retained["closed"]
    assert retained["role_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["not_ready", "not_initialized", "no_channel"])
async def test_reminders_wait_until_bot_and_channel_are_available(activity_env, reason):
    env = activity_env
    original = add_activity(env).to_dict()
    if reason == "not_ready":
        env.bot.is_ready = lambda: False
    elif reason == "not_initialized":
        env.cog.initialized = False
        env.cog.initialize_data = AsyncMock()
    else:
        env.cog._resolve_organisation_channel = lambda _guild: None

    await env.cog.check_events_loop()

    env.organisation.send.assert_not_awaited()
    assert env.cog.activities_data["events"]["1"] == original
    assert env.console._messages == []


@pytest.mark.asyncio
async def test_reminder_without_role_does_not_add_a_broad_mention(activity_env):
    event = add_activity(activity_env)
    event.role_id = None

    assert await activity_env.cog.envoyer_rappel(activity_env.organisation, event, "24h") is True

    message = activity_env.organisation.send.await_args.args[0]
    assert "<@7> <@8>" in message
    assert activity_env.organisation.send.await_args.kwargs["allowed_mentions"].roles is False
    assert "@everyone" not in message
    assert "@here" not in message
    assert "\n\n" not in message
    assert "<t:1789160400:R>" in message


@pytest.mark.asyncio
async def test_cancelled_send_propagates_without_marking_reminder(activity_env):
    env = activity_env
    original = add_activity(env).to_dict()
    env.organisation.send.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await env.cog.check_events_loop()

    assert env.cog.activities_data["events"]["1"] == original
    assert env.console._messages == []


@pytest.mark.parametrize("flags", [(False, False), (True, False), (True, True)])
def test_activity_serialization_preserves_legacy_format(activity_env, flags):
    original = add_activity(activity_env, flags=flags).to_dict()
    assert activite.ActiviteData.from_dict(original).to_dict() == original
    without_flags = {key: value for key, value in original.items() if "reminder" not in key}
    restored = activite.ActiviteData.from_dict(without_flags)
    assert restored.reminder_24_sent is restored.reminder_1_sent is False
    assert activite.utc(restored.date_obj) == datetime(2026, 9, 11, 21, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("date_text", ["11/09/2026 23:00", "11/09/2026 23:30", "12/09/2026 23:00"])
async def test_modification_resets_reminders_only_when_start_changes(activity_env, flags, date_text):
    env = activity_env
    add_activity(env, flags=flags)
    ctx = SimpleNamespace(guild=env.guild, author=SimpleNamespace(id=7), send=AsyncMock())

    await env.cog.command_modifier(ctx, f"1 {date_text} Nouvelle description")

    saved = (await env.console.latest_payload())["events"]["1"]
    expected_flags = flags if date_text == "11/09/2026 23:00" else (False, False)
    assert (saved["reminder_24_sent"], saved["reminder_1_sent"]) == expected_flags
    assert saved["date_str"] == datetime.strptime(date_text, "%d/%m/%Y %H:%M").strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert saved["description"] == "Nouvelle description"
    assert saved["participants"] == [7, 8]
    assert saved["role_id"] == 42
    assert json.loads(env.path.read_text(encoding="utf-8")) == env.cog.activities_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now_utc,start_paris,seconds,final_reminder",
    [
        (datetime(2026, 7, 1, 12), datetime(2026, 7, 1, 14, 30), 1800, True),
        (datetime(2026, 1, 1, 12), datetime(2026, 1, 1, 13, 30), 1800, True),
        (datetime(2026, 3, 29, 0, 30), datetime(2026, 3, 29, 3, 15), 2700, True),
        (datetime(2026, 10, 25, 0, 30), datetime(2026, 10, 25, 3, 15), 6300, False),
    ],
)
async def test_reminder_uses_elapsed_time_across_seasons_and_clock_changes(
    activity_env, clock, now_utc, start_paris, seconds, final_reminder,
):
    env = activity_env
    clock.current = now_utc.replace(tzinfo=timezone.utc)
    add_activity(env, start_paris)
    expected_start = clock.current + timedelta(seconds=seconds)
    assert activite._activity_datetime_utc(start_paris) == expected_start

    await env.cog.check_events_loop()

    env.organisation.send.assert_awaited_once()
    assert f"<t:{int(expected_start.timestamp())}:R>" in env.organisation.send.await_args.args[0]
    saved = (await env.console.latest_payload())["events"]["1"]
    assert saved["reminder_24_sent"] is True
    assert saved["reminder_1_sent"] is final_reminder


def test_explicit_timezone_is_preserved_by_utc_conversion():
    start = datetime(2026, 9, 11, 21, tzinfo=timezone.utc)
    assert activite._activity_datetime_utc(start) == start
    assert activite._activity_datetime_utc(start.astimezone(activite.PARIS)) == start


@pytest.mark.asyncio
async def test_list_and_cleanup_use_same_paris_clock(activity_env, clock):
    env = activity_env
    clock.current = datetime(2026, 9, 11, 21, 30, tzinfo=timezone.utc)
    add_activity(env, datetime(2026, 9, 11, 23), event_id="1")
    add_activity(env, datetime(2026, 9, 11, 23, 30), event_id="2")
    add_activity(env, datetime(2026, 9, 12, 1), event_id="3")
    add_activity(env, datetime(2026, 9, 12, 0, 15), event_id="4")
    add_activity(env, datetime(2026, 9, 12, 0, 10), event_id="5", cancelled=True)
    ctx = SimpleNamespace(guild=env.guild, author=SimpleNamespace(id=7), send=AsyncMock())

    await env.cog.command_liste(ctx)

    fields = ctx.send.await_args.kwargs["embed"].fields
    assert len(fields) == 2
    assert "#4" in fields[0].name
    assert "#3" in fields[1].name
    ctx.send.await_args.kwargs["view"].stop()
    await env.cog.check_events_loop()
    assert set(env.cog.activities_data["events"]) == {"1", "2", "3", "4", "5"}
    assert env.cog.activities_data["events"]["1"]["closed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now_utc,today_paris",
    [(datetime(2026, 9, 30, 22, 30), date(2026, 10, 1)),
     (datetime(2026, 1, 31, 23, 30), date(2026, 2, 1))],
)
async def test_calendar_today_uses_paris_in_view_and_command(
    activity_env, clock, monkeypatch, now_utc, today_paris,
):
    import matplotlib.image as mpimg

    clock.current = now_utc.replace(tzinfo=timezone.utc)
    author = SimpleNamespace(id=7)
    view = activite.CalendrierView(author, {}, None)
    try:
        assert view.highlight_date == today_paris
        assert (view.year, view.month) == (today_paris.year, today_paris.month)
    finally:
        view.stop()

    monkeypatch.setattr(mpimg, "imread", Mock(return_value=None))
    monkeypatch.setattr(activite.CalendrierView, "build_file", Mock(return_value=object()))
    ctx = SimpleNamespace(guild=activity_env.guild, author=author, send=AsyncMock())
    await activite.ActiviteCog.afficher_calendrier.callback(activity_env.cog, ctx)
    sent_view = ctx.send.await_args.kwargs["view"]
    try:
        assert sent_view.highlight_date == today_paris
        assert (sent_view.year, sent_view.month) == (today_paris.year, today_paris.month)
    finally:
        sent_view.stop()
