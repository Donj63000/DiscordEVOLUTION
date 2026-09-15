"""Libération des appels facultatifs jamais soumis, avec un vrai faux registre Discord."""
import asyncio
import copy
from datetime import datetime, timezone
import unittest

from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import config
from utils.evo_budget import Budget, quote, validate_snapshot
from utils.evo_config import EvoError


class UnusedReservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = config(user_daily_calls=2)
        self.now = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)
        self.budget = await create_budget(self.config, clock=lambda: self.now)
        self.console = self.budget.store.bot.console

    async def asyncTearDown(self):
        self.console.after_write = None
        await self.budget.close()

    async def reserve(self, name="unused", maximum=100000):
        return await self.budget.reserve(name, "member", maximum)

    async def assert_pending(self, amount, calls):
        status = await self.budget.status()
        self.assertEqual(status["used_nano"], amount)
        self.assertEqual(status["pending_nano"], amount)
        self.assertEqual(status["calls"], calls)
        validate_snapshot(await self.budget.store.load(), self.config.guild_id)

    async def remove_remote_record(self, identifier):
        payload = await self.budget.store.load()
        candidate = copy.deepcopy(payload)
        record = candidate["reservations"].pop(identifier)
        for name in (record["month_bucket"], record["day_bucket"], record["user_bucket"]):
            bucket = candidate["buckets"][name]
            bucket["used"] -= record["maximum"]
            bucket["calls"] -= 1
            if not bucket["used"] and not bucket["calls"] and not bucket["blocked"]:
                del candidate["buckets"][name]
        candidate["revision"] += 1
        await self.budget.store.save(candidate, expected_revision=payload["revision"])

    async def test_unused_release_returns_budget_and_call_quota(self):
        used = await self.reserve("used")
        unused = await self.reserve()
        await self.budget.mark_submitted(used)
        await self.budget.settle(used, 100, 10)
        with self.assertRaises(EvoError):
            await self.reserve("third")
        self.assertEqual(await self.budget.release_unsubmitted(unused), 100000)
        status = await self.budget.status()
        self.assertEqual(status["used_nano"], quote(100, 10))
        self.assertEqual(status["calls"], 1)
        await self.reserve("third")
        self.assertEqual((await self.budget.status())["calls"], 2)

    async def test_last_unused_release_leaves_valid_empty_registry(self):
        identifier = await self.reserve()
        await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(0, 0)
        self.assertEqual((await self.budget.store.load())["buckets"], {})
        self.assertEqual(self.console.sends, 1)

    async def test_zero_usage_settlement_is_still_a_call_not_a_release(self):
        identifier = await self.reserve()
        await self.budget.settle(identifier, 0, 0)
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        status = await self.budget.status()
        self.assertEqual((status["used_nano"], status["calls"]), (0, 1))
        self.assertIn(identifier, (await self.budget.store.load())["reservations"])

    async def test_submitted_reservation_cannot_be_released_or_submitted_twice(self):
        identifier = await self.reserve()
        await self.budget.mark_submitted(identifier)
        for operation in (self.budget.release_unsubmitted, self.budget.mark_submitted):
            with self.subTest(operation=operation.__name__), self.assertRaises(EvoError):
                await operation(identifier)
        await self.assert_pending(100000, 1)

    async def test_settled_reservation_cannot_start_another_generation(self):
        identifier = await self.reserve()
        await self.budget.settle(identifier, 100, 10)
        with self.assertRaises(EvoError):
            await self.budget.mark_submitted(identifier)

    async def test_missing_identifier_has_no_local_proof(self):
        for operation in (self.budget.release_unsubmitted, self.budget.mark_submitted):
            with self.subTest(operation=operation.__name__), self.assertRaises(EvoError):
                await operation("f" * 32)
        await self.assert_pending(0, 0)

    async def test_restart_cannot_recreate_proof_for_pending_reservation(self):
        identifier = await self.reserve()
        restored = Budget(self.config, self.budget.store, clock=lambda: self.now)
        await restored.open()
        for operation in (restored.release_unsubmitted, restored.mark_submitted):
            with self.subTest(operation=operation.__name__), self.assertRaises(EvoError):
                await operation(identifier)
        self.assertEqual((await restored.status())["pending_nano"], 100000)

    async def test_local_reload_preserves_known_absence_of_submission(self):
        identifier = await self.reserve()
        self.budget.invalidate()
        await self.budget.check_ready()
        await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(0, 0)

    async def test_uncertain_initial_reservation_never_gains_release_proof(self):
        self.console.commit_error = TimeoutError()
        with self.assertRaises(EvoError):
            await self.reserve()
        self.console.commit_error = None
        await self.budget.check_ready()
        identifier = next(iter((await self.budget.store.load())["reservations"]))
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(100000, 1)

    async def test_invalidation_during_initial_reserve_never_gains_release_proof(self):
        async def invalidate():
            self.budget.invalidate()

        self.console.after_write = invalidate
        with self.assertRaises(EvoError):
            await self.reserve()
        self.console.after_write = None
        await self.budget.check_ready()
        identifier = next(iter((await self.budget.store.load())["reservations"]))
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(100000, 1)

    async def test_failed_release_keeps_cache_and_reloads_before_retry(self):
        identifier = await self.reserve()
        previous = copy.deepcopy(self.budget._state)
        self.console.write_error = RuntimeError("offline")
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        self.assertTrue(self.budget._uncertain)
        self.assertEqual(self.budget._state, previous)
        self.console.write_error = None
        await self.assert_pending(100000, 1)
        await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(0, 0)

    async def test_timeout_after_release_commit_is_reconciled_without_double_refund(self):
        identifier = await self.reserve()
        previous = copy.deepcopy(self.budget._state)
        self.console.commit_error = TimeoutError()
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        self.assertTrue(self.budget._uncertain)
        self.assertEqual(self.budget._state, previous)
        self.console.commit_error = None
        await self.assert_pending(0, 0)
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(0, 0)

    async def test_cancelled_release_commit_is_reconciled_on_live_reload(self):
        identifier = await self.reserve()
        self.console.commit_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.budget.release_unsubmitted(identifier)
        self.assertTrue(self.budget._uncertain)
        self.console.commit_error = None
        await self.assert_pending(0, 0)

    async def test_invalidation_after_release_commit_is_reconciled(self):
        identifier = await self.reserve()

        async def invalidate():
            self.budget.invalidate()

        self.console.after_write = invalidate
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        self.assertIsNone(self.budget._state)
        self.console.after_write = None
        await self.assert_pending(0, 0)

    async def test_release_uncertainty_does_not_allow_other_record_to_disappear(self):
        unused = await self.reserve()
        submitted = await self.reserve("submitted")
        await self.budget.mark_submitted(submitted)
        self.console.commit_error = TimeoutError()
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(unused)
        self.console.commit_error = None
        await self.remove_remote_record(submitted)
        with self.assertRaises(EvoError):
            await self.budget.check_ready()

    async def test_submission_clears_a_failed_release_attempt(self):
        identifier = await self.reserve()
        self.console.write_error = RuntimeError("offline")
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        self.console.write_error = None
        await self.budget.mark_submitted(identifier)
        await self.remove_remote_record(identifier)
        self.budget.invalidate()
        with self.assertRaises(EvoError):
            await self.budget.check_ready()

    async def test_concurrent_release_and_submission_cannot_both_succeed(self):
        identifier = await self.reserve()
        write_started, finish_write = asyncio.Event(), asyncio.Event()

        async def wait_after_write():
            write_started.set()
            await finish_write.wait()

        self.console.after_write = wait_after_write
        release = asyncio.create_task(self.budget.release_unsubmitted(identifier))
        await asyncio.wait_for(write_started.wait(), 1)
        submission = asyncio.create_task(self.budget.mark_submitted(identifier))
        await asyncio.sleep(0)
        self.assertFalse(submission.done())
        finish_write.set()
        self.assertEqual(await asyncio.wait_for(release, 1), 100000)
        with self.assertRaises(EvoError):
            await asyncio.wait_for(submission, 1)
        self.console.after_write = None
        await self.assert_pending(0, 0)

    async def test_concurrent_releases_refund_exactly_once(self):
        identifier = await self.reserve()
        results = await asyncio.gather(
            self.budget.release_unsubmitted(identifier),
            self.budget.release_unsubmitted(identifier), return_exceptions=True,
        )
        self.assertEqual(results.count(100000), 1)
        self.assertEqual(sum(isinstance(result, EvoError) for result in results), 1)
        await self.assert_pending(0, 0)

    async def test_month_rollover_refunds_only_original_buckets(self):
        identifier = await self.reserve("september")
        self.now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        await self.reserve("october", 200000)
        await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(200000, 1)
        self.now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        await self.assert_pending(0, 0)

    async def test_release_preserves_month_safety_block(self):
        identifier = await self.reserve()
        await self.budget.block_current_month()
        await self.budget.release_unsubmitted(identifier)
        status = await self.budget.status()
        self.assertTrue(status["blocked"])
        self.assertEqual((status["used_nano"], status["calls"]), (0, 0))
        validate_snapshot(await self.budget.store.load(), self.config.guild_id)

    async def test_invalid_usage_also_removes_absence_of_submission_proof(self):
        identifier = await self.reserve()
        with self.assertRaises(EvoError):
            await self.budget.settle(identifier, None, 1)
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(100000, 1)

    async def test_failed_settlement_does_not_restore_release_proof(self):
        identifier = await self.reserve()
        self.console.write_error = RuntimeError("offline")
        with self.assertRaises(EvoError):
            await self.budget.settle(identifier, 100, 10)
        self.console.write_error = None
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(100000, 1)

    async def test_leadership_loss_blocks_release_without_publishing_change(self):
        identifier = await self.reserve()
        previous = copy.deepcopy(self.budget._state)
        self.budget.store.bot.leader = False
        with self.assertRaises(EvoError):
            await self.budget.release_unsubmitted(identifier)
        self.assertEqual(self.budget._state, previous)
        self.budget.store.bot.leader = True
        await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(0, 0)

    async def test_leadership_loss_blocks_submission(self):
        identifier = await self.reserve()
        self.budget.store.bot.leader = False
        with self.assertRaises(EvoError):
            await self.budget.mark_submitted(identifier)
        self.budget.store.bot.leader = True
        await self.budget.release_unsubmitted(identifier)
        await self.assert_pending(0, 0)
