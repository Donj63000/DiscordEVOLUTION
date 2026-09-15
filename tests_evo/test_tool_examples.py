"""Cas de consultation publics : chiffres Python et pages sans perte silencieuse."""
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from tests_evo.helpers import context, entry
from utils.dofus_wiki import WikiDetail
from utils.evo_config import EvoError
from utils.evo_equipment import Equipment, compare_equipment, exo_candidates
from utils.evo_tools import BY_NAME, EvoTools, MUTATING_TOOLS, drop_payload, schemas_for
from utils.xixou_api import DropSource, ItemEnrichment


class DropRankingTests(unittest.TestCase):
    def result(self, *sources, pp=None, pp_groupe=None):
        detail = WikiDetail(entry(category="Ressource"), {}, None, False)
        return drop_payload(detail, ItemEnrichment(drops=sources), pp, pp_groupe)

    def test_best_source_is_ranked_before_the_limit(self):
        sources = [DropSource(f"Monstre {i}", "1 %") for i in range(15)]
        result = self.result(*sources, DropSource("Meilleur taux", "2 %"))
        self.assertEqual(result["sources_drop"][0]["monstre"], "Meilleur taux")
        self.assertEqual(result["meilleure_source_taux"], "Meilleur taux")
        self.assertEqual(result["nombre_sources"], 16)

    def test_overlapping_ranges_never_claim_a_best_source(self):
        result = self.result(DropSource("A", "1–5 %"), DropSource("B", "2–4 %"))
        self.assertEqual(result["sources_drop"][0]["monstre"], "B")
        self.assertIsNone(result["meilleure_source_taux"])
        self.assertIn("chevauchent", result["comparaison"])

    def test_unknown_source_is_not_zero_or_silently_outclassed(self):
        result = self.result(DropSource("Inconnu"), DropSource("Connu", "5 %"))
        self.assertIsNone(result["sources_drop"][-1]["plage_classee"])
        self.assertIsNone(result["meilleure_source_taux"])
        self.assertIn("inconnus", result["comparaison"])

    def test_level_rates_can_disprove_an_apparent_best_source(self):
        result = self.result(
            DropSource("A", "5 %", level_rates=(("100", "1 %"),)),
            DropSource("B", "3 %"),
        )
        self.assertEqual(result["sources_drop"][0]["monstre"], "B")
        self.assertIsNone(result["meilleure_source_taux"])

    def test_unknown_pp_threshold_makes_ranking_conditional(self):
        result = self.result(
            DropSource("A", "2 %", pp="500"), DropSource("B", "1 %", pp="100"), pp=435,
        )
        self.assertEqual(result["sources_drop"][0]["taux_personnel"], ["8,7 %", "8,7 %"])
        self.assertIsNone(result["meilleure_source_taux"])
        self.assertIn("conditionnelle", result["comparaison"])

    def test_equal_known_rates_are_ties(self):
        result = self.result(DropSource("A", "1 %"), DropSource("B", "1 %"))
        self.assertIn("ex æquo", result["comparaison"])
        self.assertIsNone(result["meilleure_source_taux"])

    def test_zero_quota_cannot_win_even_when_personal_pp_is_not_given(self):
        result = self.result(DropSource("A", "99 %", maximum="0"), DropSource("B", "1 %", maximum="1"))
        self.assertEqual(result["meilleure_source_taux"], "B")
        self.assertEqual(result["sources_drop"][-1]["plage_classee"], ["0 %", "0 %"])


class EquipmentComparisonTests(unittest.TestCase):
    def test_stat_differences_are_signed_and_computed_in_python(self):
        rows = [
            {"reference": "item:1", "jets_naturels": {"Force": [31, 50], "Vitalité": [101, 150]}},
            {"reference": "item:2", "jets_naturels": {"Force": [21, 40], "Vitalité": [151, 200], "Portée": [1, 1]}},
        ]
        result = compare_equipment(rows)[0]
        differences = result["ecarts_premier_moins_second"]
        self.assertEqual(differences["Force"]["ecart_max"], 10)
        self.assertEqual(differences["Vitalité"]["ecart_min"], -50)
        self.assertEqual(differences["Portée"]["ecart_max"], -1)
        self.assertEqual(differences["Portée"]["avantage_jet_max"], "item:2")

    def test_missing_or_unparsed_effects_are_not_assumed_zero(self):
        rows = [
            {"reference": "item:1", "jets_naturels": {"Force": [10, 20]}, "effets_non_interpretes": ["Effet"]},
            {"reference": "item:2", "jets_naturels": {"Vitalité": [100, 200]}},
        ]
        self.assertEqual(compare_equipment(rows)[0]["ecarts_premier_moins_second"], {})
        rows[0]["jets_naturels"] = {}
        rows[0]["effets_non_interpretes"] = []
        self.assertFalse(compare_equipment(rows)[0]["comparaison_complete"])

    def test_named_exo_candidates_exclude_unrequested_items_and_explain_rejections(self):
        rows = [
            Equipment(entry(str(i), f"Anneau {i}", "Anneau"), bounds, unknown, (), (), ())
            for i, bounds, unknown in (
                (1, {"fo": (10, 20)}, ()), (2, {"vi": (10, 20), "pa": (1, 1)}, ()),
                (3, {"vi": (10, 20)}, ("Effet inconnu",)), (4, {"fo": (1, 2)}, ()),
            )
        ]
        result = exo_candidates(rows, target="pa", category="anneau", min_level=1, max_level=200,
                                references=["item:1", "item:2", "item:3"])
        self.assertEqual([row["reference"] for row in result["resultats"]], ["item:1"])
        self.assertEqual({row["reference"] for row in result["exclus"]}, {"item:2", "item:3"})


class RecipeAndPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tools = EvoTools()
        self.ctx = context()
        self.item = entry()

    def recipe(self, count):
        detail = WikiDetail(self.item, {"recipe": [
            {"item_id": 100 + index, "qty": index + 1, "name": f"Ressource {index}"}
            for index in range(count)
        ]}, None, False)
        self.tools.resolve = AsyncMock(return_value=(self.item, None))
        self.tools.detail = AsyncMock(return_value=(detail, None))

    async def test_recipe_pages_cover_every_ingredient_without_repeating_sources(self):
        self.recipe(10)
        self.tools.do_sources_drop = AsyncMock(return_value={"sources_drop": [], "recoltes": []})
        first = await self.tools.do_recette(self.ctx, "item:1", 5, True, page=1)
        second = await self.tools.do_recette(self.ctx, "item:1", 5, True, page=2)
        self.assertEqual(first["page_suivante"], 2)
        self.assertIsNone(second["page_suivante"])
        self.assertEqual(len(first["ingredients"] + second["ingredients"]), 10)
        self.assertEqual(second["ingredients"][-1]["quantite"], 50)
        self.assertEqual(self.tools.do_sources_drop.await_count, 10)
        with self.assertRaises(EvoError):
            await self.tools.do_recette(self.ctx, "item:1", 5, True, page=3)

    async def test_recipe_groups_known_zones_without_losing_quantities(self):
        self.recipe(2)
        self.tools.do_sources_drop = AsyncMock(return_value={
            "sources_drop": [{"monstre": "Monstre", "zones": ["Zone partagée"]}], "recoltes": [],
        })
        result = await self.tools.do_recette(self.ctx, "item:1", 5, True)
        self.assertEqual(result["zones_communes"][0]["ingredients"], ["Ressource 0", "Ressource 1"])
        self.assertEqual([row["quantite"] for row in result["ingredients"]], [5, 10])

    async def test_large_source_payload_preserves_every_page_ingredient(self):
        self.recipe(8)
        self.tools.do_sources_drop = AsyncMock(return_value={
            "sources_drop": [{"monstre": "M" * 300, "zones": ["Z" * 300] * 5}] * 3,
            "recoltes": [{"ressource": "R" * 300, "metier": "Paysan"}] * 2,
        })
        result = await self.tools.execute("recette", json.dumps({
            "objet": "item:1", "quantite": 5, "avec_sources": True, "page": 1,
        }), self.ctx, {"recette"})
        self.assertEqual(len(result["ingredients"]), 8)
        self.assertEqual([row["quantite"] for row in result["ingredients"]], list(range(5, 41, 5)))
        self.assertIn("quantités", result["portee_sources"])

    async def test_monster_pages_expose_remaining_catalog_drops(self):
        monster = entry("7", "Monstre Test", "Monstre", 10, "monster")
        detail = WikiDetail(monster, {"id": 77, "grades": []}, None, False)
        catalog = {"data": [{"id": 77, "name": "Monstre Test", "drops": [
            {"name": f"Ressource {index}", "taux": "1 %", "pp": 100, "max": 8}
            for index in range(12)
        ]}]}
        self.ctx.bot.cogs["DofusWikiCog"] = NS(enrichment_client=NS(
            enabled=True, catalog=AsyncMock(return_value=catalog),
        ))
        self.tools.resolve = AsyncMock(return_value=(monster, None))
        self.tools.detail = AsyncMock(return_value=(detail, None))
        first = await self.tools.do_monstre(self.ctx, "Monstre Test", page=1)
        second = await self.tools.do_monstre(self.ctx, "Monstre Test", page=2)
        self.assertEqual(first["page_suivante"], 2)
        self.assertEqual([row["ressource"] for row in second["drops"]], ["Ressource 10", "Ressource 11"])


class CatalogueScopeTests(unittest.TestCase):
    def test_topic_catalogues_stay_small_and_preserve_cross_domain_crafting(self):
        drop = {row["name"] for row in schemas_for("et avec 600 PP sources_drop")}
        self.assertEqual(drop, {
            "sources_drop", "monstre", "fiche_objet", "aide_bot",
            "demander_precision", "consulter_site",
        })
        recipe = {row["name"] for row in schemas_for("qui peut craft ça")}
        self.assertIn("recette", recipe)
        self.assertIn("artisans", recipe)
        self.assertNotIn("poser_rune", recipe)

    def test_mutating_tools_never_accept_an_actor_or_discord_target(self):
        for name in MUTATING_TOOLS:
            fields = set(BY_NAME[name]["schema"]["parameters"]["properties"])
            self.assertFalse(fields & {"user_id", "guild_id", "channel_id", "owner_id", "confirme"})


if __name__ == "__main__":
    unittest.main()
