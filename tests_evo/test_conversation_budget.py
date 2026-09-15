"""Je vérifie les générations réelles simulées, leur budget commun et les suivis."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import Transport, answer, config, context, function, response
from utils.evo_agent import EvoAgent, MeteredModel, Sessions
from utils.evo_budget import BudgetLimitError, quote
from utils.evo_config import EvoError
from utils.evo_memory import followup_tools, wants_depth


class ConversationBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = config()
        self.budget = await create_budget(self.config)

    async def asyncTearDown(self):
        await self.budget.close()

    def make_agent(self, responses, handler=None):
        self.transport = Transport(responses)
        self.tools = None if handler is None else SimpleNamespace(execute=AsyncMock(side_effect=handler))
        return EvoAgent(self.config, MeteredModel(self.config, self.budget, self.transport), self.tools)

    async def test_deep_request_has_one_readonly_specialist_and_shared_budget(self):
        agent = self.make_agent([
            response([function("guilde", {})]), answer("Note de conseil."), answer("Réponse naturelle."),
        ])
        result = await agent.answer(context(self.config), "Approfondis la présentation de la guilde", 1)
        self.assertEqual(result, "Réponse naturelle.")
        self.assertEqual(len(self.transport.calls), 3)
        specialist, writer = self.transport.calls[1:]
        self.assertEqual(specialist["tools"], [])
        self.assertEqual(specialist["tool_choice"], "none")
        self.assertEqual(specialist["max_output_tokens"], 1800)
        self.assertEqual(writer["max_output_tokens"], 3000)
        self.assertIn("Note de conseil", json.dumps(writer["input"]))
        status = await self.budget.status()
        self.assertEqual(status["calls"], 3)
        self.assertEqual(status["pending_nano"], 0)
        self.assertEqual(status["input_tokens"], 2400)
        self.assertEqual(status["used_nano"], 3 * quote(800, 60))

    async def test_normal_request_does_not_delegate(self):
        agent = self.make_agent([response([function("guilde", {})]), answer("Réponse unique.")])
        await agent.answer(context(self.config), "Présentation complexe et détaillée de la guilde", 2)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertTrue(all(call["max_output_tokens"] == 3000 for call in self.transport.calls))

    async def test_specialist_reservation_race_keeps_reserved_writer(self):
        agent = self.make_agent([response([function("guilde", {})]), answer("Réponse conservée.")])
        reserve = self.budget.reserve

        async def race(request_key, user_key, maximum):
            if request_key.endswith(":1"):
                raise BudgetLimitError("Solde réservé par une demande concurrente")
            return await reserve(request_key, user_key, maximum)

        self.budget.reserve = race
        result = await agent.answer(context(self.config), "Approfondis la guilde", 20)
        self.assertEqual(result, "Réponse conservée.")
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual((await self.budget.status())["pending_nano"], 0)

    async def test_deep_specialist_is_skipped_when_only_writer_quota_remains(self):
        self.config = config(user_daily_calls=2)
        self.budget.config = self.config
        agent = self.make_agent([response([function("guilde", {})]), answer("Réponse sans spécialiste.")])
        result = await agent.answer(context(self.config), "Présentation de la guilde", 3, deepen=True)
        self.assertEqual(result, "Réponse sans spécialiste.")
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual((await self.budget.status())["calls"], 2)

    async def test_deep_falls_back_to_normal_for_a_small_request_envelope(self):
        self.config = config(request_nano=9_000_000)
        self.budget.config = self.config
        agent = self.make_agent([response([function("guilde", {})]), answer("Réponse économique.")])
        result = await agent.answer(context(self.config), "Approfondis la guilde", 4)
        self.assertEqual(result, "Réponse économique.")
        self.assertEqual(len(self.transport.calls), 2)
        self.assertLessEqual((await self.budget.status())["used_nano"], self.config.request_nano)

    async def test_pp_followup_keeps_reference_and_has_one_ia_writer(self):
        async def execute(name, raw, ctx, offered):
            params = json.loads(raw)
            return {"objet": "Laine Test", "reference": "item:1", "pp_personnelle": params["pp"]}

        agent = self.make_agent([
            response([function("sources_drop", {"objet": "Laine Test", "pp": 435, "pp_groupe": None})]),
            answer("Avec 435 PP, voici le résultat."), answer("Avec 600 PP, voici le nouveau résultat."),
        ], execute)
        ctx = context(self.config)
        await agent.answer(ctx, "Laine Test avec 435 PP", 5)
        result = await agent.answer(ctx, "Et avec 600 PP ?", 6)
        self.assertIn("600 PP", result)
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual(json.loads(self.tools.execute.await_args.args[1]),
                         {"objet": "item:1", "pp": 600, "pp_groupe": None})
        self.assertEqual(self.transport.calls[-1]["tool_choice"], "auto")

    async def test_quantity_followup_multiplies_in_tool_then_ia_writes(self):
        async def execute(name, raw, ctx, offered):
            params = json.loads(raw)
            return {"objet": "Cape Test", "reference": "item:2", "quantite": params["quantite"],
                    "ingredients": [{"nom": "Laine", "quantite": params["quantite"] * 3}]}

        agent = self.make_agent([
            response([function("recette", {"objet": "Cape Test", "quantite": 1, "avec_sources": False, "page": 1})]),
            answer("Il te faut 3 laines."), answer("Pour cinq capes, il te faut 15 laines."),
        ], execute)
        ctx = context(self.config)
        await agent.answer(ctx, "Recette Cape Test", 7)
        result = await agent.answer(ctx, "J’en veux 5", 8)
        self.assertIn("15 laines", result)
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual(json.loads(self.tools.execute.await_args.args[1])["quantite"], 5)

    async def test_greeting_is_written_by_ia_without_any_tools(self):
        agent = self.make_agent([answer("Salut ! Tu prépares quoi aujourd’hui ?")])
        self.assertIn("prépares", await agent.answer(context(self.config), "Salut", 9))
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.transport.calls[0]["tools"], [])

    async def test_action_has_durable_writer_reservation_before_mutation(self):
        snapshots = []

        async def execute(name, raw, ctx, offered):
            await ctx.before_mutation()
            snapshots.append(await self.budget.status())
            ctx.action_receipt = {"action_effectuee": True, "action": "definir_mon_metier"}
            return ctx.action_receipt

        agent = self.make_agent([
            response([function("definir_mon_metier", {"metier": "Bûcheron", "niveau": 100})]),
            answer("Ton métier est ajouté."),
        ], execute)
        await agent.answer(context(self.config), "Ajoute Bûcheron niveau 100 à mon profil", 10)
        self.assertEqual(snapshots[0]["calls"], 2)
        self.assertGreater(snapshots[0]["pending_nano"], 0)
        self.assertEqual((await self.budget.status())["pending_nano"], 0)

    async def test_action_receipt_survives_failed_ia_without_reexecution(self):
        async def execute(name, raw, ctx, offered):
            await ctx.before_mutation()
            ctx.action_receipt = {"action_effectuee": True, "action": "definir_mon_metier"}
            return ctx.action_receipt

        agent = self.make_agent([
            response([function("definir_mon_metier", {"metier": "Bûcheron", "niveau": 100})]),
            EvoError("Fournisseur indisponible"),
        ], execute)
        result = await agent.answer(context(self.config), "Ajoute Bûcheron niveau 100 à mon profil", 11)
        self.assertIn("action a été enregistrée", result)
        self.assertEqual(self.tools.execute.await_count, 1)
        self.assertGreater((await self.budget.status())["pending_nano"], 0)

    async def test_second_mutation_in_same_question_is_not_executed(self):
        async def execute(name, raw, ctx, offered):
            await ctx.before_mutation()
            ctx.action_receipt = {"action_effectuee": True}
            return ctx.action_receipt

        agent = self.make_agent([
            response([function("definir_mon_metier", {"metier": "Bûcheron", "niveau": 100}, "one"),
                      function("supprimer_mon_metier", {"metier": "Paysan"}, "two")]),
            answer("Bûcheron ajouté. Une seule modification à la fois."),
        ], execute)
        await agent.answer(context(self.config), "Ajoute Bûcheron 100 et supprime Paysan de mon profil", 12)
        self.assertEqual(self.tools.execute.await_count, 1)

    async def test_readonly_tools_are_parallel_but_bounded(self):
        active = 0
        peak = 0

        async def execute(name, raw, ctx, offered):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"nom": raw}

        agent = self.make_agent([
            response([function("membre", {"nom": str(i)}, str(i)) for i in range(5)]), answer("Voilà."),
        ], execute)
        await agent.answer(context(self.config), "Compare les profils des membres", 13)
        self.assertEqual(peak, 3)
        self.assertEqual(active, 0)

    async def test_revocation_during_generation_discards_private_answer(self):
        ctx = context(self.config)
        valid = [True]

        def check():
            if not valid[0]:
                raise EvoError("Partage révoqué")

        async def execute(name, raw, context, offered):
            context.before_publish = check
            return {"puits": "ETAT_PRIVE"}

        agent = self.make_agent([
            response([function("ma_session_fm", {})]), answer("ETAT_PRIVE"),
        ], execute)
        create = self.transport.create

        async def revoke_at_writer(payload):
            result = await create(payload)
            if len(self.transport.calls) == 2:
                valid[0] = False
            return result

        self.transport.create = revoke_at_writer
        with self.assertRaisesRegex(EvoError, "révoqué"):
            await agent.answer(ctx, "Combien de puits dans mon atelier ?", 14)
        self.assertFalse(agent.sessions.items)

    async def test_revocation_during_reservation_prevents_private_generation(self):
        ctx = context(self.config)
        valid = [True]

        def check():
            if not valid[0]:
                raise EvoError("Partage révoqué")

        async def execute(name, raw, context, offered):
            context.before_publish = check
            return {"puits": "ETAT_PRIVE"}

        agent = self.make_agent([response([function("ma_session_fm", {})])], execute)
        reserve = self.budget.reserve

        async def revoke_at_reservation(request_key, user_key, maximum):
            result = await reserve(request_key, user_key, maximum)
            if request_key.endswith(":1"):
                valid[0] = False
            return result

        self.budget.reserve = revoke_at_reservation
        with self.assertRaisesRegex(EvoError, "révoqué"):
            await agent.answer(ctx, "Mon puits ?", 21)
        self.assertEqual(len(self.transport.calls), 1)


class BriefTests(unittest.TestCase):
    def test_depth_requires_explicit_direct_request(self):
        for text in ("approfondis ce choix", "Approfondis", "Evo : approfondis le conseil"):
            self.assertTrue(wants_depth(text))
        for text in ("N'approfondis pas", "Il a dit approfondis", '"approfondis"', "Peux-tu expliquer approfondis ?"):
            self.assertFalse(wants_depth(text))

    def test_ordinals_follow_displayed_order_and_omit_unshown_objects(self):
        sessions = Sessions(config())
        memory = sessions.save((1, 10, 2), "Coiffes terre", "Beta puis Alpha.", [{
            "outil": "chercher_equipements", "parametres": {}, "resultat": {"resultats": [
                {"objet": "Alpha", "reference": "item:1"}, {"objet": "Beta", "reference": "item:2"},
                {"objet": "Gamma", "reference": "item:3"},
            ]},
        }], set())
        self.assertEqual(followup_tools(memory.brief, "Le deuxième"), [("fiche_objet", {"objet": "item:1"})])
        self.assertEqual(followup_tools(memory.brief, "Le troisième"), [])

    def test_shared_exo_state_is_not_kept_in_conversation_memory(self):
        sessions = Sessions(config())
        memory = sessions.save((1, 10, 2), "Mon puits ?", "ETAT_PRIVE", [{
            "outil": "ma_session_fm", "resultat": {"puits": "ETAT_PRIVE"},
        }], set())
        self.assertEqual(memory.turns, [])
        self.assertEqual(memory.evidence, [])
        self.assertNotIn("ETAT_PRIVE", json.dumps(memory.brief))

    def test_multiple_searches_merge_selection_in_published_order(self):
        sessions = Sessions(config())
        evidence = [{"outil": "chercher_equipements", "parametres": {}, "resultat": {
            "resultats": [{"objet": label, "reference": reference}],
        }} for label, reference in [("Beta", "item:2"), ("Alpha", "item:1")]]
        memory = sessions.save((1, 10, 2), "Compare", "Alpha puis Beta", evidence, set())
        self.assertEqual(followup_tools(memory.brief, "Le premier"), [("fiche_objet", {"objet": "item:1"})])

    def test_character_constraints_survive_the_two_turn_history(self):
        sessions = Sessions(config())
        key = (1, 10, 2)
        sessions.save(key, "Je suis Cra terre 130", "Quels objectifs ?", [], set())
        sessions.save(key, "Minimum 10 PA 6 PM pour PvM", "Bien reçu.", [], set())
        sessions.save(key, "Compare ces coiffes", "Deux choix.", [], set())
        memory = sessions.save(key, "Et les capes ?", "Voici.", [], set())
        self.assertEqual(memory.brief["preferences"], {
            "classe": "cra", "element": "terre", "niveau": "130", "pa": "10", "pm": "6", "usage": "pvm",
        })
