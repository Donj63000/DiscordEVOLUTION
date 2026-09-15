"""Je vérifie la lecture facultative, la réponse directe et leurs réservations."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import answer, config, context, function, response
from tests_evo.test_reasoning_budget import CapturingTransport, incomplete, reasoning
from utils.evo_agent import EvoAgent, MeteredModel, ProviderError
from utils.evo_budget import quote
from utils.evo_config import EvoError
from utils.evo_tools import MUTATING_TOOLS


class AdaptiveAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = config()
        self.budget = await create_budget(self.config)

    async def asyncTearDown(self):
        await self.budget.close()

    def agent(self, replies, *, execute=None, count=800):
        self.transport = CapturingTransport(replies, count=count)
        self.model = MeteredModel(self.config, self.budget, self.transport)
        self.tools = SimpleNamespace(execute=AsyncMock(side_effect=execute or self.read))
        return EvoAgent(self.config, self.model, self.tools)

    async def read(self, name, raw, ctx, offered):
        return {"nom": name, "resultat_verifie": json.loads(raw)}

    async def test_direct_second_answer_releases_unused_writer_without_phantom_call(self):
        agent = self.agent([response([function("guilde", {})]), answer("Réponse vérifiée.")])
        result = await agent.answer(context(self.config), "Présente la guilde", 1)
        self.assertEqual(result, "Réponse vérifiée.")
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.transport.calls[1]["tool_choice"], "auto")
        status = await self.budget.status()
        self.assertEqual(status["calls"], 2)
        self.assertEqual(status["pending_nano"], 0)
        self.assertEqual(status["used_nano"], 2 * quote(800, 60))

    async def test_uncertainty_reads_again_before_third_final_generation(self):
        snapshots = []

        async def read(name, raw, ctx, offered):
            if name == "connaissances_guilde":
                snapshots.append(await self.budget.status())
            return await self.read(name, raw, ctx, offered)

        second = [reasoning("verification"), function("connaissances_guilde", {"question": "règles"}, "verify")]
        agent = self.agent([
            response([function("guilde", {})]), response(second), answer("Les règles vérifiées."),
        ], execute=read)
        result = await agent.answer(context(self.config), "Vérifie les règles de la guilde", 2)
        self.assertEqual(result, "Les règles vérifiées.")
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual([payload["tool_choice"] for payload in self.transport.calls], ["required", "auto", "none"])
        self.assertEqual(snapshots[0]["pending_nano"], quote(self.config.max_input + 64, 3000))
        self.assertEqual(snapshots[0]["calls"], 3)
        self.assertTrue(all(payload["reasoning"] == {"effort": "high"} for payload in self.transport.calls))
        self.assertTrue(all(tool["name"] not in MUTATING_TOOLS for tool in self.transport.calls[1]["tools"]))
        self.assertIn(second[0], self.transport.calls[-1]["input"])
        self.assertEqual((await self.budget.status())["pending_nano"], 0)

    async def test_mutation_requested_by_verification_is_never_executed(self):
        agent = self.agent([
            response([function("guilde", {})]),
            response([function("definir_mon_metier", {"metier": "Paysan", "niveau": 100}, "forbidden")]),
            answer("Aucune modification effectuée."),
        ])
        await agent.answer(context(self.config), "Présente la guilde", 3)
        self.assertEqual(self.tools.execute.await_count, 1)
        outputs = [item for item in self.transport.calls[-1]["input"] if item.get("type") == "function_call_output"]
        self.assertIn("erreur", json.loads(outputs[-1]["output"]))
        self.assertEqual(len(self.transport.calls), 3)

    async def test_action_uses_its_reserved_final_without_optional_verification(self):
        async def mutate(name, raw, ctx, offered):
            await ctx.before_mutation()
            ctx.action_receipt = {"action_effectuee": True}
            return ctx.action_receipt

        agent = self.agent([
            response([function("definir_mon_metier", {"metier": "Paysan", "niveau": 100})]),
            answer("Ton métier est enregistré."),
        ], execute=mutate)
        await agent.answer(context(self.config), "Ajoute Paysan niveau 100 à mon profil", 4)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.transport.calls[1]["tool_choice"], "none")
        self.assertEqual((await self.budget.status())["calls"], 2)

    async def test_paid_usage_frees_local_estimate_for_an_affordable_third_generation(self):
        agent = self.agent([
            response([function("guilde", {})]),
            response([function("connaissances_guilde", {"question": "règles"}, "verify")]),
            answer("Réponse finale."),
        ], count=self.config.max_input)
        await agent.answer(context(self.config), "Les règles de la guilde", 5)
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual((await self.budget.status())["used_nano"], 3 * quote(800, 60))

    async def test_tight_envelope_skips_verification_without_reserving_an_extra_call(self):
        self.config = config(request_nano=9_000_000)
        self.budget.config = self.config
        agent = self.agent([response([function("guilde", {})]), answer("Réponse finale.")])
        await agent.answer(context(self.config), "La guilde", 6)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual([payload["tool_choice"] for payload in self.transport.count_calls], ["required", "auto", "none"])
        self.assertEqual(self.transport.calls[-1]["tool_choice"], "none")
        status = await self.budget.status()
        self.assertEqual(status["calls"], 2)
        self.assertEqual(status["pending_nano"], 0)

    async def test_last_user_quota_slot_is_kept_for_final_answer(self):
        self.config = config(user_daily_calls=2)
        self.budget.config = self.config
        agent = self.agent([response([function("guilde", {})]), answer("Réponse finale.")])
        await agent.answer(context(self.config), "La guilde", 7)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.transport.calls[-1]["tool_choice"], "none")
        self.assertEqual((await self.budget.status())["calls"], 2)

    async def test_incomplete_optional_generation_does_not_read_or_retry(self):
        agent = self.agent([
            response([function("guilde", {})]),
            incomplete([function("connaissances_guilde", {"question": "règles"}, "verify")]),
        ])
        with self.assertRaisesRegex(EvoError, "relance automatique"):
            await agent.answer(context(self.config), "La guilde", 8)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.tools.execute.await_count, 1)
        self.assertGreater((await self.budget.status())["pending_nano"], 0)

    async def test_provider_error_keeps_ambiguous_reservations_without_retry(self):
        agent = self.agent([response([function("guilde", {})]), ProviderError("Indisponible")])
        with self.assertRaises(ProviderError):
            await agent.answer(context(self.config), "La guilde", 9)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual((await self.budget.status())["calls"], 3)
        self.assertGreater((await self.budget.status())["pending_nano"], quote(7564, 3000))

    async def test_permission_loss_after_verification_prevents_extra_read_and_publication(self):
        ctx = context(self.config)
        agent = self.agent([
            response([function("guilde", {})]),
            response([function("connaissances_guilde", {"question": "règles"}, "verify")]),
        ])
        create = self.transport.create

        async def revoke(payload):
            result = await create(payload)
            if len(self.transport.calls) == 2:
                ctx.channel.denied.add(ctx.member.id)
            return result

        self.transport.create = revoke
        with self.assertRaises(EvoError):
            await agent.answer(ctx, "La guilde", 10)
        self.assertEqual(self.tools.execute.await_count, 1)
        self.assertFalse(agent.sessions.items)

    async def test_prepared_followup_stays_one_generation_and_resets_web_limits(self):
        agent = self.agent([answer("Trois membres."), answer("Toujours trois membres.")])
        ctx = context(self.config)
        for trigger in (11, 12):
            ctx.web_pages.add("https://moon-bot.fr/ancienne-page")
            ctx.web_links.add("https://moon-bot.fr/ancien-lien")
            await agent.answer(ctx, "Combien de membres sur le serveur ?", trigger)
            self.assertFalse(ctx.web_pages)
            self.assertFalse(ctx.web_links)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual((await self.budget.status())["calls"], 2)

    async def test_third_generation_cannot_start_another_verification_loop(self):
        agent = self.agent([
            response([function("guilde", {})]),
            response([function("connaissances_guilde", {"question": "règles"}, "verify")]),
            response([function("membre", {"nom": "Alex"}, "fourth")]),
        ])
        with self.assertRaises(EvoError):
            await agent.answer(context(self.config), "La guilde et ses membres", 13)
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual(self.tools.execute.await_count, 2)
        self.assertFalse(agent.sessions.items)


if __name__ == "__main__":
    unittest.main()
