"""Contrôles de coût avec snapshots Discord simulés, sans fournisseur IA."""
import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

from tests_evo.budget_helpers import create_budget, make_store
from tests_evo.helpers import config, Transport, answer
from utils.evo_agent import MeteredModel, ProviderError
from utils.evo_budget import Budget, quote, validate_snapshot
from utils.evo_config import EvoConfig, EvoError


class ConfigurationTests(unittest.TestCase):
    def test_default_model_and_budget(self):
        self.assertEqual(config().model, "gpt-5.6-luna")
        self.assertEqual(config().monthly_nano, 2_000_000_000)

    def test_render_needs_no_database(self):
        env = {"OPENAI_API_KEY": "placeholder", "EVO_GUILD_ID": "1", "EVO_CHANNEL_IDS": "10",
               "RENDER": "true", "DATABASE_URL": "ignored", "EVO_SQLITE_PATH": "ignored"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(EvoConfig.from_env().guild_id, 1)

    def test_no_silent_model_replacement(self):
        with patch.dict(os.environ, {"EVO_MODEL": "expensive-model"}, clear=True), self.assertRaises(EvoError):
            EvoConfig.from_env()

    def test_invalid_numeric_settings(self):
        base = {"OPENAI_API_KEY": "placeholder", "EVO_GUILD_ID": "1", "EVO_CHANNEL_IDS": "10",
                "RENDER": "true"}
        for key, value in (("EVO_MONTHLY_USD", "NaN"), ("EVO_MONTHLY_USD", "-2"),
                           ("EVO_MAX_OUTPUT_TOKENS", "10000"),
                           ("EVO_HISTORY_CHANNEL_IDS", "20"), ("EVO_PUBLIC_MEMBER_DATA", "maybe")):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {**base, key: value}, clear=True):
                with self.assertRaises(EvoError):
                    EvoConfig.from_env()

    def test_config_repr_contains_no_secrets(self):
        c = config(api_key="MY_PRIVATE_KEY")
        self.assertNotIn("MY_PRIVATE_KEY", repr(c))

    def test_quote_validation_and_rounding(self):
        self.assertEqual(quote(1000, 100), 425500)
        for value in (None, True, -1, 100001, float("inf")):
            with self.subTest(value=value), self.assertRaises(EvoError):
                quote(value, 0)


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = config()
        self.now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        self.budget = await create_budget(self.config, clock=lambda: self.now)
        self.console = self.budget.store.bot.console

    async def asyncTearDown(self):
        await self.budget.close()

    async def test_missing_console_snapshot_is_not_recreated(self):
        bad = Budget(self.config, make_store())
        with self.assertRaises(EvoError):
            await bad.open()
        self.assertEqual(bad.store.bot.console.messages, [])

    async def test_initializer_never_overwrites(self):
        with self.assertRaises(EvoError):
            await self.budget.initialize()
        other = Budget(self.config, self.budget.store)
        with self.assertRaises(EvoError):
            await other.initialize()
        self.assertEqual(self.console.sends, 1)

    async def test_reservation_settlement_idempotence(self):
        maximum = quote(1000, 600)
        token = await self.budget.reserve("request_one", "user", maximum)
        self.assertEqual((await self.budget.status())["used_nano"], maximum)
        await self.budget.settle(token, 800, 60)
        first = await self.budget.status()
        await self.budget.settle(token, 800, 60)
        self.assertEqual(first, await self.budget.status())
        self.assertEqual(first["used_nano"], quote(800, 60))
        self.assertEqual(first["pending_nano"], 0)

    async def test_duplicate_request_never_reserves_twice(self):
        await self.budget.reserve("same_request", "one", 5000)
        with self.assertRaises(EvoError):
            await self.budget.reserve("same_request", "two", 5000)
        self.assertEqual((await self.budget.status())["calls"], 1)

    async def test_reopen_preserves_pending(self):
        await self.budget.reserve("one", "user", 10000)
        other = Budget(self.config, self.budget.store, clock=lambda: self.now)
        await other.open()
        self.assertEqual((await other.status())["used_nano"], 10000)
        self.assertEqual((await other.status())["pending_nano"], 10000)
        await other.close()

    async def test_month_rollover_and_late_settlement(self):
        reservation = await self.budget.reserve("sept", "user", 500000)
        self.now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        await self.budget.settle(reservation, 100, 10)
        self.assertEqual((await self.budget.status())["used_nano"], 0)
        await self.budget.reserve("oct", "user", 10000)
        self.now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        self.assertEqual((await self.budget.status())["used_nano"], quote(100, 10))

    async def test_concurrent_requests_cannot_overreserve(self):
        settings = replace(self.config, monthly_nano=10000, daily_nano=10000, request_nano=6000)
        one = await create_budget(settings, clock=lambda: self.now)
        result = await asyncio.gather(
            one.reserve("one", "one", 6000), one.reserve("two", "two", 6000), return_exceptions=True)
        self.assertEqual(sum(isinstance(v, str) for v in result), 1)
        self.assertEqual((await one.status())["used_nano"], 6000)

    async def test_daily_limit_and_user_call_limit(self):
        settings = replace(self.config, daily_nano=10000, request_nano=9000, user_daily_calls=1)
        limited = Budget(settings, self.budget.store, clock=lambda: self.now)
        await limited.reserve("one", "user", 4000)
        with self.assertRaises(EvoError):
            await limited.reserve("two", "user", 1000)
        with self.assertRaises(EvoError):
            await limited.reserve("three", "other", 9000)
        self.now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        await limited.reserve("newday", "user", 4000)

    async def test_usage_anomaly_blocks_month(self):
        token = await self.budget.reserve("one", "user", quote(100, 10))
        with self.assertRaises(EvoError):
            await self.budget.settle(token, 200, 30)
        self.assertTrue((await self.budget.status())["blocked"])
        with self.assertRaises(EvoError):
            await self.budget.reserve("two", "user", 1000)

    async def test_invalid_usage_keeps_full_reservation(self):
        token = await self.budget.reserve("one", "user", 10000)
        with self.assertRaises(EvoError):
            await self.budget.settle(token, None, 100)
        self.assertEqual((await self.budget.status())["pending_nano"], 10000)

    async def test_stale_revision_cannot_overwrite_new_reservations(self):
        other = Budget(self.config, self.budget.store, clock=lambda: self.now)
        await other.open()
        await self.budget.reserve("first", "user", 10000)
        with self.assertRaises(EvoError):
            await other.reserve("second", "other", 10000)
        await other.check_ready()
        self.assertEqual((await other.status())["used_nano"], 10000)
        await other.reserve("second", "other", 10000)
        self.assertEqual((await other.status())["used_nano"], 20000)

    async def test_timeout_after_remote_commit_is_reloaded_without_refund(self):
        self.console.commit_error = TimeoutError()
        with self.assertRaises(EvoError):
            await self.budget.reserve("uncertain", "user", 10000)
        self.assertTrue(self.budget._uncertain)
        self.console.commit_error = None
        await self.budget.check_ready()
        self.assertEqual((await self.budget.status())["pending_nano"], 10000)
        with self.assertRaises(EvoError):
            await self.budget.reserve("uncertain", "user", 10000)

    async def test_cancellation_after_remote_commit_preserves_reservation(self):
        self.console.commit_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.budget.reserve("cancelled", "user", 10000)
        self.assertTrue(self.budget._uncertain)
        self.console.commit_error = None
        other = Budget(self.config, self.budget.store, clock=lambda: self.now)
        await other.open()
        self.assertEqual((await other.status())["pending_nano"], 10000)

    async def test_failed_settlement_keeps_maximum_until_reload_and_retry(self):
        reservation = await self.budget.reserve("one", "user", 500000)
        self.console.write_error = RuntimeError("unavailable")
        with self.assertRaises(EvoError):
            await self.budget.settle(reservation, 100, 10)
        self.console.write_error = None
        self.assertEqual((await self.budget.status())["pending_nano"], 500000)
        await self.budget.settle(reservation, 100, 10)
        self.assertEqual((await self.budget.status())["used_nano"], quote(100, 10))

    async def test_invalidation_during_write_requires_restore(self):
        async def invalidate_after_write():
            self.budget.invalidate()

        self.console.after_write = invalidate_after_write
        with self.assertRaises(EvoError):
            await self.budget.reserve("one", "user", 10000)
        self.assertIsNone(self.budget._state)
        self.console.after_write = None
        self.assertEqual((await self.budget.status())["pending_nano"], 10000)

    async def test_leadership_check_can_invalidate_without_deadlocking(self):
        async def lose_leadership():
            self.budget.invalidate()
            raise EvoError("leader lost")

        with patch.object(self.budget.store, "check_ready", side_effect=lose_leadership):
            with self.assertRaises(EvoError):
                await asyncio.wait_for(self.budget.reserve("one", "user", 10000), 1)

    async def test_changed_or_missing_reservation_cannot_be_restored(self):
        await self.budget.reserve("one", "user", 10000)
        payload = await self.budget.store.load()
        removed = copy.deepcopy(payload)
        removed["revision"] += 1
        removed["reservations"] = {}
        removed["buckets"] = {}
        await self.budget.store.save(removed, expected_revision=payload["revision"])
        self.budget.invalidate()
        with self.assertRaises(EvoError):
            await self.budget.check_ready()

    async def test_corrupt_state_is_rejected_by_recomputed_totals(self):
        reservation = await self.budget.reserve("one", "user", 10000)
        payload = await self.budget.store.load()
        corruptions = []
        bad = copy.deepcopy(payload)
        bad["buckets"]["month:2026-09"]["used"] = 0
        corruptions.append(bad)
        bad = copy.deepcopy(payload)
        bad["buckets"]["month:2026-09"]["calls"] = True
        corruptions.append(bad)
        bad = copy.deepcopy(payload)
        bad["reservations"][reservation]["day_bucket"] = "day:2026-09-14"
        corruptions.append(bad)
        bad = copy.deepcopy(payload)
        bad["reservations"]["f" * 32] = copy.deepcopy(bad["reservations"][reservation])
        corruptions.append(bad)
        bad = copy.deepcopy(payload)
        bad["reservations"][reservation]["input_tokens"] = 10
        corruptions.append(bad)
        bad = copy.deepcopy(payload)
        bad["guild_id"] = "2"
        corruptions.append(bad)
        for bad in corruptions:
            with self.subTest(payload=bad), self.assertRaises(EvoError):
                validate_snapshot(bad, self.config.guild_id)

    async def test_month_block_without_reservation_survives_restart(self):
        await self.budget.block_current_month()
        other = Budget(self.config, self.budget.store, clock=lambda: self.now)
        await other.open()
        self.assertTrue((await other.status())["blocked"])
        with self.assertRaises(EvoError):
            await other.reserve("one", "user", 10000)

    async def test_known_overrun_survives_failed_write_and_budget_recreation(self):
        reservation = await self.budget.reserve("one", "user", 1000)
        self.console.write_error = RuntimeError("write unavailable")
        with self.assertRaises(EvoError):
            await self.budget.settle(reservation, 1000, 100)
        self.budget.invalidate()
        self.console.write_error = None
        store = type(self.budget.store)(self.budget.store.bot, self.config.guild_id)
        other = Budget(self.config, store, clock=lambda: self.now)
        await other.open()
        status = await other.status()
        self.assertTrue(status["blocked"])
        self.assertEqual(status["used_nano"], quote(1000, 100))
        with self.assertRaises(EvoError):
            await other.reserve("two", "user", 1000)

    async def test_known_month_block_is_retried_before_any_new_reservation(self):
        self.console.write_error = RuntimeError("write unavailable")
        with self.assertRaises(EvoError):
            await self.budget.block_current_month()
        with self.assertRaises(EvoError):
            await self.budget.reserve("one", "user", 1000)
        self.console.write_error = None
        self.assertTrue((await self.budget.status())["blocked"])
        with self.assertRaises(EvoError):
            await self.budget.reserve("one", "user", 1000)

    def payload(self):
        return {"model": self.config.model, "store": False, "service_tier": "default",
                "reasoning": {"effort": self.config.reasoning_effort},
                "max_output_tokens": self.config.analysis_tokens,
                "input": [{"role": "user", "content": "hello"}], "tools": []}

    async def test_provider_timeout_retains_reservation_and_no_retry(self):
        transport = Transport([ProviderError("indisponible")])
        model = MeteredModel(self.config, self.budget, transport)
        with self.assertRaises(ProviderError):
            await model.generate(self.payload(), "one", "user", self.config.request_nano)
        self.assertEqual(len(transport.calls), 1)
        self.assertGreater((await self.budget.status())["pending_nano"], 0)
        with self.assertRaises(EvoError):
            await model.generate(self.payload(), "two", "user", self.config.request_nano)
        self.assertEqual(len(transport.calls), 1)

    async def test_wrong_model_response_no_fallback(self):
        result = answer("no")
        result["model"] = "different-model"
        transport = Transport([result])
        model = MeteredModel(self.config, self.budget, transport)
        with self.assertRaises(EvoError):
            await model.generate(self.payload(), "one", "user", self.config.request_nano)
        self.assertEqual(len(transport.calls), 1)
        self.assertGreater((await self.budget.status())["pending_nano"], 0)

    async def test_hosted_tools_rejected_before_network(self):
        transport = Transport([])
        model = MeteredModel(self.config, self.budget, transport)
        payload = self.payload()
        payload["tools"] = [{"type": "web_search"}]
        with self.assertRaises(EvoError):
            await model.generate(payload, "one", "user", self.config.request_nano)
        self.assertEqual(transport.count_calls, [])

    async def test_no_network_if_ledger_unavailable(self):
        transport = Transport([])
        broken = Budget(self.config, make_store())
        model = MeteredModel(self.config, broken, transport)
        with self.assertRaises(EvoError):
            await model.generate(self.payload(), "one", "user", self.config.request_nano)
        self.assertEqual(transport.count_calls, [])

    async def test_reservation_is_confirmed_before_provider_generation(self):
        transport = Transport([answer("ok")])
        original_create = transport.create

        async def assert_reserved(payload):
            snapshot = await self.budget.store.load()
            self.assertEqual(snapshot["revision"], 2)
            self.assertEqual(len(snapshot["reservations"]), 1)
            self.assertIsNone(next(iter(snapshot["reservations"].values()))["charged"])
            return await original_create(payload)

        transport.create = assert_reserved
        model = MeteredModel(self.config, self.budget, transport)
        await model.generate(self.payload(), "one", "user", self.config.request_nano)

    async def test_unconfirmed_revision_prevents_provider_generation(self):
        self.console.ignore_edits = True
        transport = Transport([answer("ok")])
        model = MeteredModel(self.config, self.budget, transport)
        with self.assertRaises(EvoError):
            await model.generate(self.payload(), "one", "user", self.config.request_nano)
        self.assertEqual(transport.calls, [])

    async def test_uncertain_save_prevents_provider_generation(self):
        self.console.commit_error = TimeoutError()
        transport = Transport([answer("ok")])
        model = MeteredModel(self.config, self.budget, transport)
        with self.assertRaises(EvoError):
            await model.generate(self.payload(), "one", "user", self.config.request_nano)
        self.assertEqual(transport.calls, [])

    async def test_lost_leadership_blocks_even_token_counting(self):
        self.budget.store.bot.leader = False
        transport = Transport([answer("ok")])
        model = MeteredModel(self.config, self.budget, transport)
        with self.assertRaises(EvoError):
            await model.generate(self.payload(), "one", "user", self.config.request_nano)
        self.assertEqual(transport.calls, [])
        self.assertEqual(transport.count_calls, [])

    async def test_no_global_circuit_for_personal_budget_limit(self):
        settings = replace(self.config, user_daily_calls=1)
        model = MeteredModel(settings, Budget(settings, self.budget.store, clock=lambda: self.now),
                             Transport([answer("a"), answer("b")]))
        await model.generate(self.payload(), "one", "user", settings.request_nano)
        with self.assertRaises(EvoError):
            await model.generate(self.payload(), "two", "user", settings.request_nano)
        self.assertEqual(model.cool_until, 0)
        await model.generate(self.payload(), "three", "other", settings.request_nano)


if __name__ == "__main__":
    unittest.main()
