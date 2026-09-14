"""Configuration Evo sans base externe et résolution de la console existante."""
import os
import unittest
from unittest.mock import patch

from tests_evo.helpers import Guild
from utils.evo_config import EvoConfig, EvoError, resolve_console_channel


class ConfigTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-placeholder"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.guild = Guild()

    def test_single_guild_and_public_channels_are_automatic_on_render(self):
        with patch.dict(os.environ, {"RENDER": "true"}):
            settings = EvoConfig.from_env(guilds=[self.guild])
        self.assertEqual(settings.guild_id, self.guild.id)
        self.assertEqual(settings.channel_ids, frozenset())
        self.assertEqual(settings.model, "gpt-5.6-luna")

    def test_multiple_guilds_require_an_explicit_choice(self):
        guilds = [self.guild, Guild(9)]
        with self.assertRaisesRegex(EvoError, "EVO_GUILD_ID"):
            EvoConfig.from_env(guilds=guilds)
        with patch.dict(os.environ, {"EVO_GUILD_ID": "9"}):
            self.assertEqual(EvoConfig.from_env(guilds=guilds).guild_id, 9)

    def test_unknown_configured_guild_is_rejected(self):
        with patch.dict(os.environ, {"EVO_GUILD_ID": "9"}):
            with self.assertRaisesRegex(EvoError, "accessible"):
                EvoConfig.from_env(guilds=[self.guild])

    def test_optional_allowlist_and_history_restrictions(self):
        with patch.dict(os.environ, {"EVO_HISTORY_CHANNEL_IDS": "30"}):
            settings = EvoConfig.from_env(guilds=[self.guild])
            self.assertEqual(settings.history_channels, frozenset({30}))
            with patch.dict(os.environ, {"EVO_CHANNEL_IDS": "10"}):
                with self.assertRaisesRegex(EvoError, "inclus"):
                    EvoConfig.from_env(guilds=[self.guild])

    def test_console_name_alias_and_id_precedence(self):
        self.guild.get_channel(20).name = "archives"
        with patch.dict(os.environ, {"CONSOLE_CHANNEL_NAME": "archives"}):
            self.assertEqual(resolve_console_channel(self.guild).id, 20)
            with patch.dict(os.environ, {"CHANNEL_CONSOLE": "sorties"}):
                self.assertEqual(resolve_console_channel(self.guild).id, 30)
                with patch.dict(os.environ, {"CHANNEL_CONSOLE_ID": "10"}):
                    self.assertEqual(resolve_console_channel(self.guild).id, 10)

    def test_console_resolution_never_uses_another_guild(self):
        with patch.dict(os.environ, {"CHANNEL_CONSOLE_ID": "999"}):
            self.assertIsNone(resolve_console_channel(self.guild))
        self.guild.get_channel(20).name = "console"
        self.assertEqual(resolve_console_channel(self.guild).id, 20)


if __name__ == "__main__":
    unittest.main()
