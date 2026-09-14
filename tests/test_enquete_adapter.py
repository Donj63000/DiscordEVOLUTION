"""Tests de cycle de vie avec une doublure minimale du SDK.

Ils exécutent le cog, mais ne valident PAS les décorateurs/contrats de discord.py.
Le vrai schéma SDK est vérifié séparément dans test_enquete_sdk.py.
"""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from utils.enquete_core import Config, EnqueteError, Receipts, ReportMeta, Window
from test_enquete_service import Guild, DEST, TARGET

ROOT = Path(__file__).resolve().parents[1]


def load_stubbed_cog():
    discord = ModuleType("discord")
    app = ModuleType("discord.app_commands")
    ext = ModuleType("discord.ext")
    commands = ModuleType("discord.ext.commands")

    def identity(*args, **kwargs):
        return lambda function: function

    def command(**kwargs):
        def decorate(function):
            function.autocomplete = identity
            return function
        return decorate

    app.command = command
    for name in ["guild_only", "allowed_installs", "default_permissions", "describe"]:
        setattr(app, name, identity)
    app.Choice = lambda **kw: NS(**kw)
    discord.AllowedMentions = NS(none=lambda: NS(parse=[]))
    discord.ReactionType = NS(normal=0, burst=1)
    discord.Member = type("Member", (), {})
    discord.HTTPException = type("HTTPException", (Exception,), {})
    discord.NotFound = type("NotFound", (discord.HTTPException,), {})
    commands.Cog = type("Cog", (), {})
    commands.Bot = type("Bot", (), {})
    discord.app_commands = app
    discord.ext = ext
    ext.commands = commands

    class File:
        def __init__(self, path, filename=None):
            self.path, self.filename = path, filename
            self.closed = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.closed = True
    discord.File = File
    fake_modules = {"discord": discord, "discord.app_commands": app, "discord.ext": ext,
                    "discord.ext.commands": commands}
    name = "_enquete_test_adapter"
    spec = importlib.util.spec_from_file_location(name, ROOT/"enquete.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {**fake_modules, name: module}):
        spec.loader.exec_module(module)
    return module


MOD = load_stubbed_cog()
JID, GID, UID, CID = "012345abcdef", 100, 200, 300


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = NS(user=NS(id=999), get_channel=Mock(), fetch_channel=AsyncMock())
        self.cog = MOD.EnqueteCog(self.bot)
        self.cog.temporary = Path(self.temp.name)/"tmp"
        self.cog.temporary.mkdir()
        self.cog.receipts = Receipts(Path(self.temp.name)/"publications.sqlite3")
        self.cog.receipts.create(JID, GID, UID, 1000, 7)
        self.job = MOD.Job(JID, GID, UID)
        self.cog.jobs[JID] = self.job
        self.cog.busy = True

    async def asyncTearDown(self):
        for job in list(self.cog.jobs.values()):
            if job.task and not job.task.done():
                await self.cog._cancel(job)
        self.cog.receipts.close()
        self.temp.cleanup()

    def publication(self, mid=1, *, guild_id=GID, author=999, text=None):
        message = NS(id=mid, author=NS(id=author), content=text or f"[ENQUETE:{JID}] rapport",
                     delete=AsyncMock(), edit=AsyncMock())
        channel = NS(id=CID, guild=NS(id=guild_id), fetch_message=AsyncMock(return_value=message),
                     send=AsyncMock(return_value=message))
        self.bot.get_channel.return_value = channel
        return channel, message

    async def test_send_is_silent_no_mentions_and_receipted(self):
        channel, message = self.publication()
        await self.cog._send(self.job, channel, "safe")
        kw = channel.send.await_args.kwargs
        self.assertTrue(kw["silent"])
        self.assertTrue(kw["suppress_embeds"])
        self.assertIs(kw["allowed_mentions"], MOD.NO_MENTIONS)
        self.assertEqual(len(self.cog.receipts.deliveries(JID)), 1)

    async def test_receipt_failure_removes_untracked_publication(self):
        channel, message = self.publication()
        with patch.object(self.cog.receipts, "sent", side_effect=OSError()):
            with self.assertRaises(OSError):
                await self.cog._send(self.job, channel, "safe")
        message.delete.assert_awaited_once()

    async def test_purge_removes_only_owned_job_messages(self):
        channel, message = self.publication()
        self.cog.receipts.sent(JID, CID, message.id)
        self.assertEqual(await self.cog._purge(JID, GID), 0)
        message.delete.assert_awaited_once()
        self.assertEqual(self.cog.receipts.deliveries(JID), [])

    async def test_purge_refuses_foreign_author_marker_and_guild(self):
        for fields in [dict(author=4), dict(text="unrelated"), dict(guild_id=666)]:
            channel, message = self.publication(**fields)
            self.cog.receipts.sent(JID, CID, message.id)
            self.assertEqual(await self.cog._purge(JID, GID), 1)
            message.delete.assert_not_awaited()
            self.cog.receipts.forget_delivery(message.id)

    async def test_purge_already_missing_is_idempotent(self):
        channel, message = self.publication()
        self.cog.receipts.sent(JID, CID, 1)
        channel.fetch_message.side_effect = MOD.discord.NotFound()
        self.assertEqual(await self.cog._purge(JID, GID), 0)
        self.assertEqual(await self.cog._purge(JID, GID), 0)

    async def test_purge_failure_keeps_receipt_for_retry(self):
        channel, message = self.publication()
        self.cog.receipts.sent(JID, CID, 1)
        message.delete.side_effect = MOD.discord.HTTPException()
        self.assertEqual(await self.cog._purge(JID, GID), 1)
        self.assertEqual(len(self.cog.receipts.deliveries(JID)), 1)

    async def test_private_response_never_uses_dm_or_public_fallback(self):
        interaction = NS(is_expired=lambda: True, response=NS(send_message=AsyncMock(), is_done=lambda: False),
                         edit_original_response=AsyncMock(), user=NS(send=AsyncMock()))
        await self.cog._private(interaction, "secret")
        interaction.response.send_message.assert_not_awaited()
        interaction.user.send.assert_not_awaited()
        interaction.is_expired = lambda: False
        await self.cog._private(interaction, "safe")
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        interaction.response.is_done = lambda: True
        await self.cog._private(interaction, "next")
        interaction.edit_original_response.assert_awaited_once()

    async def test_finish_always_unlocks_even_receipt_write_failure(self):
        with patch.object(self.cog.receipts, "update", side_effect=OSError()), self.assertLogs(MOD.log, level="ERROR"):
            self.cog._finish(self.job, "terminé")
        self.assertFalse(self.cog.busy)
        self.assertNotIn(JID, self.cog.jobs)

    async def test_cancel_before_task_start_still_purges_and_unlocks(self):
        channel, message = self.publication()
        self.cog.receipts.sent(JID, CID, 1)
        self.job.task = asyncio.create_task(asyncio.sleep(100))
        await self.cog._cancel(self.job)
        message.delete.assert_awaited_once()
        self.assertNotIn(JID, self.cog.jobs)
        self.assertFalse(self.cog.busy)
        self.assertIn("annulé", self.cog.receipts.get(JID, GID)["status"])

    async def test_receipts_are_guild_scoped_and_owner_checked(self):
        interaction = NS(guild_id=GID, user=NS(id=UID+1, guild_permissions=NS(administrator=False)))
        with self.assertRaises(EnqueteError):
            self.cog._receipt(interaction, JID, owner=True)
        interaction.user.guild_permissions.administrator = True
        self.assertIsNotNone(self.cog._receipt(interaction, JID, owner=True))
        interaction.guild_id += 1
        with self.assertRaises(EnqueteError):
            self.cog._receipt(interaction, JID)
        with self.assertRaises(EnqueteError):
            self.cog._receipt(interaction, "../escape")

    def execution_fakes(self, *, collect=None, revalidate=None):
        channel, message = self.publication()
        scope = NS(guild=NS(filesize_limit=8*1024**2), destinations=[channel], destination_ids=[CID],
                   revalidate=AsyncMock(side_effect=revalidate))
        collector = NS(collect=AsyncMock(side_effect=collect), sources={}, visited_messages=7)

        async def export(work, meta, cfg, *, file_limit):
            path = work.path.parent/"enquete_test.txt"
            path.write_text("rapport synthétique")
            return [path]
        return scope, collector, export, channel

    def launch_interaction(self):
        guild = Guild()
        requester = MOD.discord.Member()
        requester.__dict__.update(vars(guild.members[1]))
        guild.members[1] = requester
        interaction = NS(
            guild=guild, guild_id=guild.id, channel_id=DEST, user=requester,
            is_expired=lambda: False,
            response=NS(defer=AsyncMock(), is_done=lambda: True, send_message=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        self.cog.jobs.clear()
        self.cog.busy = False
        self.bot.intents = NS(members=True, message_content=True)
        channel = guild.channels[0]
        channel.send = AsyncMock(return_value=NS(id=10, edit=AsyncMock(), delete=AsyncMock()))
        return interaction, channel

    async def test_default_launch_sends_progress_and_report_to_current_channel_without_env(self):
        interaction, channel = self.launch_interaction()
        collector = NS(collect=AsyncMock(), sources={}, visited_messages=7)

        async def export(work, meta, cfg, *, file_limit):
            path = work.path.parent / "rapport.txt"
            path.write_text("Rapport de test", encoding="utf-8")
            return [path]

        with patch.dict(os.environ, {}, clear=True), \
             patch.object(MOD, "Collector", return_value=collector), \
             patch.object(MOD, "build_report", side_effect=export):
            await self.cog.enquete(interaction, str(TARGET))
            self.assertEqual(len(self.cog.jobs), 1)
            job = next(iter(self.cog.jobs.values()))
            await job.task
        self.assertEqual(channel.send.await_count, 2)
        self.assertIsNone(channel.send.await_args_list[0].kwargs["file"])
        self.assertIsNotNone(channel.send.await_args_list[1].kwargs["file"])
        confirmation = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn(f"<#{DEST}>", confirmation)
        self.assertIn("publié", self.cog.receipts.get(job.id, interaction.guild_id)["status"])
        self.assertEqual({r["cid"] for r in self.cog.receipts.deliveries(job.id)}, {DEST})

    async def test_current_public_channel_returns_private_error_without_starting(self):
        interaction, channel = self.launch_interaction()
        channel.visible.add(interaction.guild.id)
        with patch.dict(os.environ, {}, clear=True), patch.object(self.cog, "_run") as run:
            await self.cog.enquete(interaction, str(TARGET))
        run.assert_not_called()
        channel.send.assert_not_awaited()
        self.assertFalse(self.cog.busy)
        self.assertEqual(self.cog.jobs, {})
        self.assertIn("@everyone", interaction.edit_original_response.await_args.kwargs["content"])
        self.assertTrue(interaction.response.defer.await_args.kwargs["ephemeral"])

    async def test_direct_message_launch_is_rejected(self):
        interaction, channel = self.launch_interaction()
        interaction.guild = None
        with patch.dict(os.environ, {}, clear=True), patch.object(self.cog, "_run") as run:
            await self.cog.enquete(interaction, str(TARGET))
        run.assert_not_called()
        channel.send.assert_not_awaited()
        interaction.response.defer.assert_not_awaited()
        self.assertIn("uniquement dans le serveur",
                      interaction.edit_original_response.await_args.kwargs["content"])

    async def test_success_cleans_temporary_data_and_revalidates(self):
        scope, collector, export, channel = self.execution_fakes()
        with patch.object(MOD, "Collector", return_value=collector), patch.object(MOD, "build_report", side_effect=export):
            await self.cog._run(self.job, scope, None, Config())
        self.assertFalse(self.cog.busy)
        self.assertEqual(list(self.cog.temporary.iterdir()), [])
        self.assertIn("publié", self.cog.receipts.get(JID, GID)["status"])
        self.assertEqual(scope.revalidate.await_count, 2)
        channel.send.assert_awaited_once()

    async def test_changed_permissions_stop_all_content_publication(self):
        scope, collector, export, channel = self.execution_fakes(revalidate=EnqueteError("droits"))
        with patch.object(MOD, "Collector", return_value=collector), patch.object(MOD, "build_report", side_effect=export):
            with self.assertLogs(MOD.log, level="ERROR"):
                await self.cog._run(self.job, scope, None, Config())
        channel.send.assert_not_awaited()
        self.assertEqual(list(self.cog.temporary.iterdir()), [])
        self.assertFalse(self.cog.busy)
        self.assertIn("échec", self.cog.receipts.get(JID, GID)["status"])

    async def test_running_cancel_closes_sqlite_removes_files_and_unlocks(self):
        entered = asyncio.Event()
        async def collect():
            entered.set()
            await asyncio.sleep(100)
        scope, collector, export, channel = self.execution_fakes(collect=collect)
        with patch.object(MOD, "Collector", return_value=collector), patch.object(MOD, "build_report", side_effect=export):
            self.job.task = asyncio.create_task(self.cog._run(self.job, scope, None, Config()))
            await entered.wait()
            await self.cog._cancel(self.job)
        self.assertEqual(list(self.cog.temporary.iterdir()), [])
        self.assertFalse(self.cog.busy)
        self.assertIn("annulé", self.cog.receipts.get(JID, GID)["status"])


if __name__ == "__main__":
    unittest.main()
