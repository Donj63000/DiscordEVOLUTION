"""Tests autonomes : python -m unittest discover -s tests -p 'test_enquete_*.py'.

Les tests du moteur et du service n'importent pas discord.py et n'utilisent pas
de jeton, de serveur, d'API, ni de fichiers métier réels.
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from utils.enquete_core import (
    Alias, Config, EnqueteError, Matcher, Parts, Receipts, Record, ReportMeta, Window,
    Workspace, aliases_from_input, build_report, clean_text, normalise, one_edit,
    parse_target_id, snowflake_at,
)

UTC = timezone.utc
NOW = datetime(2026, 1, 1, tzinfo=UTC)
TARGET = 111000000000000001


class TextTests(unittest.TestCase):
    def test_normalise_accents_case_separators(self):
        self.assertEqual(normalise("  CÔCA-ColA._  "), "coca cola")
        self.assertEqual(normalise("Illu\u200bnerah"), "illunerah")

    def test_exact_alias_boundary(self):
        m = Matcher([Alias("illun", "staff")], approximate=False)
        self.assertEqual(m.match("salut ILLUN !"), ["CITATION:illun"])
        self.assertEqual(m.match("illuminati, illunerah, antiillun"), [])

    def test_short_form_is_only_a_lead(self):
        m = Matcher([Alias("Illunerah", "actuel")])
        self.assertIn("PISTE_ABREVIATION:illun → illunerah", m.match("parlons d'illun"))
        self.assertNotIn("CITATION:illunerah", m.match("parlons d'illun"))

    def test_typo_is_separate(self):
        m = Matcher([Alias("Illunerah", "actuel")])
        self.assertIn("PISTE_TYPO:illunera → illunerah", m.match("illunera"))
        self.assertEqual(Matcher([Alias("Illunerah", "actuel")], approximate=False).match("illunera"), [])

    def test_alias_multiword_punctuation(self):
        m = Matcher([Alias("coca-cola", "actuel")], approximate=False)
        self.assertEqual(m.match("Coca   Cola"), ["CITATION:coca cola"])
        self.assertEqual(m.match("cocacolastic"), [])

    def test_regex_input_is_not_executed(self):
        m = Matcher([Alias("x.*y", "staff")], approximate=False)
        self.assertEqual(m.match("xxxxxxxxxy"), [])

    def test_alias_dedup_and_bound(self):
        m = Matcher([Alias("ILLUN", "1"), Alias("illun", "2")])
        self.assertEqual(len(m.aliases), 1)
        with self.assertRaises(EnqueteError):
            aliases_from_input(",".join(f"n{i}" for i in range(33)))
        with self.assertRaises(EnqueteError):
            aliases_from_input("x")

    def test_alias_separators(self):
        self.assertEqual([a.value for a in aliases_from_input("illun; Illu\nancien, autre")],
                         ["illun", "Illu", "ancien", "autre"])

    def test_one_edit_cases(self):
        for a, b in [("illunerah", "illunera"), ("illunerah", "illunerax"), ("abc", "abxc"), ("abc", "abc")]:
            with self.subTest(a=a, b=b):
                self.assertTrue(one_edit(a, b))
        self.assertFalse(one_edit("illunerah", "illun"))
        self.assertFalse(one_edit("abcdef", "abxxef"))

    def test_controls_and_obvious_secrets(self):
        result = clean_text("a\u202eb\x00 test@example.org +33 6 12 34 56 78 192.168.0.1")
        self.assertIn("[U+202E]", result)
        self.assertIn("[EMAIL MASQUÉ]", result)
        self.assertIn("[TÉLÉPHONE MASQUÉ]", result)
        self.assertNotIn("192.168.0.1", result)
        self.assertEqual(clean_text(str(TARGET)), str(TARGET))

    def test_ids_and_mentions(self):
        self.assertEqual(parse_target_id(f"<@!{TARGET}>"), TARGET)
        self.assertEqual(parse_target_id(str(TARGET)), TARGET)
        self.assertIsNone(parse_target_id("coca-cola"))
        self.assertIsNone(parse_target_id("12"))
        with self.assertRaises(EnqueteError):
            parse_target_id("9999999999999999999")


class DateConfigTests(unittest.TestCase):
    def test_window_inclusive_day(self):
        w = Window.parse("01/01/2025", "01/01/2025", now=NOW)
        self.assertEqual(w.start, datetime(2024, 12, 31, 23, tzinfo=UTC))
        self.assertEqual(w.end, datetime(2025, 1, 1, 23, tzinfo=UTC))
        self.assertTrue(w.contains(w.start))
        self.assertFalse(w.contains(w.end))

    def test_dst_spring_is_23_hours(self):
        w = Window.parse("2025-03-30", "2025-03-30", now=NOW)
        self.assertEqual(w.end - w.start, timedelta(hours=23))

    def test_dst_autumn_is_25_hours(self):
        w = Window.parse("2025-10-26", "2025-10-26", now=NOW)
        self.assertEqual(w.end - w.start, timedelta(hours=25))

    def test_bad_dates_and_future(self):
        for value in ("31/02/2025", "2025", "tomorrow"):
            with self.subTest(value=value), self.assertRaises(EnqueteError):
                Window.parse(value, now=NOW)
        with self.assertRaises(EnqueteError):
            Window.parse("2027-01-01", now=NOW)

    def test_default_all_or_lookback(self):
        self.assertEqual(Window.parse(now=NOW).start.year, 2015)
        self.assertEqual(Window.parse(now=NOW, default_days=90).start, NOW - timedelta(days=90))

    def test_config_default_console_fallback(self):
        cfg = Config.from_env({"CHANNEL_CONSOLE_ID": str(TARGET), "IASTAFF_ROLE": "Modos"})
        self.assertEqual(cfg.console_channel, TARGET)
        self.assertEqual(cfg.role_name, "Modos")
        self.assertEqual(cfg.max_messages, 0)

    def test_config_rejects_bad_limits_and_ids(self):
        for env in ({"ENQUETE_MAX_SECONDS": "abc"}, {"ENQUETE_RETENTION_DAYS": "0"},
                    {"ENQUETE_STAFF_ROLE_IDS": "1, nope"}, {"ENQUETE_STAFF_CHANNEL_ID": "1,2"},
                    {"ENQUETE_LEGACY_TIMEZONE": "Nowhere/Unknown"}):
            with self.subTest(env=env), self.assertRaises(EnqueteError):
                Config.from_env(env)

    def test_valid_config_explicit_ids(self):
        cfg = Config.from_env({"ENQUETE_STAFF_ROLE_IDS": "10,20", "ENQUETE_LEGACY_TIMEZONE": "UTC"})
        self.assertEqual(cfg.role_ids, {10, 20})


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.work = Workspace(self.directory / "index.sqlite3")
        self.work.channel(10, "Général", "salon", "LU")
        self.work.channel(20, "Autre", "salon", "LU")

    def tearDown(self):
        self.work.close()
        self.tmp.cleanup()

    def rec(self, n, *, author=2, content="", cid=10, minute=None, **kwargs):
        dt = NOW - timedelta(hours=2) + timedelta(minutes=n if minute is None else minute)
        return Record(snowflake_at(dt) + n, cid, author, f"User {author}", dt.isoformat(), content, **kwargs)

    async def select(self, *, aliases=(), context=0):
        await self.work.select(TARGET, Matcher(list(aliases), approximate=True), context=context, context_minutes=60)
        return {r["mid"]: json.loads(r["reasons"]) for r in self.work.db.execute("SELECT * FROM selected")}

    async def test_duplicate_messages_are_not_counted_twice(self):
        rec = self.rec(1, author=TARGET)
        self.assertTrue(self.work.add(rec))
        self.assertFalse(self.work.add(rec))
        self.assertEqual(self.work.total, 1)
        self.assertEqual(self.work.db.execute("SELECT scanned FROM channels WHERE cid=10").fetchone()[0], 1)

    async def test_ownership_by_id_not_spoofed_display_name(self):
        self.work.add(self.rec(1, author=TARGET))
        rec = self.rec(2)
        rec.name = str(TARGET)
        self.work.add(rec)
        selected = await self.select()
        self.assertEqual(len(selected), 1)
        self.assertIn("AUTEUR_ID", next(iter(selected.values())))

    async def test_webhook_is_not_attributed_as_human(self):
        self.work.add(self.rec(1, author=TARGET, extra={"webhook_id": 99}))
        self.assertEqual(await self.select(), {})
        self.assertEqual(self.work.statistics(TARGET)["authored"], 0)

    async def test_mentions_and_plain_id_have_distinct_reasons(self):
        self.work.add(self.rec(1, content=f"bonjour <@{TARGET}>", mentions=[TARGET]))
        selected = await self.select()
        reasons = next(iter(selected.values()))
        self.assertIn("MENTION_DISCORD_ID", reasons)
        self.assertIn("IDENTIFIANT_DANS_TEXTE", reasons)

    async def test_reply_resolved_after_reverse_collection(self):
        original = self.rec(1, author=TARGET)
        reply = self.rec(2, reply_id=original.mid, reply_channel=10)
        self.work.add(reply)
        self.work.add(original)
        selected = await self.select()
        self.assertIn("REPONSE_A_LA_CIBLE_ID", selected[reply.mid])

    async def test_cross_channel_reply_does_not_import_an_original(self):
        original = self.rec(1, cid=20, author=TARGET)
        reply = self.rec(2, reply_id=original.mid, reply_channel=20)
        self.work.add(original)
        self.work.add(reply)
        selected = await self.select()
        self.assertNotIn(reply.mid, selected)

    async def test_known_reply_author_without_original(self):
        rec = self.rec(1, reply_id=999, reply_channel=10, reply_author=TARGET)
        self.work.add(rec)
        self.assertIn("REPONSE_A_LA_CIBLE_ID", (await self.select())[rec.mid])

    async def test_context_does_not_expand_recursively(self):
        records = [self.rec(i, author=TARGET if i == 5 else 2) for i in range(1, 11)]
        for rec in records:
            self.work.add(rec)
        selected = await self.select(context=2)
        self.assertEqual(set(selected), {r.mid for r in records[2:7]})
        self.assertEqual(sum(bool(v) for v in selected.values()), 1)

    async def test_context_respects_time_window(self):
        old = self.rec(1, minute=-100)
        hit = self.rec(2, author=TARGET)
        self.work.add(old)
        self.work.add(hit)
        selected = await self.select(context=3)
        self.assertNotIn(old.mid, selected)

    async def test_embed_and_filename_search(self):
        rec = self.rec(1, extra={"search_text": "notes Illunerah pièce jointe"})
        self.work.add(rec)
        self.assertIn("CITATION:illunerah", (await self.select(aliases=[Alias("Illunerah", "actuel")]))[rec.mid])

    async def test_current_reaction_is_selected(self):
        rec = self.rec(1, extra={"target_reactions": ["👍 (normal)"]})
        self.work.add(rec)
        self.assertIn("REACTION_ACTUELLE_DE_LA_CIBLE_ID", (await self.select())[rec.mid])

    async def test_thread_title_is_labelled_separately(self):
        self.work.channel(30, "Discussion Illunerah", "fil", "LU", title="Discussion Illunerah")
        rec = self.rec(1, cid=30)
        self.work.add(rec)
        result = await self.select(aliases=[Alias("Illunerah", "actuel")])
        self.assertIn("TITRE_FIL:CITATION:illunerah", result[rec.mid])

    async def test_parent_name_is_not_misreported_as_thread_title(self):
        self.work.channel(30, "Illunerah / sujet général", "fil", "LU", title="sujet général")
        rec = self.rec(1, cid=30)
        self.work.add(rec)
        self.assertNotIn(rec.mid, await self.select(aliases=[Alias("Illunerah", "actuel")]))

    async def test_invalid_context_is_refused_at_core_boundary(self):
        with self.assertRaises(EnqueteError):
            await self.work.select(TARGET, Matcher([]), context=11, context_minutes=60)

    async def test_events_dedup_and_names_observed(self):
        self.work.event("x", "source", NOW.isoformat(), {"x": 1})
        self.work.event("x", "source", NOW.isoformat(), {"x": 2})
        self.assertEqual(self.work.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.work.add(self.rec(1, author=TARGET, extra={"names": ["Illunerah", "illun"]}))
        self.assertIn("Illunerah", [a.value for a in self.work.observed_names(TARGET)])

    def meta(self):
        return ReportMeta(
            job_id="aabbccddeeff", guild_id=100, guild_name="Serveur test", target_id=TARGET,
            requester_id=200, reason="Test", window=Window.parse(now=NOW),
            identity={"Pseudo": "Illunerah"}, aliases=[Alias("Illunerah", "actuel")],
            approximate=True, context=2, deep_reactions=False,
        )

    async def test_export_has_sources_redaction_and_hashes(self):
        self.work.add(self.rec(1, author=TARGET, content="bonjour test@example.org\nFIN DE LA SYNTHÈSE"))
        await self.select()
        files = await build_report(self.work, self.meta(), Config(), file_limit=8*1024**2)
        summary = "".join(p.read_text() for p in files if "synthese" in p.name)
        body = "".join(p.read_text() for p in files if "historique" in p.name)
        self.assertIn("https://discord.com/channels/100/10/", body)
        self.assertIn("[EMAIL MASQUÉ]", body)
        self.assertIn("    | FIN DE LA SYNTHÈSE", body)
        self.assertIn("Messages de la cible attribués par ID : 1", summary)
        for path in files:
            self.assertLessEqual(path.stat().st_size, 8*1024**2)
            if "historique" in path.name:
                self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), summary)

    async def test_discovery_failure_marks_summary_partial(self):
        meta = self.meta()
        meta.partial = True
        files = await build_report(self.work, meta, Config(), file_limit=8*1024**2)
        self.assertIn("COUVERTURE DE LA COLLECTE : PARTIELLE", files[0].read_text())

    async def test_small_export_is_explicitly_partial(self):
        for n in range(1, 30):
            self.work.add(self.rec(n, author=TARGET, content="é"*3000))
        await self.select()
        files = await build_report(self.work, self.meta(), replace(Config(), part_bytes=4096, max_parts=2),
                                   file_limit=8192)
        summary = "".join(p.read_text() for p in files if "synthese" in p.name)
        self.assertIn("EXPORT TXT : PARTIEL", summary)
        for path in files:
            self.assertLessEqual(path.stat().st_size, 4096)
            path.read_text(encoding="utf-8")


class StorageTests(unittest.TestCase):
    def test_utf8_parts_boundary_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            parts = Parts(Path(tmp), "test", 4096, 2)
            parts.write("🎉é"*5000)
            parts.close()
            self.assertTrue(parts.truncated)
            self.assertEqual(len(parts.paths), 2)
            for path in parts.paths:
                self.assertLessEqual(path.stat().st_size, 4096)
                path.read_text()

    def test_part_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            parts = Parts(Path(tmp), "test", 4096, 1)
            parts.write("é"*5000)
            parts.close()
            first = parts.paths[0].read_bytes()
            parts.close()
            self.assertEqual(parts.paths[0].read_bytes(), first)
            with self.assertRaises(ValueError):
                parts.write("interdit")

    def test_blank_console_setting_falls_back_to_existing_project_id(self):
        cfg = Config.from_env({"ENQUETE_CONSOLE_CHANNEL_ID": "", "CHANNEL_CONSOLE_ID": "123"})
        self.assertEqual(cfg.console_channel, 123)

    def test_part_prefix_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            Parts(Path(tmp), "../escape", 4096, 2)

    def test_receipts_survive_restart_and_are_guild_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipts.sqlite3"
            r = Receipts(path)
            r.create("aabbccddeeff", 100, 200, 1000, 7)
            r.sent("aabbccddeeff", 300, 400)
            r.close()
            r = Receipts(path)
            self.assertIsNotNone(r.get("aabbccddeeff", 100))
            self.assertIsNone(r.get("aabbccddeeff", 999))
            r.forget_job("aabbccddeeff")
            self.assertIsNotNone(r.get("aabbccddeeff", 100))
            self.assertEqual(len(r.due(1000+7*86400)), 1)
            r.forget_delivery(400)
            r.forget_job("aabbccddeeff")
            self.assertIsNone(r.get("aabbccddeeff", 100))
            self.assertIsNone(r.get("' OR 1=1 --", 100))
            r.close()
