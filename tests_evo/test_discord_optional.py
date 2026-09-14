"""Vérifie l'enregistrement natif avec discord.py installé ; aucune connexion Discord."""
import importlib.util
import os
import unittest
from unittest.mock import patch

HAS_DISCORD = importlib.util.find_spec("discord") is not None
if HAS_DISCORD:
    import discord
    from discord.ext import commands
    from evo import EvoCog
    from utils.command_policy import ai_service_enabled, unavailable_reason


@unittest.skipUnless(HAS_DISCORD, "discord.py absent de cet environnement ; test à exécuter après installation des dépendances")
class DiscordRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_registration_without_token_or_network(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        try:
            await bot.add_cog(EvoCog(bot))
            names = {cmd.name for cmd in bot.tree.get_commands()}
            self.assertTrue({"evo", "evo-budget", "evo-oublier"} <= names)
            evo = bot.tree.get_command("evo")
            self.assertEqual([p.name for p in evo.parameters], ["question"])
            self.assertTrue(evo.guild_only)
            self.assertFalse(bot.tree.get_command("evo-budget").default_permissions.administrator)
            self.assertTrue(bot.tree.get_command("evo-budget").default_permissions.manage_guild)
            budget = bot.tree.get_command("evo-budget")
            self.assertEqual([p.name for p in budget.parameters], ["initialiser"])
            self.assertFalse(budget.parameters[0].required)
        finally:
            await bot.close()

    async def test_evo_independent_and_legacy_explicitly_disabled(self):
        values = {"EVO_ENABLED": "1", "ENABLE_AI_COMMANDS": "1",
                  "OPENAI_API_KEY": "placeholder", "GEMINI_API_KEY": "placeholder"}
        with patch.dict(os.environ, values, clear=True):
            self.assertIsNone(unavailable_reason("evo"))
            self.assertFalse(ai_service_enabled("openai"))
            self.assertFalse(ai_service_enabled("gemini"))
        with patch.dict(os.environ, {**values, "EVO_ALLOW_LEGACY_AI": "1"}, clear=True):
            self.assertTrue(ai_service_enabled("openai"))


if __name__ == "__main__":
    unittest.main()
