"""Parcours Discord d'Evo simulés, avec les commandes natives et sans connexion réseau."""
import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from evo import EvoCog, NO_MENTIONS
from tests_evo.helpers import config, context
from utils.evo_agent import Sessions


class DiscordWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        enabled = patch.dict(os.environ, {"EVO_ENABLED": "1"})
        enabled.start()
        self.addCleanup(enabled.stop)
        self.ctx = context(config(cooldown=0))
        self.permissions = self.ctx.channel
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = self.ctx.channel.id
        self.channel.guild = self.ctx.guild
        self.channel.permissions_for.side_effect = self.permissions.permissions_for
        self.ctx.channel = self.channel
        self.ctx.guild.text_channels[0] = self.channel
        self.sessions = Sessions(self.ctx.config)
        self.agent = SimpleNamespace(
            sessions=self.sessions,
            answer=AsyncMock(side_effect=self.answer),
        )
        self.cog = EvoCog(self.ctx.bot)
        self.cog.config = self.ctx.config
        self.cog.agent = self.agent

    async def answer(self, ctx, question, trigger_id, *, private=False):
        rendered = "Voici les activités disponibles."
        key = (ctx.guild.id, ctx.channel.id, ctx.member.id, private)
        self.sessions.save(key, question, rendered, [], ctx.sources)
        return rendered

    def interaction(self, identifier, message_id):
        return SimpleNamespace(
            id=identifier,
            guild=self.ctx.guild,
            channel=self.channel,
            user=self.ctx.member,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock(
                return_value=SimpleNamespace(id=message_id),
            ),
        )

    def message(self, identifier, reference_id, *, member=None):
        return SimpleNamespace(
            id=identifier,
            guild=self.ctx.guild,
            channel=self.channel,
            author=member or self.ctx.member,
            content="Quelles autres activités sont disponibles ?",
            webhook_id=None,
            reference=(
                SimpleNamespace(message_id=reference_id) if reference_id is not None else None
            ),
            reply=AsyncMock(return_value=SimpleNamespace(id=identifier + 1000)),
        )

    async def test_followup_requires_the_members_latest_explicit_reply(self):
        interaction = self.interaction(100, 200)
        await self.cog.evo.callback(self.cog, interaction, "Quelles activités sont prévues ?")
        key = (self.ctx.guild.id, self.channel.id, self.ctx.member.id, False)
        self.assertEqual(self.sessions.items[key].last_message_id, 200)

        unrelated = [
            self.message(101, None),
            self.message(102, 199),
            self.message(103, 200, member=self.ctx.guild.get_member(3)),
        ]
        for message in unrelated:
            await self.cog.on_message(message)
            message.reply.assert_not_awaited()
        self.assertEqual(self.agent.answer.await_count, 1)

        followup = self.message(104, 200)
        await self.cog.on_message(followup)
        self.assertEqual(self.agent.answer.await_count, 2)
        self.assertEqual(self.sessions.items[key].last_message_id, 1104)
        followup.reply.assert_awaited_once_with(
            "Voici les activités disponibles.",
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
            suppress_embeds=True,
        )

        stale_reply = self.message(105, 200)
        await self.cog.on_message(stale_reply)
        stale_reply.reply.assert_not_awaited()
        self.assertEqual(self.agent.answer.await_count, 2)

    async def test_private_reply_cannot_start_a_public_followup(self):
        private = self.interaction(300, 400)
        public = self.interaction(301, 401)
        await self.cog.evo.callback(self.cog, private, "Ma session de forgemagie", True)
        await self.cog.evo.callback(self.cog, public, "Quelles activités sont prévues ?", False)
        private.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        public.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        private_call, public_call = self.agent.answer.await_args_list
        self.assertTrue(private_call.kwargs["private"])
        self.assertTrue(private_call.args[0].allow_private_fm)
        self.assertFalse(public_call.kwargs["private"])
        self.assertFalse(public_call.args[0].allow_private_fm)

        private_reference = self.message(302, 400)
        await self.cog.on_message(private_reference)
        private_reference.reply.assert_not_awaited()
        self.assertEqual(self.agent.answer.await_count, 2)

        public_reference = self.message(303, 401)
        await self.cog.on_message(public_reference)
        public_reference.reply.assert_awaited_once()
        self.assertEqual(self.agent.answer.await_count, 3)
        self.assertFalse(self.agent.answer.await_args.kwargs["private"])

    async def test_permissions_revoked_during_generation_prevent_answer_publication(self):
        async def revoke_permissions(ctx, question, trigger_id, *, private=False):
            self.permissions.denied.add(ctx.member.id)
            return "CONTENU_QUI_NE_DOIT_PAS_ETRE_PUBLIE"

        self.agent.answer.side_effect = revoke_permissions
        interaction = self.interaction(500, 600)
        await self.cog.evo.callback(self.cog, interaction, "Quelles activités sont prévues ?")

        self.agent.answer.assert_awaited_once()
        interaction.edit_original_response.assert_awaited_once()
        content = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("Permission", content)
        self.assertNotIn("CONTENU_QUI_NE_DOIT_PAS_ETRE_PUBLIE", content)
        self.assertFalse(self.cog._active)

    async def test_forget_cancels_pending_answer_before_erasing_both_memories(self):
        await self.cog.evo.callback(
            self.cog, self.interaction(700, 800), "Quelles activités sont prévues ?", False,
        )
        await self.cog.evo.callback(
            self.cog, self.interaction(701, 801), "Ma session de forgemagie", True,
        )
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending_answer(ctx, question, trigger_id, *, private=False):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.agent.answer.side_effect = pending_answer
        pending_interaction = self.interaction(702, 802)
        pending = asyncio.create_task(
            self.cog.evo.callback(self.cog, pending_interaction, "Et la prochaine sortie ?"),
        )
        try:
            await asyncio.wait_for(entered.wait(), 3)
            forgotten = self.interaction(703, 803)
            await self.cog.forget.callback(self.cog, forgotten)

            self.assertTrue(cancelled.is_set())
            self.assertTrue(pending.cancelled())
            self.assertFalse(self.cog._active)
            self.assertFalse(self.sessions.items)
            pending_interaction.edit_original_response.assert_not_awaited()
            forgotten.response.defer.assert_awaited_once_with(ephemeral=True)
            forgotten.edit_original_response.assert_awaited_once()
            self.assertIn(
                "oublié", forgotten.edit_original_response.await_args.kwargs["content"],
            )
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
