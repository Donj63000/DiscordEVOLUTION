"""Simulateurs hors réseau des méthodes publiques utilisées par le collecteur."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from utils.enquete_core import Alias, Config, EnqueteError, ReportMeta, Window, Workspace, snowflake_at
from utils.enquete_service import (
    AccessScope, Collector, as_list, identity_snapshot, internal_snapshot,
    names_of, resolve_target, retry_read, staff, to_record,
)

UTC = timezone.utc
NOW = datetime(2026, 1, 1, tzinfo=UTC)
GID, TARGET, BOT, REQUESTER, READER = 900000000000000001, 111000000000000001, 2, 3, 4
DEST, SOURCE, SOURCE2, ROLE = 910000000000000001, 20, 21, 80


def permissions(**kw):
    values = dict(view_channel=True, read_message_history=True, send_messages=True,
                  attach_files=True, connect=True, manage_threads=False, administrator=False,
                  view_audit_log=True)
    values.update(kw)
    return NS(**values)


def role(rid=ROLE, name="Staff", value=0):
    return NS(id=rid, name=name, permissions=NS(value=value), is_default=lambda: rid == GID)


def member(uid, name, *, is_staff=False, administrator=False, bot=False):
    return NS(id=uid, name=name, global_name=None, nick=None, display_name=name,
              roles=[role(GID, "@everyone"), *([role()] if is_staff else [])],
              guild_permissions=permissions(administrator=administrator), bot=bot,
              created_at=NOW-timedelta(days=1000), joined_at=NOW-timedelta(days=100))


class Denied(Exception):
    status = 403


class Gone(Exception):
    status = 404


class Channel:
    def __init__(self, guild, cid=SOURCE, name="discussion", kind=0, *, visible=None,
                 parent=None, category_id=None):
        self.guild, self.id, self.name, self.type = guild, cid, name, NS(value=kind)
        self.visible = set(visible if visible is not None else [BOT, REQUESTER, READER, TARGET])
        self.parent, self.parent_id = parent, getattr(parent, "id", None)
        self.category_id, self.category = category_id, None
        self.overrides = {}
        self.nsfw = False
        self.messages = []
        self.history_calls = []
        self.history_error = None
        self.thread_members = set()
        self.member_calls = []
        self.public_archives, self.private_archives, self.archive_calls = [], [], []

    def permissions_for(self, who):
        return permissions(view_channel=who.id in self.visible, **self.overrides.get(who.id, {}))

    def is_nsfw(self):
        return self.nsfw

    async def history(self, *, limit, before, oldest_first=False):
        self.history_calls.append(dict(limit=limit, before=before.id, oldest_first=oldest_first))
        if self.history_error:
            raise self.history_error
        for m in sorted((m for m in self.messages if m.id < before.id), key=lambda m: m.id, reverse=True)[:limit]:
            yield m

    async def archived_threads(self, **kw):
        self.archive_calls.append(kw)
        for thread in self.private_archives if kw.get("private") else self.public_archives:
            yield thread

    async def fetch_member(self, uid):
        self.member_calls.append(uid)
        if uid not in self.thread_members:
            raise Gone()
        return NS(id=uid)


class Guild:
    def __init__(self):
        self.id, self.name, self.filesize_limit = GID, "Serveur de test", 8*1024**2
        self.default_role = role(GID, "@everyone")
        self.roles = [self.default_role, role()]
        self.fresh_roles = self.roles.copy()
        self.members = [member(BOT, "Evo", bot=True), member(REQUESTER, "Modérateur", is_staff=True),
                        member(READER, "Staff2", is_staff=True), member(TARGET, "Illunerah")]
        self.me = self.members[0]
        self.channels = [Channel(self, DEST, "Général-Staff", visible=[BOT, REQUESTER, READER]),
                         Channel(self), Channel(self, SOURCE2, "autre")]
        self.active = []
        self.audits = []
        self.active_error = None

    async def fetch_roles(self):
        return self.fresh_roles

    async def fetch_members(self, *, limit):
        assert limit is None
        for m in self.members:
            yield m

    async def fetch_channels(self):
        return self.channels.copy()

    async def fetch_channel(self, cid):
        for channel in [*self.channels, *self.active]:
            if channel.id == cid:
                return channel
        raise Gone()

    async def active_threads(self):
        if self.active_error:
            raise self.active_error
        return self.active.copy()

    async def audit_logs(self, **kw):
        for entry in self.audits[:kw["limit"]]:
            yield entry


def message(channel, n=1, *, author=None, content="bonjour", created=None):
    created = created or NOW-timedelta(minutes=n)
    return NS(id=snowflake_at(created)+n, guild=channel.guild, channel=channel,
              author=author or channel.guild.members[-1], content=content,
              created_at=created, edited_at=None, raw_mentions=[], reference=None,
              attachments=[], embeds=[], stickers=[], poll=None, pinned=False,
              webhook_id=None, message_snapshots=[], reactions=[])


def meta(**kw):
    values = dict(job_id="012345abcdef", guild_id=GID, guild_name="Test", target_id=TARGET,
                  requester_id=REQUESTER, reason="Test", window=Window.parse(now=NOW),
                  identity={}, aliases=[Alias("Illunerah", "actuel")], approximate=True,
                  context=0, deep_reactions=False)
    values.update(kw)
    return ReportMeta(**values)


class TargetTests(unittest.TestCase):
    def test_exact_name_nickname_and_normalised_separator(self):
        m = member(TARGET, "coca-cola")
        self.assertEqual(resolve_target("Côca Cola", [m])[0], TARGET)
        m.nick = "illun"
        self.assertEqual(resolve_target("illun", [m])[0], TARGET)

    def test_ambiguous_names_refuse_to_guess(self):
        with self.assertRaisesRegex(EnqueteError, "ambigu"):
            resolve_target("illun", [member(TARGET, "illun"), member(99, "ILLUN")])

    def test_absent_id_is_accepted_without_external_lookup(self):
        uid, obj = resolve_target(str(TARGET), [])
        self.assertEqual(uid, TARGET)
        self.assertIsNone(obj)
        self.assertIn("non recherchée", identity_snapshot(obj, uid)["Identité globale"])

    def test_partial_or_unknown_name_refused(self):
        with self.assertRaises(EnqueteError):
            resolve_target("illun", [member(TARGET, "Illunerah")])

    def test_role_id_takes_priority_over_name(self):
        m = member(3, "staff", is_staff=True)
        self.assertTrue(staff(m, Config()))
        self.assertFalse(staff(m, Config(role_ids=frozenset({999}))))
        m.guild_permissions.administrator = True
        self.assertTrue(staff(m, Config(role_ids=frozenset({999}))))


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guild = Guild()
        self.cfg = Config(staff_channel=DEST)
        self.scope = AccessScope(self.guild, self.guild.me, REQUESTER, self.cfg, "ici",
                                 current_channel_id=DEST)
        await self.scope.refresh()
        self.scope.bind_target(TARGET)
        self.dest, self.source = self.guild.channels[:2]

    async def test_authorised_scope(self):
        self.assertTrue((await self.scope.can_read(self.source))[0])
        self.assertEqual({m.id for m in self.scope.readers}, {REQUESTER, READER})

    async def test_missing_destination_never_guesses_name(self):
        with self.assertRaisesRegex(EnqueteError, "destination:ici"):
            AccessScope(self.guild, self.guild.me, REQUESTER, Config(), "staff")

    async def test_current_channel_without_config_and_with_configured_destinations(self):
        for cfg in (Config(), Config(staff_channel=SOURCE, console_channel=SOURCE2)):
            scope = AccessScope(self.guild, self.guild.me, REQUESTER, cfg, "ici",
                                current_channel_id=DEST)
            await scope.refresh()
            scope.bind_target(TARGET)
            self.assertEqual(scope.destination_ids, [DEST])
            self.assertEqual(scope.destinations, [self.dest])

    async def test_current_channel_missing_or_not_text_never_falls_back(self):
        with self.assertRaisesRegex(EnqueteError, "salon textuel"):
            AccessScope(self.guild, self.guild.me, REQUESTER, self.cfg, "ici")
        for kind in (5, 10, 11, 12, 2, 15):
            with self.subTest(kind=kind):
                self.source.type = NS(value=kind)
                scope = AccessScope(self.guild, self.guild.me, REQUESTER, self.cfg, "ici",
                                    current_channel_id=SOURCE)
                with self.assertRaisesRegex(EnqueteError, "textuel ordinaire"):
                    await scope.refresh()
        scope = AccessScope(self.guild, self.guild.me, REQUESTER, self.cfg, "ici",
                            current_channel_id=999)
        with self.assertRaisesRegex(EnqueteError, "textuel ordinaire"):
            await scope.refresh()

    async def test_explicit_destinations_ignore_current_channel(self):
        console = Channel(self.guild, 88, "console", visible=[BOT, REQUESTER, READER])
        self.guild.channels.append(console)
        cfg = replace(self.cfg, console_channel=88)
        for destination, expected in (("staff", [DEST]), ("console", [88]),
                                      ("les-deux", [DEST, 88])):
            scope = AccessScope(self.guild, self.guild.me, REQUESTER, cfg, destination,
                                current_channel_id=SOURCE)
            await scope.refresh()
            self.assertEqual(scope.destination_ids, expected)

    async def test_public_destination_rejected(self):
        self.dest.visible.add(GID)
        with self.assertRaisesRegex(EnqueteError, "@everyone"):
            await self.scope.refresh()

    async def test_announcements_destination_rejected(self):
        self.dest.type = NS(value=5)
        with self.assertRaises(EnqueteError):
            await self.scope.refresh()

    async def test_nonstaff_reader_rejected_even_other_bot(self):
        outsider = member(777, "AutreBot", bot=True)
        self.guild.members.append(outsider)
        self.dest.visible.add(777)
        with self.assertRaisesRegex(EnqueteError, "sans autorisation"):
            await self.scope.refresh()

    async def test_target_cannot_read_output(self):
        target = self.guild.members[-1]
        target.roles.append(role())
        self.dest.visible.add(TARGET)
        with self.assertRaisesRegex(EnqueteError, "recherchée"):
            await self.scope.refresh()

    async def test_missing_bot_attachment_permission(self):
        self.dest.overrides[BOT] = dict(attach_files=False)
        with self.assertRaises(EnqueteError):
            await self.scope.refresh()

    async def test_requester_role_revoked(self):
        self.guild.members[1].roles = []
        with self.assertRaises(EnqueteError):
            await self.scope.refresh()

    async def test_rejects_stale_role_permission_cache(self):
        self.guild.fresh_roles = [role(GID), role(value=123)]
        with self.assertRaisesRegex(EnqueteError, "cache"):
            await self.scope.refresh()

    async def test_source_requires_every_reader_history_permission(self):
        self.source.overrides[READER] = dict(read_message_history=False)
        self.assertFalse((await self.scope.can_read(self.source))[0])

    async def test_other_guild_source_rejected(self):
        foreign = Channel(NS(id=9))
        self.assertEqual((await self.scope.can_read(foreign))[1], "autre serveur")

    async def test_explicit_parent_and_category_exclusions(self):
        thread = Channel(self.guild, 40, "fil", 11, parent=self.source)
        for cid in [40, SOURCE, 444]:
            self.source.category_id = 444
            self.scope.cfg = replace(self.cfg, excluded=frozenset({cid}))
            self.assertFalse((await self.scope.can_read(thread))[0])

    async def test_nsfw_does_not_leak_to_unrestricted_destination(self):
        self.source.nsfw = True
        self.assertFalse((await self.scope.can_read(self.source))[0])
        self.dest.nsfw = True
        self.assertTrue((await self.scope.can_read(self.source))[0])

    async def test_voice_chat_requires_connect(self):
        self.source.type = NS(value=2)
        self.source.overrides[READER] = dict(connect=False)
        self.assertFalse((await self.scope.can_read(self.source))[0])

    async def test_private_thread_requires_actual_membership(self):
        thread = Channel(self.guild, 40, "privé", 12, parent=self.source)
        thread.thread_members = {BOT, REQUESTER}
        self.assertFalse((await self.scope.can_read(thread))[0])
        thread.thread_members.add(READER)
        self.scope.private_members.clear()
        self.assertTrue((await self.scope.can_read(thread))[0])
        calls = len(thread.member_calls)
        self.assertTrue((await self.scope.can_read(thread))[0])
        self.assertEqual(len(thread.member_calls), calls)

    async def test_private_thread_manager_does_not_need_invitation(self):
        thread = Channel(self.guild, 40, "privé", 12, parent=self.source)
        for uid in [BOT, REQUESTER, READER]:
            self.source.overrides[uid] = dict(manage_threads=True)
        self.assertTrue((await self.scope.can_read(thread))[0])
        self.assertEqual(thread.member_calls, [])

    async def test_revoked_source_permissions_abort_publication(self):
        self.source.visible.remove(READER)
        with self.assertRaisesRegex(EnqueteError, "droits"):
            await self.scope.revalidate({SOURCE: self.source})

    async def test_deleted_source_aborts_publication(self):
        self.guild.channels.remove(self.source)
        with self.assertRaisesRegex(EnqueteError, "disparu"):
            await self.scope.revalidate({SOURCE: self.source})

    async def test_both_destinations_use_union_of_readers(self):
        extra = member(777, "staff3", is_staff=True)
        self.guild.members.append(extra)
        console = Channel(self.guild, 88, "console", visible=[BOT, REQUESTER, 777])
        self.guild.channels.append(console)
        scope = AccessScope(self.guild, self.guild.me, REQUESTER, replace(self.cfg, console_channel=88), "les-deux")
        await scope.refresh()
        self.assertEqual({m.id for m in scope.readers}, {REQUESTER, READER, 777})
        self.assertFalse((await scope.can_read(self.source))[0])


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Workspace(Path(self.temp.name)/"index.sqlite3")
        self.guild = Guild()
        self.cfg = Config(staff_channel=DEST)
        self.scope = AccessScope(self.guild, self.guild.me, REQUESTER, self.cfg, "staff")
        await self.scope.refresh()
        self.scope.bind_target(TARGET)
        self.data = {}
        self.bot = NS(get_cog=lambda name: self.data.get(name))
        self.meta = meta()
        self.collector = Collector(self.bot, self.scope, self.work, self.meta, self.cfg)
        self.source, self.other = self.guild.channels[1:]

    async def asyncTearDown(self):
        self.work.close()
        self.temp.cleanup()

    async def test_multiple_pages_are_decreasing_without_duplicates(self):
        self.source.messages = [message(self.source, n) for n in range(1, 252)]
        await self.collector.collect()
        self.assertEqual(self.work.total, 251)
        calls = self.source.history_calls
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(a["before"] > b["before"] for a, b in zip(calls, calls[1:])))
        self.assertEqual(self.work.statistics(TARGET)["authored"], 251)
        self.assertFalse(self.meta.partial)

    async def test_global_budget_uses_round_robin_not_only_first_channel(self):
        self.source.messages = [message(self.source, n) for n in range(1, 151)]
        self.other.messages = [message(self.other, n, created=NOW-timedelta(minutes=n, seconds=1)) for n in range(1, 151)]
        self.collector.cfg = replace(self.cfg, max_messages=201)
        await self.collector.collect()
        self.assertEqual(self.work.total, 201)
        counts = dict(self.work.db.execute("SELECT cid,COUNT(*) FROM messages GROUP BY cid"))
        self.assertGreaterEqual(counts[SOURCE], 100)
        self.assertGreaterEqual(counts[SOURCE2], 100)
        self.assertTrue(self.meta.partial)

    async def test_window_excludes_old_content(self):
        self.meta.window = Window(NOW-timedelta(minutes=5), NOW)
        self.source.messages = [message(self.source, n) for n in range(1, 10)]
        await self.collector.collect()
        self.assertEqual(self.work.total, 5)

    async def test_channel_failure_is_partial_but_other_channels_continue(self):
        self.source.history_error = Denied("THIS RAW ERROR MUST NOT BE EXPORTED")
        self.other.messages = [message(self.other)]
        await self.collector.collect()
        self.assertTrue(self.meta.partial)
        self.assertEqual(self.work.total, 1)
        detail = self.work.db.execute("SELECT detail FROM channels WHERE cid=?", (SOURCE,)).fetchone()[0]
        self.assertIn("Denied", detail)
        self.assertNotIn("RAW ERROR", detail)

    async def test_bot_reports_and_backups_are_not_reingested(self):
        own_report = message(self.source, 1, author=self.guild.me, content="[ENQUETE:012345abcdef] test")
        ordinary = message(self.source, 2, author=self.guild.me, content="Illunerah bonjour")
        backup = message(self.source, 3, author=self.guild.me)
        backup.attachments = [NS(filename="players_data.json")]
        self.source.messages = [own_report, ordinary, backup]
        await self.collector.collect()
        self.assertEqual(self.work.total, 1)
        self.assertEqual(self.collector.skipped_internal, 2)

    async def test_archived_public_and_joined_private_threads_discovered(self):
        public = Channel(self.guild, 40, "ancien", 11, parent=self.source)
        private = Channel(self.guild, 41, "privé", 12, parent=self.source)
        private.thread_members = {BOT, REQUESTER, READER}
        public.messages = [message(public)]
        private.messages = [message(private, 2)]
        self.source.public_archives = [public]
        self.source.private_archives = [private]
        await self.collector.collect()
        self.assertIn(40, self.collector.sources)
        self.assertIn(41, self.collector.sources)
        self.assertEqual(self.work.total, 2)
        self.assertIn({"limit": None, "private": True, "joined": True}, self.source.archive_calls)
        self.assertTrue(all("before" not in call for call in self.source.archive_calls))

    async def test_forum_archives_never_receive_private_keyword(self):
        forum = NS(id=50, guild=self.guild, name="forum", type=NS(value=15), parent_id=None,
                   category_id=None, permissions_for=self.source.permissions_for, is_nsfw=lambda: False)
        calls = []
        async def archives(*, limit):
            calls.append(limit)
            if False:
                yield None
        forum.archived_threads = archives
        self.scope.channels[50] = forum
        await self.collector.discover()
        self.assertEqual(calls, [None])
        self.assertEqual(self.work.db.execute("SELECT status FROM channels WHERE cid=50").fetchone()[0], "CONTENEUR")

    async def test_thread_limit_and_discovery_failure_are_explicit(self):
        self.collector.cfg = replace(self.cfg, max_threads=1)
        self.guild.active = [Channel(self.guild, 40, kind=11, parent=self.source),
                             Channel(self.guild, 41, kind=11, parent=self.source)]
        await self.collector.collect()
        self.assertTrue(self.meta.partial)
        self.assertEqual(self.collector.thread_count, 1)

    async def test_active_listing_failure_is_explicit(self):
        self.guild.active_error = Denied()
        await self.collector.collect()
        self.assertTrue(self.meta.partial)
        self.assertTrue(any("fils actifs" in n for n in self.meta.notes))

    async def test_global_timeout_produces_partial_coverage(self):
        self.collector.cfg = replace(self.cfg, max_seconds=0.01)
        async def slow():
            await self.collector.add_channel(self.source)
            await asyncio.sleep(1)
        self.collector.discover = slow
        await self.collector.collect()
        self.assertTrue(self.meta.partial)
        self.assertEqual(self.work.db.execute("SELECT status FROM channels WHERE cid=?", (SOURCE,)).fetchone()[0], "PARTIEL")

    async def test_current_reaction_membership_queries_one_user_after_target_minus_one(self):
        calls = []
        async def users(**kw):
            calls.append(kw)
            yield NS(id=TARGET)
        reaction = NS(emoji="👍", count=2, normal_count=1, burst_count=1, users=users)
        self.collector.reaction_types = (("normal", 0), ("burst", 1))
        result = await self.collector.target_reactions(NS(reactions=[reaction]))
        self.assertEqual(result, ["👍 (normal)", "👍 (burst)"])
        self.assertTrue(all(c["limit"] == 1 and c["after"].id == TARGET-1 for c in calls))

    async def test_other_reactor_is_not_attributed_to_target(self):
        async def users(**kw):
            yield NS(id=TARGET+1)
        self.collector.reaction_types = (("normal", 0),)
        reaction = NS(emoji="👍", count=1, normal_count=1, users=users)
        self.assertEqual(await self.collector.target_reactions(NS(reactions=[reaction])), [])

    async def test_reaction_failure_is_partial_not_negative_evidence(self):
        async def users(**kw):
            raise Denied()
            yield None
        self.collector.reaction_types = (("normal", 0),)
        reaction = NS(emoji="👍", count=1, normal_count=1, users=users)
        self.assertEqual(await self.collector.target_reactions(NS(reactions=[reaction])), [])
        self.assertTrue(self.meta.partial)

    async def test_legacy_requires_known_channel_and_explicit_naive_timezone(self):
        base = dict(author_id=TARGET, channel_id=SOURCE, timestamp="2025-12-01T10:00:00", content="ancien")
        foreign = dict(base, channel_id=999)
        self.data["StatsCog"] = NS(stats_data={"logs": {"messages_deleted": [base, foreign]}})
        await self.collector.legacy()
        self.assertEqual(self.work.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.collector.cfg = replace(self.cfg, legacy_timezone="UTC")
        await self.collector.legacy()
        self.assertEqual(self.work.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertIn(SOURCE, self.collector.sources)

    async def test_legacy_ambiguous_dst_time_is_not_guessed(self):
        self.collector.cfg = replace(self.cfg, legacy_timezone="Europe/Paris")
        self.data["StatsCog"] = NS(stats_data={"logs": {"messages_deleted": [
            dict(author_id=TARGET, channel_id=SOURCE, timestamp="2025-10-26T02:30:00", content="ancien"),
            dict(author_id=TARGET, channel_id=SOURCE, timestamp="2025-03-30T02:30:00", content="ancien"),
        ]}})
        await self.collector.legacy()
        self.assertEqual(self.work.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertTrue(any("ambigu" in n for n in self.meta.notes))

    async def test_legacy_global_bases_need_manual_guild_binding(self):
        self.data["ModerationCog"] = NS(warnings={str(TARGET): 3})
        self.data["PlayersCog"] = NS(persos_data={str(TARGET): {"main": "AncienPersonnage", "mules": ["AutreNom"]}})
        await self.collector.legacy()
        self.assertEqual(self.meta.identity, {})
        self.collector.cfg = replace(self.cfg, legacy_guild=GID)
        await self.collector.legacy()
        self.assertIn(3, self.meta.identity.values())
        self.assertIn("AncienPersonnage", [a.value for a in self.meta.aliases])

    async def test_legacy_voice_requires_both_channels_authorised(self):
        self.other.visible.remove(READER)
        self.collector.cfg = replace(self.cfg, legacy_timezone="UTC")
        self.data["StatsCog"] = NS(stats_data={"logs": {"voice": [
            dict(member_id=TARGET, old_channel_id=SOURCE, new_channel_id=SOURCE2,
                 timestamp="2025-12-01T10:00:00", type="MOVE")
        ]}})
        await self.collector.legacy()
        self.assertEqual(self.work.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    async def test_audits_link_by_target_id_and_recover_dated_nicknames(self):
        self.guild.audits = [
            NS(id=1, target=NS(id=TARGET), user=NS(id=REQUESTER), action=NS(name="member_update"),
               reason="test", before=[("nick", "Ancien")], after=[("nick", "Illunerah")], created_at=NOW-timedelta(days=1)),
            NS(id=2, target=NS(id=333), user=NS(id=REQUESTER), action=NS(name="ban"),
               reason="", before=[], after=[], created_at=NOW-timedelta(days=1)),
        ]
        await self.collector.audits()
        self.assertEqual(self.work.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertIn("Ancien", [a.value for a in self.meta.aliases])

    async def test_audit_permission_is_required_for_all_readers(self):
        self.guild.members[2].guild_permissions.view_audit_log = False
        await self.collector.audits()
        self.assertTrue(any("droit Voir les logs" in n for n in self.meta.notes))

    async def test_homonym_warning_includes_member_id(self):
        self.guild.members.append(member(333, "Illunerah"))
        self.scope.members = self.guild.members
        await self.collector.collect()
        self.assertTrue(any("333" in c for c in self.meta.collisions))

    async def test_to_record_refuses_wrong_server_and_channel(self):
        m = message(self.source)
        with self.assertRaises(EnqueteError):
            to_record(m, GID+1, SOURCE)
        with self.assertRaises(EnqueteError):
            to_record(m, GID, SOURCE2)

    async def test_cross_channel_resolved_author_not_imported(self):
        m = message(self.source)
        m.reference = NS(message_id=123, channel_id=SOURCE2, resolved=NS(author=NS(id=TARGET)))
        self.assertIsNone(to_record(m, GID, SOURCE).reply_author)

    async def test_retry_reads_only_transient_failures(self):
        retryable = OSError("offline")
        factory = AsyncMock(side_effect=[retryable, 42])
        with patch("utils.enquete_service.asyncio.sleep", new=AsyncMock()):
            self.assertEqual(await retry_read(factory), 42)
        denied = AsyncMock(side_effect=Denied())
        with self.assertRaises(Denied):
            await retry_read(denied)
        self.assertEqual(denied.await_count, 1)


if __name__ == "__main__":
    unittest.main()
