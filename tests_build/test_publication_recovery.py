"""Je réconcilie les publications ambiguës sans envoyer une seconde fiche."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from build import BuildCog
from utils.build.models import BuildError
from utils.build.repository import share_hash


class PublicationChannel:
    def __init__(self, bot):
        self.bot = bot
        self.id = 333
        self.messages = []
        self.sends = 0
        self.failure = None
        self.history_failure = False

    def permissions_for(self, member):
        return SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)

    def append(self, content, *, author=None):
        message = SimpleNamespace(id=1000 + len(self.messages), content=content,
                                  author=self.bot.user if author is None else author)
        self.messages.append(message)
        return message

    async def send(self, *, content, **kwargs):
        self.sends += 1
        if self.failure == "before":
            raise OSError("Connexion perdue avant résultat")
        message = self.append(content)
        if self.failure == "after":
            raise OSError("Accusé perdu après publication")
        return message

    async def history(self, **kwargs):
        if self.history_failure:
            raise OSError("Historique inaccessible")
        for message in self.messages:
            yield message


async def prepare(service, actor, profile):
    saved, _, _ = await service.new(actor, "Partage de recette", profile, operation="create")
    member = SimpleNamespace(id=actor.user_id)
    guild = SimpleNamespace(me=SimpleNamespace(id=999), get_member=lambda _: member)
    bot = SimpleNamespace(user=guild.me, get_guild=lambda _: guild)
    channel = PublicationChannel(bot)
    cog = object.__new__(BuildCog)
    cog.bot, cog.service, cog.repository = bot, service, service.repository
    cog.track = lambda _: None
    cog.untrack = lambda _: None
    cog.send_view = AsyncMock()
    interaction = SimpleNamespace(guild_id=actor.guild_id, channel=channel)
    await cog.confirm_share(interaction, actor, saved.id)
    view = cog.send_view.await_args.kwargs["view"]
    click = SimpleNamespace(edit_original_response=AsyncMock())
    return cog, channel, saved, view, click


@pytest.mark.asyncio
async def test_lost_send_ack_finds_unique_message_and_replay_never_resends(service, actor, profile):
    cog, channel, saved, view, click = await prepare(service, actor, profile)
    channel.failure = "after"
    try:
        await view.callback(click, "publish")
        receipt = await service.share(actor, saved, "publish")
        state = service.repository.shares[share_hash(receipt["code"])]
        assert state["state"] == "sent"
        assert state["message_id"] == channel.messages[0].id
        assert "retrouvée" in click.edit_original_response.await_args.kwargs["content"]
        await view.callback(click, "publish")
        assert channel.sends == 1
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_unknown_send_keeps_reservation_and_retry_reconciles_late_message(service, actor, profile):
    cog, channel, saved, view, click = await prepare(service, actor, profile)
    channel.failure = "before"
    try:
        with pytest.raises(BuildError, match="publication incertaine"):
            await view.callback(click, "publish")
        receipt = await service.share(actor, saved, "publish")
        assert service.repository.shares[share_hash(receipt["code"])]["state"] == "sending"
        channel.failure = None
        with pytest.raises(BuildError, match="toujours incertaine"):
            await view.callback(click, "publish")
        assert channel.sends == 1
        channel.append(f"Build partagé pendant 7 jours. Code : `{receipt['code']}`\nVoir et copier")
        await view.callback(click, "publish")
        assert channel.sends == 1
        assert service.repository.shares[share_hash(receipt["code"])]["state"] == "sent"
    finally:
        view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["foreign_author", "two_matches", "unreadable_history"])
async def test_ambiguous_or_untrusted_history_never_confirms_or_resends(service, actor, profile, case):
    cog, channel, saved, view, click = await prepare(service, actor, profile)
    receipt = await service.share(actor, saved, "publish")
    await service.repository.claim_publication(actor, receipt["code"])
    content = f"Build partagé pendant 7 jours. Code : `{receipt['code']}`\nVoir et copier"
    if case == "foreign_author":
        channel.append(content, author=SimpleNamespace(id=777))
    elif case == "two_matches":
        channel.append(content)
        channel.append(content)
    else:
        channel.history_failure = True
    try:
        with pytest.raises(BuildError, match="toujours incertaine"):
            await view.callback(click, "publish")
        assert channel.sends == 0
        assert service.repository.shares[share_hash(receipt["code"])]["state"] == "sending"
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_legacy_failed_publication_can_be_confirmed_by_exact_message(service, actor, profile):
    cog, channel, saved, view, click = await prepare(service, actor, profile)
    receipt = await service.share(actor, saved, "publish")
    await service.repository.claim_publication(actor, receipt["code"])
    await service.repository.publication(actor, receipt["code"], "failed")
    channel.append(f"Build partagé pendant 7 jours. Code : `{receipt['code']}`\nVoir et copier")
    try:
        await view.callback(click, "publish")
        assert service.repository.shares[share_hash(receipt["code"])]["state"] == "sent"
        assert channel.sends == 0
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_publication_state_failure_reconciles_without_new_send(service, actor, profile):
    cog, channel, saved, view, click = await prepare(service, actor, profile)
    publication = service.repository.publication
    service.repository.publication = AsyncMock(side_effect=BuildError("Sauvegarde incertaine"))
    try:
        with pytest.raises(BuildError, match="Sauvegarde incertaine"):
            await view.callback(click, "publish")
        assert len(channel.messages) == 1
        service.repository.publication = publication
        await view.callback(click, "publish")
        assert channel.sends == 1
        receipt = await service.share(actor, saved, "publish")
        assert service.repository.shares[share_hash(receipt["code"])]["state"] == "sent"
    finally:
        service.repository.publication = publication
        view.stop()
