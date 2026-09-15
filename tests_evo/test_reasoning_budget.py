"""Je couvre le raisonnement borné, sa facturation et sa continuité éphémère."""
from copy import deepcopy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import Guild, Transport, answer, config, context, function, response
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_budget import quote
from utils.evo_config import EvoConfig, EvoError
from utils.evo_tools import schemas_for


class CapturingTransport(Transport):
    """Je capture les entrées au moment de l'envoi, avant les ajouts au contexte."""

    async def count(self, payload):
        self.count_calls.append(deepcopy(payload))
        return self.input_count

    async def create(self, payload):
        self.calls.append(deepcopy(payload))
        value = self.replies.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def reasoning(identifier="one"):
    return {
        "type": "reasoning", "id": "rs_" + identifier,
        "encrypted_content": "SECRET_CHIFFRE_" + identifier,
        "summary": [{"type": "summary_text", "text": "RESUME_INTERNE_" + identifier}],
    }


def incomplete(outputs, *, tokens=3000):
    result = response(outputs, usage={
        "input_tokens": 800, "output_tokens": tokens,
        "output_tokens_details": {"reasoning_tokens": tokens},
    })
    result.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    return result


class ReasoningConfigTests(unittest.TestCase):
    def settings(self, **env):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-placeholder", **env}, clear=True):
            return EvoConfig.from_env(guilds=[Guild()])

    def test_default_medium_preserves_money_and_call_limits(self):
        settings = self.settings()
        self.assertEqual(settings.reasoning_effort, "medium")
        self.assertEqual([settings.output_limit(role) for role in ("analysis", "writer", "specialist")],
                         [3000, 3000, 1800])
        self.assertEqual((settings.max_output, settings.specialist_output), (400, 250))
        self.assertEqual((settings.monthly_nano, settings.daily_nano, settings.request_nano),
                         (2_000_000_000, 120_000_000, 15_000_000))
        self.assertEqual((settings.max_calls, settings.deep_max_calls), (2, 3))

    def test_none_keeps_the_previous_small_output_envelopes(self):
        settings = self.settings(EVO_REASONING_EFFORT="none")
        self.assertEqual([settings.output_limit(role) for role in ("analysis", "writer", "specialist")],
                         [400, 400, 250])

    def test_low_is_available_without_relaxing_caps(self):
        settings = self.settings(EVO_REASONING_EFFORT="low")
        self.assertEqual(settings.reasoning_effort, "low")
        self.assertEqual(settings.output_limit("writer"), 3000)

    def test_invalid_effort_and_out_of_range_caps_are_rejected(self):
        for name, value in (
            ("EVO_REASONING_EFFORT", "high"), ("EVO_REASONING_EFFORT", ""),
            ("EVO_ANALYSIS_MAX_OUTPUT_TOKENS", "4001"),
            ("EVO_WRITER_MAX_OUTPUT_TOKENS", "400"),
            ("EVO_SPECIALIST_MAX_OUTPUT_TOKENS", "3001"),
            ("EVO_SPECIALIST_MAX_OUTPUT_TOKENS", "499"),
        ):
            with self.subTest(name=name, value=value), self.assertRaises(EvoError):
                self.settings(**{name: value})


class ReasoningBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = config()
        self.budget = await create_budget(self.config)

    async def asyncTearDown(self):
        await self.budget.close()

    def agent(self, replies, *, handler=None, count=800):
        self.transport = CapturingTransport(replies, count=count)
        self.model = MeteredModel(self.config, self.budget, self.transport)
        self.tools = None if handler is None else SimpleNamespace(execute=AsyncMock(side_effect=handler))
        return EvoAgent(self.config, self.model, self.tools)

    def payload(self, agent, *, specialist=False, tools=()):
        return agent.payload(context(self.config), [{"role": "user", "content": "Question"}],
                             specialist=specialist, tools=tools)

    async def test_entire_output_usage_is_charged_once_including_hidden_reasoning(self):
        result = answer("Réponse courte.")
        result["usage"] = {"input_tokens": 800, "output_tokens": 2900,
                           "output_tokens_details": {"reasoning_tokens": 2700}}
        agent = self.agent([result])
        await agent.answer(context(self.config), "Salut", 1)
        status = await self.budget.status()
        self.assertEqual(status["used_nano"], quote(800, 2900))
        self.assertEqual(status["output_tokens"], 2900)
        self.assertEqual(status["pending_nano"], 0)

    async def test_invalid_hidden_usage_keeps_reservation_and_requires_console_reload(self):
        invalid_details = ["invalid", {}, {"reasoning_tokens": True}, {"reasoning_tokens": -1},
                           {"reasoning_tokens": "40"}, {"reasoning_tokens": 61}]
        for index, details in enumerate(invalid_details):
            with self.subTest(details=details):
                result = answer("Réponse non fiable.")
                result["usage"]["output_tokens_details"] = details
                agent = self.agent([result])
                with self.assertRaisesRegex(EvoError, "raisonnement invalide"):
                    await self.model.generate(self.payload(agent), str(index), "member", self.config.request_nano)
                console = self.budget.store.bot.console
                console.read_error = PermissionError("Console inaccessible")
                try:
                    with self.assertRaises(EvoError):
                        await self.model.generate(self.payload(agent), "next", "member", self.config.request_nano)
                finally:
                    console.read_error = None
                self.assertEqual(len(self.transport.count_calls), 1)
                self.assertEqual(len(self.transport.calls), 1)
        self.assertGreater((await self.budget.status())["pending_nano"], 0)

    async def test_output_above_the_role_cap_is_charged_and_blocks_even_if_quote_fits(self):
        result = answer("Trop de sortie.")
        result["usage"]["output_tokens"] = 3001
        agent = self.agent([result], count=self.config.max_input)
        with self.assertRaisesRegex(EvoError, "limite de sortie"):
            await self.model.generate(self.payload(agent), "over-cap", "member", self.config.request_nano)
        status = await self.budget.status()
        self.assertEqual(status["used_nano"], quote(800, 3001))
        self.assertEqual(status["pending_nano"], 0)
        self.assertTrue(status["blocked"])

    async def test_role_limits_and_effort_are_validated_before_counting(self):
        self.config = config(analysis_tokens=3200, writer_tokens=2800, specialist_tokens=1600)
        self.budget.config = self.config
        agent = self.agent([])
        catalogue = schemas_for("guilde")
        for specialist, tools, cap in ((False, catalogue, 3200), (False, [], 2800), (True, [], 1600)):
            payload = self.payload(agent, specialist=specialist, tools=tools)
            self.assertEqual(payload["max_output_tokens"], cap)
            self.assertEqual(payload["reasoning"], {"effort": "medium"})
            for key, value in (("max_output_tokens", 400), ("reasoning", {"effort": "none"})):
                with self.subTest(specialist=specialist, cap=cap, key=key):
                    altered = {**payload, key: value}
                    with self.assertRaises(EvoError):
                        await self.model.generate(altered, "invalid", "member", self.config.request_nano,
                                                  specialist=specialist)
        self.assertFalse(self.transport.count_calls)
        self.assertFalse(self.transport.calls)

    async def test_encrypted_reasoning_is_replayed_in_order_only_during_current_request(self):
        outputs = [reasoning("one"), function("guilde", {}, "one"),
                   reasoning("two"), function("guilde", {}, "two")]
        agent = self.agent([response(outputs), answer("Evolution."), answer("Salut !")])
        ctx = context(self.config)
        result = await agent.answer(ctx, "Présente la guilde", 2)
        sent = self.transport.calls[1]["input"]
        replayed = [row for row in sent if row.get("type") in {"reasoning", "function_call"}]
        self.assertEqual(replayed, outputs)
        first_result = next(i for i, row in enumerate(sent) if row.get("type") == "function_call_output")
        self.assertEqual(sent[first_result - 4:first_result], outputs)
        self.assertNotIn("SECRET_CHIFFRE", json.dumps(self.transport.calls[0]))
        self.assertEqual(self.transport.count_calls[1]["input"], sent)
        self.assertIn("reasoning.encrypted_content", self.transport.calls[0]["include"])
        await agent.answer(ctx, "Salut", 3)
        memory = agent.sessions.get((ctx.guild.id, ctx.channel.id, ctx.member.id))
        for text in (result, repr(memory), json.dumps(self.transport.calls[-1])):
            self.assertNotIn("SECRET_CHIFFRE", text)
            self.assertNotIn("RESUME_INTERNE", text)

    async def test_incomplete_analysis_with_action_is_charged_without_mutation_or_retry(self):
        execute = AsyncMock(return_value={"action_effectuee": True})
        agent = self.agent([incomplete([reasoning(), function(
            "definir_mon_metier", {"metier": "Paysan", "niveau": 100},
        )])], handler=execute)
        with self.assertRaisesRegex(EvoError, "limite de raisonnement"):
            await agent.answer(context(self.config), "Ajoute Paysan niveau 100 à mon profil", 4)
        execute.assert_not_awaited()
        self.assertEqual(len(self.transport.calls), 1)
        self.assertFalse(agent.sessions.items)
        status = await self.budget.status()
        self.assertEqual(status["used_nano"], quote(800, 3000))
        self.assertEqual(status["pending_nano"], 0)

    async def test_incomplete_writer_is_not_published_or_retried(self):
        agent = self.agent([incomplete(answer("Texte tronqué.")["output"])])
        with self.assertRaisesRegex(EvoError, "limite de raisonnement"):
            await agent.answer(context(self.config), "Salut", 5)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertFalse(agent.sessions.items)
        self.assertEqual((await self.budget.status())["pending_nano"], 0)

    async def test_failed_or_cancelled_analysis_never_executes_complete_looking_calls(self):
        for index, status in enumerate(("failed", "cancelled", "in_progress", None)):
            with self.subTest(status=status):
                selected = response([function("definir_mon_metier", {"metier": "Paysan", "niveau": 100})])
                selected["status"] = status
                execute = AsyncMock(return_value={"action_effectuee": True})
                agent = self.agent([selected], handler=execute)
                with self.assertRaises(EvoError):
                    await agent.answer(context(self.config), "Ajoute Paysan 100 à mon profil", 20 + index)
                execute.assert_not_awaited()
                self.assertEqual(len(self.transport.calls), 1)

    async def test_unfinished_function_call_is_not_executed_even_in_completed_response(self):
        for index, status in enumerate(("in_progress", "incomplete")):
            with self.subTest(status=status):
                call = function("definir_mon_metier", {"metier": "Paysan", "niveau": 100})
                call["status"] = status
                execute = AsyncMock(return_value={"action_effectuee": True})
                agent = self.agent([response([call])], handler=execute)
                with self.assertRaisesRegex(EvoError, "aucune action"):
                    await agent.answer(context(self.config), "Ajoute Paysan 100 à mon profil", 30 + index)
                execute.assert_not_awaited()
                self.assertEqual(len(self.transport.calls), 1)

    async def test_specialist_without_text_does_not_prevent_the_reserved_writer(self):
        for index, outputs in enumerate(([], [reasoning()], [{
            "type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "Refus"}],
        }])):
            with self.subTest(outputs=outputs):
                agent = self.agent([
                    response([function("guilde", {})]), response(outputs), answer("Réponse finale."),
                ])
                result = await agent.answer(context(self.config), "Approfondis la guilde", 40 + index)
                self.assertEqual(result, "Réponse finale.")
                self.assertEqual(len(self.transport.calls), 3)
                self.assertEqual((await self.budget.status())["pending_nano"], 0)

    async def test_incomplete_specialist_is_charged_then_existing_writer_finishes(self):
        agent = self.agent([
            response([function("guilde", {})]),
            incomplete([reasoning(), *answer("AVIS_INCOMPLET")["output"]], tokens=1800),
            answer("Réponse vérifiée."),
        ])
        result = await agent.answer(context(self.config), "Approfondis la présentation de la guilde", 6)
        self.assertEqual(result, "Réponse vérifiée.")
        self.assertEqual(len(self.transport.calls), 3)
        self.assertNotIn("AVIS_INCOMPLET", json.dumps(self.transport.calls[-1]))
        status = await self.budget.status()
        self.assertEqual(status["used_nano"], 2 * quote(800, 60) + quote(800, 1800))
        self.assertEqual(status["pending_nano"], 0)

    async def test_three_medium_generations_fit_real_input_with_writer_reserved_first(self):
        agent = self.agent([
            response([function("guilde", {})]), answer("Note courte."), answer("Réponse finale."),
        ])
        reservations = []
        reserve = self.budget.reserve

        async def record(request_key, user_key, maximum):
            reservations.append((request_key, maximum))
            return await reserve(request_key, user_key, maximum)

        self.budget.reserve = record
        await agent.answer(context(self.config), "Approfondis la guilde", 7)
        self.assertEqual([key for key, _ in reservations], ["1:7:0", "1:7:writer", "1:7:1"])
        self.assertEqual([amount for _, amount in reservations], [
            quote(864, 3000), quote(7564, 3000), quote(864, 1800),
        ])
        self.assertLessEqual(sum(amount for _, amount in reservations), self.config.request_nano)
        self.assertEqual([row["max_output_tokens"] for row in self.transport.calls], [3000, 1800, 3000])
        self.assertTrue(all(row["reasoning"] == {"effort": "medium"} for row in self.transport.calls))

    async def test_large_specialist_input_is_skipped_before_generation_preserving_writer(self):
        self.config = config(request_nano=11_000_000)
        self.budget.config = self.config
        agent = self.agent([response([function("guilde", {})]), answer("Réponse finale.")])
        count = self.transport.count

        async def specialist_count(payload):
            measured = await count(payload)
            return 7000 if payload["max_output_tokens"] == 1800 else measured

        self.transport.count = specialist_count
        await agent.answer(context(self.config), "Approfondis la guilde", 8)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual([row["max_output_tokens"] for row in self.transport.count_calls], [3000, 1800, 3000])
        status = await self.budget.status()
        self.assertEqual(status["calls"], 2)
        self.assertEqual(status["pending_nano"], 0)

    async def test_concurrent_daily_quota_cannot_spend_the_reserved_writer(self):
        self.config = config(user_daily_calls=3)
        self.budget.config = self.config
        agent = self.agent([response([function("guilde", {})]), answer("Réponse conservée.")])
        reserve = self.budget.reserve

        async def consume_last_slot(request_key, user_key, maximum):
            if request_key == "1:9:1":
                concurrent = await reserve("another-question", user_key, quote(800, 60))
                await self.budget.settle(concurrent, 800, 60)
            return await reserve(request_key, user_key, maximum)

        self.budget.reserve = consume_last_slot
        self.assertEqual(await agent.answer(context(self.config), "Approfondis la guilde", 9),
                         "Réponse conservée.")
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.transport.calls[-1]["max_output_tokens"], 3000)
        status = await self.budget.status()
        self.assertEqual(status["calls"], 3)
        self.assertEqual(status["pending_nano"], 0)

    async def test_none_remains_functional_for_both_normal_and_specialist_paths(self):
        self.config = config(reasoning_effort="none")
        self.budget.config = self.config
        agent = self.agent([
            response([function("guilde", {})]), answer("Note courte."), answer("Réponse finale."),
        ])
        await agent.answer(context(self.config), "Approfondis la guilde", 10)
        self.assertEqual([row["max_output_tokens"] for row in self.transport.calls], [400, 250, 400])
        self.assertTrue(all(row["reasoning"] == {"effort": "none"} for row in self.transport.calls))


if __name__ == "__main__":
    unittest.main()
