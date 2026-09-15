"""Outils métier, consentement, absence d'effets de bord et sources vérifiées."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests_evo.helpers import config, context, entry, Guild, Channel, Member
from utils.dofus_wiki import WikiDetail
from utils.evo_config import EvoError
from utils.evo_equipment import build_index, category_name, equipment_search, exo_candidates
from utils.evo_safety import bounded_json, clean, output_text, parse_arguments
from utils.evo_tools import BY_NAME, EvoTools, drop_payload, schemas_for
from utils.xixou_api import DropSource, ItemEnrichment, monster_record


def enrichment(*drops):
    return ItemEnrichment(drops=tuple(drops))


class SafetyTests(unittest.TestCase):
    def test_schema_rejects_extra_and_duplicate_fields(self):
        schema = BY_NAME["membre"]["schema"]["parameters"]
        for args in ('{"nom":"moi","guild_id":99}', '{"nom":"moi","nom":"autre"}', '{"nom":5}'):
            with self.subTest(args=args), self.assertRaises(EvoError):
                parse_arguments(args, schema)

    def test_schema_rejects_null_booleans_and_unbounded_numbers(self):
        schema = BY_NAME["sources_drop"]["schema"]["parameters"]
        for args in ('{"objet":"x","pp":true,"pp_groupe":null}',
                     '{"objet":"x","pp":10001,"pp_groupe":null}',
                     '{"objet":"x","pp":NaN,"pp_groupe":null}'):
            with self.subTest(args=args), self.assertRaises(EvoError):
                parse_arguments(args, schema)

    def test_schema_valid_nulls(self):
        result = parse_arguments('{"objet":"x","pp":435,"pp_groupe":null}',
                                 BY_NAME["sources_drop"]["schema"]["parameters"])
        self.assertEqual(result["pp"], 435)

    def test_no_unrestricted_tools(self):
        forbidden = {"exec", "run_bot_command", "shell", "sql", "ban_member", "web_search"}
        self.assertFalse(forbidden.intersection(BY_NAME))
        for entry_ in BY_NAME.values():
            schema = entry_["schema"]
            self.assertEqual(schema["type"], "function")
            self.assertTrue(schema["strict"])
            self.assertFalse(schema["parameters"]["additionalProperties"])
            self.assertEqual(set(schema["parameters"]["required"]), set(schema["parameters"]["properties"]))

    def test_small_catalogue_selection(self):
        game = {s["name"] for s in schemas_for("où drop cette ressource")}
        guild = {s["name"] for s in schemas_for("membres de la guilde")}
        self.assertIn("sources_drop", game)
        self.assertNotIn("membre", game)
        self.assertIn("membre", guild)
        self.assertNotIn("sources_drop", guild)

    def test_redaction_mentions_and_unverified_links(self):
        safe = "https://wiki.moon-bot.io/items/test/"
        result = output_text("@everyone sk-12345678901234567890 https://bad.test/ " + safe, {safe})
        self.assertNotIn("@everyone", result)
        self.assertNotIn("12345678901234567890", result)
        self.assertNotIn("https://bad.test", result)
        self.assertIn(safe, result)

    def test_utf16_length_and_valid_json(self):
        result = output_text("😀" * 2500, set())
        self.assertLessEqual(len(result.encode("utf-16-le")) // 2, 1800)
        payload = json.loads(bounded_json({"x": [{"y": "z" * 1000} for _ in range(100)]}, 800))
        self.assertIsInstance(payload, dict)

    def test_source_allowlist(self):
        ctx = context()
        for url in ("http://wiki.moon-bot.io/x", "https://wiki.moon-bot.io.evil/x",
                    "https://secret@wiki.moon-bot.io/x", "https://wiki.moon-bot.io:9999/x"):
            self.assertEqual(ctx.source(url), "")
        self.assertTrue(ctx.source("https://wiki.moon-bot.io/items/x/"))

    def test_permission_and_guild_checks(self):
        ctx = context()
        ctx.check()
        ctx.channel.denied.add(ctx.member.id)
        with self.assertRaises(EvoError):
            ctx.check()
        ctx = context()
        ctx.guild.id = 999
        with self.assertRaises(EvoError):
            ctx.check()

    def test_cross_private_channel_never_exposed(self):
        ctx = context()
        self.assertFalse(ctx.readable_here(ctx.guild.get_channel(20)))
        self.assertTrue(ctx.readable_here(ctx.guild.get_channel(30)))
        self.assertFalse(ctx.readable_here(Channel(Guild(999), 10)))

    def test_public_channels_allowed_without_an_allowlist(self):
        ctx = context(config(channel_ids=frozenset()))
        ctx.check()
        ctx.channel = ctx.guild.get_channel(30)
        ctx.check()
        ctx.channel = ctx.guild.get_channel(20)
        with self.assertRaisesRegex(EvoError, "salons publics"):
            ctx.check()

    def test_allowlist_cannot_enable_a_private_channel(self):
        ctx = context(config(channel_ids=frozenset({20})))
        ctx.channel = ctx.guild.get_channel(20)
        with self.assertRaisesRegex(EvoError, "salons publics"):
            ctx.check()

    def test_console_excluded_even_when_public_and_allowlisted(self):
        ctx = context()
        ctx.channel.name = "archives-bot"
        with patch.dict("os.environ", {"CONSOLE_CHANNEL_NAME": "archives-bot"}, clear=True):
            with self.assertRaisesRegex(EvoError, "console"):
                ctx.check()


class EquipmentTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            entry("1", "Coiffe Alpha", level=120), entry("2", "Coiffe Beta", level=115),
            entry("3", "Coiffe Gamma", level=130), entry("4", "Coiffe Delta", level=100),
            entry("5", "Coiffe Native PA", level=120),
        ]
        self.catalog = {"famille": "equipements", "genere_le": "2026-09-15", "data": {
            "chapeaux": [
                {"name": e.name, "level": str(e.level), "effets": effects}
                for e, effects in zip(self.entries, [
                    ["+31 à 50 Force", "+101 à 150 Vitalité"],
                    ["+21 à 40 Force", "+151 à 200 Vitalité"],
                    ["+61 à 80 Force", "-20 à -10 Sagesse"],
                    ["+20 Force", "Effet non interprété"],
                    ["+50 Force", "+1 PA"],
                ])
            ]}}
        self.rows, self.info = build_index(self.entries, self.catalog)

    def test_actual_effects_indexed(self):
        self.assertEqual(len(self.rows), 5)
        self.assertEqual(self.rows[0].bounds["fo"], (31, 50))

    def test_level_type_and_lexicographic_priority(self):
        result = equipment_search(self.rows, category="coiffe", min_level=110, max_level=120,
                                  priorities=["force", "vitalite"], no_malus=[], query="")
        self.assertEqual([v["objet"] for v in result["resultats"]], ["Coiffe Alpha", "Coiffe Beta"])
        self.assertIn("lexicographique", result["classement"])

    def test_no_malus_excludes_negative_range(self):
        result = equipment_search(self.rows, category="coiffe", min_level=1, max_level=200,
                                  priorities=["force"], no_malus=["sagesse"], query="")
        self.assertNotIn("Coiffe Gamma", [v["objet"] for v in result["resultats"]])

    def test_exo_native_and_unknown_effects_excluded(self):
        result = exo_candidates(self.rows, target="pa", category="coiffe", min_level=1, max_level=200)
        names = [v["objet"] for v in result["resultats"]]
        self.assertNotIn("Coiffe Native PA", names)
        self.assertNotIn("Coiffe Delta", names)
        self.assertIn("n'augmente pas", result["limites"])
        self.assertNotIn("probabilite", result["resultats"][0])

    def test_ambiguous_catalog_identity_excluded(self):
        self.catalog["data"]["chapeaux"].append({"name": "Coiffe Alpha", "level": "120", "effets": ["+99 Force"]})
        rows, info = build_index(self.entries, self.catalog)
        self.assertNotIn("Coiffe Alpha", [r.entry.name for r in rows])
        self.assertEqual(info["identites_ambigues"], 1)

    def test_invalid_explicit_equipment_id_never_falls_back_to_name(self):
        row = self.catalog["data"]["chapeaux"][0]
        for identifier in ("invalid", True, [], {}, -1):
            with self.subTest(identifier=identifier):
                row["id"] = identifier
                rows, _ = build_index(self.entries, self.catalog)
                self.assertNotIn("Coiffe Alpha", [equipment.entry.name for equipment in rows])
                self.assertEqual(len(rows), 4)

    def test_invalid_type_and_level_order(self):
        with self.assertRaises(EvoError):
            category_name("type inventé")
        with self.assertRaises(EvoError):
            equipment_search(self.rows, category=None, min_level=150, max_level=100,
                             priorities=["force"], no_malus=[], query="")

    def test_monster_double_identity_required(self):
        detail = WikiDetail(entry("7", "Monstre Test", "Monstre", 10, "monster"), {"id": 77}, None, False)
        catalog = {"data": [{"id": 77, "name": "Monstre Test - Variante", "drops": []}]}
        self.assertIsNotNone(monster_record(catalog, detail))
        catalog["data"][0]["name"] = "Autre monstre"
        self.assertIsNone(monster_record(catalog, detail))


class DomainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tools = EvoTools()

    async def test_pp_uses_existing_exact_calculator(self):
        detail = WikiDetail(entry(category="Ressource"), {}, None, False)
        result = drop_payload(detail, enrichment(DropSource("Monstre", "1 %", pp="100", maximum="8")), 435, 1000)
        self.assertEqual(result["sources_drop"][0]["taux_personnel"], ["4,35 %", "4,35 %"])

    async def test_pp_unmet_threshold_zero(self):
        detail = WikiDetail(entry(category="Ressource"), {}, None, False)
        result = drop_payload(detail, enrichment(DropSource("Monstre", "1 %", pp="1000", maximum="1")), 435, 500)
        self.assertEqual(result["sources_drop"][0]["taux_personnel"], ["0 %", "0 %"])
        self.assertFalse(result["sources_drop"][0]["seuil_atteint"])

    async def test_missing_drop_data_not_impossibility(self):
        result = drop_payload(WikiDetail(entry(), {}, None, False), None)
        self.assertEqual(result["sources_drop"], [])
        self.assertIn("absente ne prouve pas", result["limites"])

    async def test_unknown_tool_cannot_execute(self):
        result = await self.tools.execute("run_bot_command", '{"command":"ban"}', context(), {"run_bot_command"})
        self.assertIn("erreur", result)

    async def test_unshared_exo_error_is_preserved(self):
        ctx = context()
        with patch("utils.evo_exo.read_shared_session", new=AsyncMock(
            side_effect=EvoError("Aucun atelier partagé dans ce salon.")
        )) as read:
            result = await self.tools.execute("ma_session_fm", "{}", ctx, {"ma_session_fm"})
        read.assert_awaited_once_with(ctx)
        self.assertIn("erreur", result)
        self.assertNotIn("jets", result)
        self.assertIn("ma_session_fm", BY_NAME)
        self.assertIn("ma_session_fm", {tool["name"] for tool in schemas_for("ma session exo")})

    async def test_other_member_data_requires_consent(self):
        ctx = context(cogs={"PlayersCog": NS(initialized=True, persos_data={"3": {"main": "SECRET_CHARACTER"}})})
        result = await self.tools.do_membre(ctx, "Alex")
        self.assertNotIn("personnage", result)

    async def test_only_current_members_and_no_crossguild_legacy(self):
        ctx = context(config(public_member_data=True))
        ctx.bot.cogs["JobCog"] = NS(initialized=True, jobs_data={
            "3": {"jobs": {"Tailleur": 100}}, "555": {"jobs": {"Tailleur": 100}}})
        result = await self.tools.do_artisans(ctx, "tailleur", 1)
        self.assertEqual(len(result["artisans"]), 1)
        ctx.bot.guilds.append(Guild(9))
        with self.assertRaises(EvoError):
            await self.tools.do_artisans(ctx, "tailleur", 1)

    async def test_scoped_profile_only(self):
        profile = NS(guild_id=999, owner_id=2, player_name="PRIVATE")
        ctx = context(cogs={"ProfilCog": NS(store=NS(get_by_owner=AsyncMock(return_value=profile)))})
        result = await self.tools.do_membre(ctx, "moi")
        self.assertNotIn("profil_declare", result)

    async def test_history_opt_in(self):
        ctx = context()
        with self.assertRaises(EvoError):
            await self.tools.do_conversation_salon(ctx)
        self.assertEqual(ctx.channel.history_calls, 0)
        ctx.config = replace(ctx.config, history_channels=frozenset({10}))
        ctx.channel.history_allowed = False
        with self.assertRaises(EvoError):
            await self.tools.do_conversation_salon(ctx)

    async def test_history_current_channel_bounded_without_bots(self):
        ctx = context(config(history_channels=frozenset({10})))
        for i in range(30):
            ctx.channel.messages.append(NS(
                author=ctx.member if i % 2 else ctx.guild.me, webhook_id=None,
                content="test " * 100, created_at=datetime.now(timezone.utc),
                jump_url=f"https://discord.com/channels/1/10/{i+1}"))
        result = await self.tools.do_conversation_salon(ctx)
        self.assertEqual(len(result["messages"]), 7)
        self.assertTrue(all(len(m["texte"]) <= 250 for m in result["messages"]))

    async def test_activity_drafts_and_private_audiences_hidden(self):
        ctx = context()
        date = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        base = {"starts_at": date, "participants": [2], "creator_id": 3, "capacity": 8}
        events = {
            "ok": {**base, "titre": "Sortie publique", "message_id": 88, "channel_id": 30},
            "staff": {**base, "titre": "Réunion privée", "message_id": 89, "channel_id": 20},
            "draft": {**base, "titre": "Brouillon", "channel_id": 30},
        }
        ctx.bot.cogs["ActiviteCog"] = NS(initialized=True, events_for_guild=lambda gid: events)
        result = await self.tools.do_activites(ctx, "", 7)
        self.assertEqual([a["titre"] for a in result["activites"]], ["Sortie publique"])
        self.assertEqual(result["activites"][0]["identifiant"], "ok")
        self.assertEqual(result["activites"][0]["places_restantes"], 7)
        self.assertTrue(result["activites"][0]["deja_inscrit"])

    async def test_knowledge_other_guild_not_read(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "knowledge.json"
            path.write_text(json.dumps({"guild_id": 9, "facts": [{"text": "Secret guilde", "title": "guilde"}]}))
            ctx = context(config(knowledge_path=str(path)))
            result = await self.tools.do_connaissances_guilde(ctx, "guilde")
            self.assertEqual(result["faits"], [])

    async def test_craft_quantity_computed_in_python(self):
        ctx = context()
        e = entry()
        detail = WikiDetail(e, {"recipe": [
            {"item_id": 2, "qty": 4, "name": "Ressource Test"},
            {"item_id": 2, "qty": 2, "name": "Ressource Test"}]}, None, False)
        self.tools.resolve = AsyncMock(return_value=(e, None))
        self.tools.detail = AsyncMock(return_value=(detail, None))
        result = await self.tools.do_recette(ctx, "objet", 3, False)
        self.assertEqual(result["ingredients"][0]["quantite"], 18)

    async def test_monster_inventory_from_matched_catalog(self):
        ctx = context()
        e = entry("7", "Monstre Test", "Monstre", 10, "monster")
        detail = WikiDetail(e, {"id": 77, "grades": [{"level": 10, "hp": 100, "ap": 6, "mp": 3}]}, None, False)
        catalog = {"data": [{"id": 77, "name": "Monstre Test", "zones": ["Zone Test"],
                             "drops": [{"name": "Ressource Test", "taux": "1 %", "pp": 100, "max": 1}]}]}
        client = NS(enabled=True, catalog=AsyncMock(return_value=catalog))
        ctx.bot.cogs["DofusWikiCog"] = NS(enrichment_client=client)
        self.tools.resolve = AsyncMock(return_value=(e, None))
        self.tools.detail = AsyncMock(return_value=(detail, None))
        result = await self.tools.do_monstre(ctx, "Monstre Test")
        self.assertEqual(result["drops"][0]["ressource"], "Ressource Test")
        self.assertEqual(result["zones"], ["Zone Test"])

    async def test_monster_inventory_normalizes_active_and_suspect_rank_rates(self):
        ctx = context()
        e = entry("7", "Monstre Test", "Monstre", 10, "monster")
        detail = WikiDetail(e, {"id": 77, "grades": []}, None, False)
        monster = {
            "id": 77, "name": "Monstre Test", "ranks_inactifs": [3], "ranks_suspects": [2],
            "stats": {"niveau": {"ranks": {"rank_1": "10", "rank_2": "20", "rank_3": "30"}}},
            "drops": [{"name": "Ressource Test", "taux": "90%", "pp": {}, "max": True,
                       "taux_ranks": [0.03, 0.06, 90]}],
        }
        client = NS(enabled=True, catalog=AsyncMock(return_value={"data": [monster]}))
        ctx.bot.cogs["DofusWikiCog"] = NS(enrichment_client=client)
        self.tools.resolve = AsyncMock(return_value=(e, None))
        self.tools.detail = AsyncMock(return_value=(detail, None))

        result = await self.tools.do_monstre(ctx, "Monstre Test")

        drop = result["drops"][0]
        self.assertEqual(drop["taux_base"], "0.03% – 0.06%")
        self.assertEqual(drop["taux_par_niveau"], [("10", "0.03%"), ("20 (à vérifier)", "0.06%")])
        self.assertIsNone(drop["seuil_pp"])
        self.assertIsNone(drop["quota_partage"])
        self.assertNotIn("taux_par_grade", drop)

        monster["ranks_inactifs"] = [1, 2, 3]
        result = await self.tools.do_monstre(ctx, "Monstre Test")
        self.assertIsNone(result["drops"][0]["taux_base"])
        self.assertEqual(result["drops"][0]["taux_par_niveau"], [])


if __name__ == "__main__":
    unittest.main()
