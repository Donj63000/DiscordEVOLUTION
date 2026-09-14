"""Lectures strictes et sauvegardes confirmées dans un faux salon Discord."""
import asyncio
import copy
from types import SimpleNamespace
import unittest

from tests_evo.budget_helpers import create_budget, FakeMessage, make_store
from tests_evo.helpers import config
from utils.evo_budget import Budget
from utils.evo_budget_store import ConsoleBudgetStore, FILENAME, MARKER
from utils.evo_config import EvoError


class ConsoleBudgetStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.budget = await create_budget()
        self.store = self.budget.store
        self.console = self.store.bot.console

    async def test_large_snapshot_uses_one_pinned_attachment_and_reloads(self):
        for index in range(5):
            await self.budget.reserve(str(index), "user", 10000)
        self.assertEqual(len(self.console.messages), 1)
        message = self.console.messages[0]
        self.assertTrue(message.pinned)
        self.assertEqual(message.attachments[0].filename, FILENAME)
        other = Budget(config(), ConsoleBudgetStore(self.store.bot, 1))
        await other.open()
        self.assertEqual((await other.status())["pending_nano"], 50000)

    async def test_json_code_fences_do_not_damage_restore(self):
        await self.budget.reserve("request```json\nsecret```", "user", 10000)
        other = Budget(config(), ConsoleBudgetStore(self.store.bot, 1))
        await other.open()
        self.assertEqual((await other.status())["pending_nano"], 10000)

    async def test_read_error_cannot_initialize_empty_budget(self):
        store = make_store()
        store.bot.console.read_error = RuntimeError("history denied")
        with self.assertRaises(EvoError):
            await Budget(config(), store).initialize()
        self.assertEqual(store.bot.console.sends, 0)

    async def test_unknown_guild_never_uses_another_guild_console(self):
        store = ConsoleBudgetStore(self.store.bot, 2)
        with self.assertRaises(EvoError):
            await store.load()

    async def test_duplicate_snapshot_is_rejected(self):
        original = self.console.messages[0]
        duplicate = FakeMessage(self.console, 500, original.content, original.attachments)
        self.console.messages.append(duplicate)
        with self.assertRaises(EvoError):
            await self.store.load()

    async def test_foreign_author_cannot_supply_budget_snapshot(self):
        store = make_store()
        forged = FakeMessage(store.bot.console, 500, MARKER + " damaged")
        forged.author = SimpleNamespace(id=1234)
        store.bot.console.messages.append(forged)
        self.assertIsNone(await store.load())

    async def test_damaged_latest_snapshot_does_not_become_empty_budget(self):
        self.console.messages[0].content = MARKER + " damaged"
        store = ConsoleBudgetStore(self.store.bot, 1)
        with self.assertRaises(EvoError):
            await store.load()

    async def test_modified_content_fails_checksum_validation(self):
        message = self.console.messages[0]
        message.content = message.content.replace('"revision": 1', '"revision": 2')
        with self.assertRaises(EvoError):
            await self.store.load()

    async def test_missing_snapshot_is_never_replaced_during_reservation(self):
        await self.console.messages[0].delete()
        with self.assertRaises(EvoError):
            await self.budget.reserve("one", "user", 10000)
        self.assertEqual(self.console.messages, [])
        self.assertEqual(self.console.sends, 1)

    async def test_edit_failure_preserves_old_snapshot(self):
        message = self.console.messages[0]
        content = message.content
        self.console.write_error = RuntimeError("write refused")
        with self.assertRaises(EvoError):
            await self.budget.reserve("one", "user", 10000)
        self.assertFalse(message.deleted)
        self.assertEqual(message.content, content)
        self.assertEqual(self.console.sends, 1)

    async def test_pin_failure_preserves_created_snapshot_for_recovery(self):
        store = make_store()
        store.bot.console.pin_error = RuntimeError("pin refused")
        budget = Budget(config(), store)
        with self.assertRaises(EvoError):
            await budget.initialize()
        self.assertEqual(len(store.bot.console.messages), 1)
        self.assertFalse(store.bot.console.messages[0].deleted)
        store.bot.console.pin_error = None
        await budget.open()
        await budget.reserve("one", "user", 10000)
        self.assertTrue(store.bot.console.messages[0].pinned)

    async def test_uncertain_initial_send_is_found_and_cannot_be_reinitialized(self):
        store = make_store()
        store.bot.console.commit_error = TimeoutError()
        budget = Budget(config(), store)
        with self.assertRaises(EvoError):
            await budget.initialize()
        store.bot.console.commit_error = None
        with self.assertRaises(EvoError):
            await budget.initialize()
        await budget.open()
        self.assertEqual((await budget.status())["calls"], 0)
        self.assertEqual(store.bot.console.sends, 1)

    async def test_unpinned_snapshot_outside_recent_history_is_found(self):
        original = self.console.messages[0]
        original.pinned = False
        for index in range(205):
            self.console.messages.append(FakeMessage(self.console, 1000 + index, "other data"))
        store = ConsoleBudgetStore(self.store.bot, 1)
        self.assertEqual((await store.load())["revision"], 1)

    async def test_size_limit_does_not_discard_reservations(self):
        await self.budget.reserve("one", "user", 10000)
        previous = await self.store.load()
        self.console.guild.filesize_limit = 100
        with self.assertRaises(EvoError):
            await self.budget.reserve("two", "user", 10000)
        self.assertEqual(await self.store.load(), previous)

    async def test_leadership_loss_during_confirmation_keeps_remote_record(self):
        async def lose_leadership():
            self.store.bot.leader = False

        self.console.after_write = lose_leadership
        with self.assertRaises(EvoError):
            await self.budget.reserve("one", "user", 10000)
        self.store.bot.leader = True
        self.console.after_write = None
        await self.budget.check_ready()
        self.assertEqual((await self.budget.status())["pending_nano"], 10000)
