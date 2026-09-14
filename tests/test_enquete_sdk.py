"""Contrôle du vrai schéma discord.py, sans connexion Discord.

Ignoré si le SDK n'est pas installé ; aucune doublure n'est utilisée ici.
"""
import importlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HAS_SDK = importlib.util.find_spec("discord") is not None
if HAS_SDK:
    import discord
    from discord.ext import commands
    ORIGINAL_EVALUATE = discord.utils.evaluate_annotation
    ORIGINAL_INSIDE = discord.utils.is_inside_class


@unittest.skipUnless(HAS_SDK, "discord.py absent : test du SDK réel non exécuté")
class RealSDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_commands_load_and_schema_is_valid(self):
        # Le conftest historique neutralise ces fonctions ; ce contrôle nécessite les vraies.
        with patch.object(discord.utils, "evaluate_annotation", ORIGINAL_EVALUATE), \
             patch.object(discord.utils, "is_inside_class", ORIGINAL_INSIDE):
            module = importlib.import_module("enquete")
            with tempfile.TemporaryDirectory() as tmp, patch.object(module, "BASE_DIR", Path(tmp)):
                async with commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None) as bot:
                    cog = module.EnqueteCog(bot)
                    await bot.add_cog(cog)
                    expected = {"enquete", "enquete-statut", "enquete-annuler", "enquete-purger"}
                    self.assertEqual({c.name for c in cog.get_app_commands()}, expected)
                    schema = bot.tree.get_command("enquete").to_dict(bot.tree)
                    options = {o["name"]: o for o in schema["options"]}
                    self.assertTrue(options["pseudo"]["required"])
                    self.assertTrue(options["pseudo"]["autocomplete"])
                    self.assertEqual(options["contexte"]["min_value"], 0)
                    self.assertEqual(options["contexte"]["max_value"], 10)
                    self.assertEqual({c["value"] for c in options["destination"]["choices"]},
                                     {"staff", "console", "les-deux"})
                    self.assertEqual(int(schema["default_member_permissions"]), discord.Permissions(manage_guild=True).value)
                    self.assertEqual(len(options), 9)
                    from slash_commands import SlashCommandsCog
                    catalogue = SlashCommandsCog(bot)
                    original = {name: bot.tree.get_command(name) for name in expected}
                    catalogue.register_commands()
                    for name in expected:
                        self.assertIs(bot.tree.get_command(name), original[name])
                    catalogue.cog_unload()
                    await bot.remove_cog(cog.qualified_name)
                    self.assertIsNone(bot.tree.get_command("enquete"))


if __name__ == "__main__":
    unittest.main()
