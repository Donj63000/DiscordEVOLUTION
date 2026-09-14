"""Contrôles de coût avec de vraies transactions SQLite, sans fournisseur IA."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests_evo.helpers import config, Transport, answer
from utils.evo_agent import MeteredModel, ProviderError
from utils.evo_budget import Budget, SQLiteBackend, initialize_sqlite, quote
from utils.evo_config import EvoConfig, EvoError


class ConfigurationTests(unittest.TestCase):
    def test_default_model_and_budget(self):
        self.assertEqual(config().model, "gpt-5.6-luna")
        self.assertEqual(config().monthly_nano, 2_000_000_000)

    def test_render_rejects_ephemeral_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "budget.sqlite3")
            initialize_sqlite(path)
            env = {"OPENAI_API_KEY": "placeholder", "EVO_GUILD_ID": "1", "EVO_CHANNEL_IDS": "10",
                   "RENDER": "true", "EVO_SQLITE_PATH": path}
            with patch.dict(os.environ, env, clear=True), self.assertRaises(EvoError):
                EvoConfig.from_env()

    def test_no_silent_model_replacement(self):
        with patch.dict(os.environ, {"EVO_MODEL": "expensive-model"}, clear=True), self.assertRaises(EvoError):
            EvoConfig.from_env()

    def test_invalid_numeric_settings(self):
        base = {"OPENAI_API_KEY": "placeholder", "EVO_GUILD_ID": "1", "EVO_CHANNEL_IDS": "10",
                "EVO_DATABASE_URL": "postgresql://unused/test"}
        for key, value in (("EVO_MONTHLY_USD", "NaN"), ("EVO_MONTHLY_USD", "-2"),
                           ("EVO_MAX_OUTPUT_TOKENS", "10000"),
                           ("EVO_HISTORY_CHANNEL_IDS", "20"), ("EVO_PUBLIC_MEMBER_DATA", "maybe")):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {**base, key: value}, clear=True):
                with self.assertRaises(EvoError):
                    EvoConfig.from_env()

    def test_config_repr_contains_no_secrets(self):
        c = config(api_key="MY_PRIVATE_KEY", database_url="postgresql://secret_value")
        self.assertNotIn("MY_PRIVATE_KEY", repr(c))
        self.assertNotIn("secret_value", repr(c))

    def test_quote_validation_and_rounding(self):
        self.assertEqual(quote(1000, 100), 425500)
        for value in (None, True, -1, 100001, float("inf")):
            with self.subTest(value=value), self.assertRaises(EvoError):
                quote(value, 0)


class SQLiteInitializationTests(unittest.TestCase):
    def test_initializer_closes_connection_before_returning(self):
        connections = []
        connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.sqlite3"
            try:
                with patch("utils.evo_budget.sqlite3.connect", side_effect=tracked_connect):
                    initialize_sqlite(str(path))
                self.assertEqual(len(connections), 1)
                with self.assertRaises(sqlite3.ProgrammingError):
                    connections[0].execute("SELECT 1")
                path.unlink()
            finally:
                for connection in connections:
                    connection.close()

    def test_initializer_removes_partial_file_after_schema_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.sqlite3"
            with patch("utils.evo_budget.SCHEMA", ("INVALID SQL",)):
                with self.assertRaises(sqlite3.OperationalError):
                    initialize_sqlite(str(path))
            self.assertFalse(path.exists())


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "ledger.sqlite3")
        initialize_sqlite(self.path)
        self.config = config(sqlite_path=self.path)
        self.now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        self.budget = Budget(self.config, clock=lambda: self.now)
        await self.budget.open()

    async def asyncTearDown(self):
        await self.budget.close()
        self.temp.cleanup()

    async def test_missing_file_is_not_recreated(self):
        bad = Budget(config(sqlite_path=str(Path(self.temp.name) / "absent.sqlite3")))
        with self.assertRaises(EvoError):
            await bad.open()
        self.assertFalse(Path(bad.config.sqlite_path).exists())

    async def test_initializer_never_overwrites(self):
        with self.assertRaises(FileExistsError):
            initialize_sqlite(self.path)

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
        other = Budget(self.config, clock=lambda: self.now)
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

    async def test_concurrent_instances_cannot_overreserve(self):
        settings = replace(self.config, monthly_nano=10000, daily_nano=10000, request_nano=6000)
        one = Budget(settings, clock=lambda: self.now)
        two = Budget(settings, clock=lambda: self.now)
        result = await asyncio.gather(
            one.reserve("one", "one", 6000), two.reserve("two", "two", 6000), return_exceptions=True)
        self.assertEqual(sum(isinstance(v, str) for v in result), 1)
        self.assertEqual((await self.budget.status())["used_nano"], 6000)

    async def test_daily_limit_and_user_call_limit(self):
        settings = replace(self.config, daily_nano=10000, request_nano=9000, user_daily_calls=1)
        limited = Budget(settings, clock=lambda: self.now)
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

    def payload(self):
        return {"model": self.config.model, "store": False, "service_tier": "default",
                "reasoning": {"effort": "none"}, "max_output_tokens": self.config.max_output,
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
        broken = Budget(config(sqlite_path=str(Path(self.temp.name) / "missing")))
        model = MeteredModel(self.config, broken, transport)
        with self.assertRaises(EvoError):
            await model.generate(self.payload(), "one", "user", self.config.request_nano)
        self.assertEqual(transport.count_calls, [])

    async def test_no_global_circuit_for_personal_budget_limit(self):
        settings = replace(self.config, user_daily_calls=1)
        model = MeteredModel(settings, Budget(settings, clock=lambda: self.now), Transport([answer("a"), answer("b")]))
        await model.generate(self.payload(), "one", "user", settings.request_nano)
        with self.assertRaises(EvoError):
            await model.generate(self.payload(), "two", "user", settings.request_nano)
        self.assertEqual(model.cool_until, 0)
        await model.generate(self.payload(), "three", "other", settings.request_nano)


if __name__ == "__main__":
    unittest.main()
