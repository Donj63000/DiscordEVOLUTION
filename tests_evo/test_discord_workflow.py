"""Parcours Discord d'Evo simulés, avec les commandes natives et sans connexion réseau."""
import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import discord

from evo import EvoCog, NO_MENTIONS
from tests_evo.helpers import config, context
from utils.evo_agent import Sessions
from utils.evo_config import EvoError


class DiscordWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        enabled = patch.dict(os.environ, {"EVO_ENABLED": "1"}, clear=True)
        enabled.start()
        self.addCleanup(enabled.stop)
        self.ctx = context(config(cooldown=0))
        self.permissions = self.ctx.channel
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = self.ctx.channel.id
        self.channel.name = self.ctx.channel.name
        self.channel.guild = self.ctx.guild
        self.channel.permissions_for.side_effect = self.permissions.permissions_for
        self.ctx.channel = self.channel
        self.ctx.guild.text_channels[0] = self.channel
        self.ctx.bot.user = self.ctx.guild.me
        self.ctx.bot.ensure_evo_leadership = AsyncMock()
        self.sessions = Sessions(self.ctx.config)
        self.agent = SimpleNamespace(
            sessions=self.sessions,
            answer=AsyncMock(side_effect=self.answer),
            model=SimpleNamespace(close=AsyncMock()),
        )
        self.budget = SimpleNamespace(
            open=AsyncMock(), check_ready=AsyncMock(), invalidate=Mock(), close=AsyncMock(),
        )
        self.cog = EvoCog(self.ctx.bot)
        self.cog.config = self.ctx.config
        self.cog.agent = self.agent
        self.cog.budget = self.budget

    async def answer(self, ctx, question, trigger_id):
        rendered = "Voici les activités disponibles."
        key = (ctx.guild.id, ctx.channel.id, ctx.member.id)
        self.sessions.save(key, question, rendered, [], ctx.sources)
        return rendered

    def interaction(self, identifier, message_id):
        return SimpleNamespace(
            id=identifier,
            guild=self.ctx.guild,
            guild_id=self.ctx.guild.id,
            channel=self.channel,
            user=self.ctx.member,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock(
                return_value=SimpleNamespace(id=message_id),
            ),
        )

    def message(self, identifier, reference_id=None, *, member=None, content=None):
        return SimpleNamespace(
            id=identifier,
            guild=self.ctx.guild,
            channel=self.channel,
            author=member or self.ctx.member,
            content=content if content is not None else "Quelles autres activités sont disponibles ?",
            webhook_id=None,
            reference=(
                SimpleNamespace(message_id=reference_id) if reference_id is not None else None
            ),
            reply=AsyncMock(return_value=SimpleNamespace(id=identifier + 1000)),
        )

    async def test_followup_requires_the_members_latest_explicit_reply(self):
        interaction = self.interaction(100, 200)
        await self.cog.evo.callback(self.cog, interaction, "Quelles activités sont prévues ?")
        key = (self.ctx.guild.id, self.channel.id, self.ctx.member.id)
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

    async def test_slash_answer_and_configuration_error_are_public(self):
        public = self.interaction(300, 400)
        await self.cog.evo.callback(self.cog, public, "Quelles activités sont prévues ?")
        public.response.defer.assert_awaited_once_with(thinking=True)
        public.edit_original_response.assert_awaited_once()
        self.assertFalse(self.agent.answer.await_args.kwargs)

        self.permissions.denied.add(self.ctx.member.id)
        refused = self.interaction(301, 401)
        await self.cog.evo.callback(self.cog, refused, "Quelles activités sont prévues ?")
        refused.response.send_message.assert_awaited_once()
        self.assertNotIn("ephemeral", refused.response.send_message.await_args.kwargs)
        self.assertEqual(self.agent.answer.await_count, 1)

    async def test_direct_mention_initializes_agent_on_first_message(self):
        self.cog.agent = None
        self.cog.budget = None
        message = self.message(410, content="<@99> Quelles activités sont prévues ?")
        with (
            patch("evo.ConsoleBudgetStore") as store,
            patch("evo.Budget", return_value=self.budget) as budget,
            patch("evo.MeteredModel") as model,
            patch("evo.EvoAgent", return_value=self.agent) as agent,
        ):
            await self.cog.on_message(message)

        store.assert_called_once_with(self.ctx.bot, self.ctx.guild.id)
        budget.assert_called_once_with(self.ctx.config, store.return_value)
        self.budget.open.assert_awaited_once()
        model.assert_called_once_with(self.ctx.config, self.budget)
        agent.assert_called_once_with(self.ctx.config, model.return_value)
        self.agent.answer.assert_awaited_once()
        self.assertEqual(self.agent.answer.await_args.args[1], "Quelles activités sont prévues ?")
        message.reply.assert_awaited_once()

    async def test_staff_slash_response_is_visible_in_the_current_channel(self):
        self.permissions.public = False
        self.channel.name = "staff"
        self.ctx.member.guild_permissions.manage_guild = True
        interaction = self.interaction(411, 1411)

        await self.cog.evo.callback(self.cog, interaction, "Quelles activités sont prévues ?")

        self.agent.answer.assert_awaited_once()
        self.assertIs(self.agent.answer.await_args.args[0].channel, self.channel)
        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.response.send_message.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once()
        self.assertEqual(
            interaction.edit_original_response.await_args.kwargs["content"],
            "Voici les activités disponibles.",
        )
        self.assertNotIn("ephemeral", interaction.edit_original_response.await_args.kwargs)

    async def test_staff_first_mention_initializes_agent_and_accepts_own_followup(self):
        self.permissions.public = False
        self.channel.name = "staff"
        self.ctx.member.guild_permissions.manage_guild = True
        self.cog.agent = None
        self.cog.budget = None
        message = self.message(412, content="<@99> Quelles activités sont prévues ?")
        with (
            patch("evo.ConsoleBudgetStore"),
            patch("evo.Budget", return_value=self.budget),
            patch("evo.MeteredModel"),
            patch("evo.EvoAgent", return_value=self.agent),
        ):
            await self.cog.on_message(message)

        self.budget.open.assert_awaited_once()
        self.agent.answer.assert_awaited_once()
        message.reply.assert_awaited_once_with(
            "Voici les activités disponibles.", mention_author=False,
            allowed_mentions=NO_MENTIONS, suppress_embeds=True,
        )

        other_member = self.message(413, 1412, member=self.ctx.guild.get_member(3))
        await self.cog.on_message(other_member)
        other_member.reply.assert_not_awaited()

        followup = self.message(414, 1412, content="Et demain ?")
        await self.cog.on_message(followup)
        self.assertEqual(self.agent.answer.await_count, 2)
        self.assertEqual(self.agent.answer.await_args.args[1], "Et demain ?")
        followup.reply.assert_awaited_once_with(
            "Voici les activités disponibles.", mention_author=False,
            allowed_mentions=NO_MENTIONS, suppress_embeds=True,
        )
        key = (self.ctx.guild.id, self.channel.id, self.ctx.member.id)
        self.assertEqual(self.sessions.items[key].last_message_id, 1414)

    async def test_bare_mention_invites_without_budget_or_model_and_allows_reply(self):
        self.cog.agent = None
        self.cog.budget = None
        message = self.message(420, content="<@!99>")
        with patch("evo.Budget") as budget, patch("evo.EvoAgent") as agent:
            await self.cog.on_message(message)
        budget.assert_not_called()
        agent.assert_not_called()
        self.agent.answer.assert_not_awaited()
        self.assertIn("Pose-moi ta question", message.reply.await_args.args[0])

        self.cog.agent = self.agent
        self.cog.budget = self.budget
        followup = self.message(421, 1420)
        await self.cog.on_message(followup)
        self.agent.answer.assert_awaited_once()
        followup.reply.assert_awaited_once()

    async def test_message_mentioning_and_replying_is_processed_once(self):
        first = self.interaction(430, 1430)
        await self.cog.evo.callback(self.cog, first, "Quelles activités sont prévues ?")
        followup = self.message(431, 1430, content="<@!99> Et la suivante ?")
        await self.cog.on_message(followup)
        await self.cog.on_message(followup)
        self.assertEqual(self.agent.answer.await_count, 2)
        self.assertEqual(self.agent.answer.await_args.args[1], "Et la suivante ?")
        followup.reply.assert_awaited_once()

    async def test_ambient_role_bot_webhook_and_direct_messages_do_not_trigger(self):
        messages = [
            self.message(440, content="Evo, quelles activités ?"),
            self.message(441, content="<@&99> quelles activités ?"),
            self.message(442, content="@everyone quelles activités ?"),
            self.message(443, member=self.ctx.guild.me, content="<@99> activités ?"),
            self.message(444, content="<@99> activités ?"),
            self.message(445, content="<@99> activités ?"),
        ]
        messages[-2].webhook_id = 123
        messages[-1].guild = None
        for message in messages:
            await self.cog.on_message(message)
            message.reply.assert_not_awaited()
        self.agent.answer.assert_not_awaited()
        self.ctx.bot.ensure_evo_leadership.assert_not_awaited()

    async def test_mentions_are_ignored_in_inaccessible_channels_and_console(self):
        self.permissions.public = False
        self.permissions.denied.add(self.ctx.member.id)
        inaccessible = self.message(450, content="<@99> activités ?")
        await self.cog.on_message(inaccessible)
        self.permissions.denied.clear()
        self.channel.name = "console"
        console = self.message(451, content="<@99> activités ?")
        await self.cog.on_message(console)
        inaccessible.reply.assert_not_awaited()
        console.reply.assert_not_awaited()
        self.agent.answer.assert_not_awaited()

    async def test_mentions_and_slash_share_member_cooldown(self):
        self.cog.config = config(cooldown=12)
        first = self.interaction(460, 1460)
        await self.cog.evo.callback(self.cog, first, "Quelles activités sont prévues ?")
        message = self.message(461, content="<@99> Et demain ?")
        await self.cog.on_message(message)
        self.assertEqual(self.agent.answer.await_count, 1)
        self.assertIn("quelques secondes", message.reply.await_args.args[0])

    async def test_existing_agent_requires_current_leadership_and_ready_budget(self):
        await self.cog._ready()
        await self.cog._ready()
        self.assertEqual(self.ctx.bot.ensure_evo_leadership.await_count, 2)
        self.assertEqual(self.budget.check_ready.await_count, 2)
        self.ctx.bot.ensure_evo_leadership.side_effect = EvoError("Evo se réinitialise.")
        interaction = self.interaction(470, 1470)
        await self.cog.evo.callback(self.cog, interaction, "Quelles activités sont prévues ?")
        self.agent.answer.assert_not_awaited()
        self.assertIn("réinitialise", interaction.edit_original_response.await_args.kwargs["content"])

    async def test_budget_initialization_is_staff_only(self):
        interaction = self.interaction(480, 1480)
        with patch("evo.Budget") as budget:
            await self.cog.budget_status.callback(self.cog, interaction, True)
        budget.assert_not_called()
        self.ctx.bot.ensure_evo_leadership.assert_not_awaited()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_staff_initializes_console_budget_without_generation(self):
        self.ctx.member.guild_permissions.manage_guild = True
        self.cog.agent = None
        self.cog.budget = None
        interaction = self.interaction(490, 1490)
        initialized = SimpleNamespace(initialize=AsyncMock(), close=AsyncMock())
        with patch("evo.Budget", return_value=initialized), patch("evo.ConsoleBudgetStore"):
            await self.cog.budget_status.callback(self.cog, interaction, True)
        initialized.initialize.assert_awaited_once()
        initialized.close.assert_not_awaited()
        self.assertIs(self.cog.budget, initialized)
        self.agent.answer.assert_not_awaited()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertIn("initialisé dans #console", interaction.edit_original_response.await_args.kwargs["content"])

    async def test_initialization_error_preserves_admin_ephemeral_response(self):
        self.ctx.member.guild_permissions.manage_guild = True
        interaction = self.interaction(491, 1491)
        initialized = SimpleNamespace(
            initialize=AsyncMock(side_effect=EvoError("Le compteur existe déjà.")),
            close=AsyncMock(),
        )
        self.cog.budget = initialized
        with patch("evo.Budget") as constructor:
            await self.cog.budget_status.callback(self.cog, interaction, True)
        constructor.assert_not_called()
        initialized.close.assert_not_awaited()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertEqual(
            interaction.edit_original_response.await_args.kwargs["content"],
            "Le compteur existe déjà.",
        )

    async def test_permissions_revoked_during_generation_prevent_answer_publication(self):
        async def revoke_permissions(ctx, question, trigger_id):
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

    async def test_forget_cancels_pending_answer_and_removes_followup_reference(self):
        await self.cog.evo.callback(
            self.cog, self.interaction(700, 800), "Quelles activités sont prévues ?",
        )
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending_answer(ctx, question, trigger_id):
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
            self.assertFalse(self.cog._last_messages)
            pending_interaction.edit_original_response.assert_not_awaited()
            forgotten.response.defer.assert_awaited_once_with(ephemeral=True)
            forgotten.edit_original_response.assert_awaited_once()
            self.assertIn(
                "oublié", forgotten.edit_original_response.await_args.kwargs["content"],
            )
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_leadership_suspension_cancels_generation_and_preserves_budget_guard(self):
        entered = asyncio.Event()

        async def pending_answer(ctx, question, trigger_id):
            entered.set()
            await asyncio.Event().wait()

        self.agent.answer.side_effect = pending_answer
        interaction = self.interaction(810, 1810)
        pending = asyncio.create_task(
            self.cog.evo.callback(self.cog, interaction, "Quelles activités sont prévues ?"),
        )
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await self.cog.suspend()
            self.assertTrue(pending.cancelled())
            self.assertIsNone(self.cog.agent)
            self.assertIs(self.cog.budget, self.budget)
            self.budget.invalidate.assert_called_once()
            self.budget.close.assert_not_awaited()
            self.agent.model.close.assert_awaited_once()
            interaction.edit_original_response.assert_not_awaited()
            self.assertFalse(self.cog._active)
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_suspension_from_leadership_check_does_not_cancel_its_own_request(self):
        async def lose_leadership():
            await self.cog.suspend()
            raise EvoError("Evo attend le prochain leader.")

        self.ctx.bot.ensure_evo_leadership.side_effect = lose_leadership
        interaction = self.interaction(820, 1820)
        await asyncio.wait_for(
            self.cog.evo.callback(self.cog, interaction, "Quelles activités sont prévues ?"), 3,
        )
        self.agent.answer.assert_not_awaited()
        self.budget.invalidate.assert_called_once()
        self.assertIn("prochain leader", interaction.edit_original_response.await_args.kwargs["content"])
        self.assertFalse(self.cog._active)


if __name__ == "__main__":
    unittest.main()
