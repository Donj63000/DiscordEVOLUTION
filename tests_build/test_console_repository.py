"""Je simule Discord pour éprouver engagement, reprise et isolation sans réseau."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import discord
import pytest
import pytest_asyncio

from utils.build.console_repository import (
    ConsoleRepository, MARKER, PART_FILENAME, ROOT_FILENAME,
)
from utils.build.models import Actor, BuildError, Conflict, NotFound, canonical, digest, revised
from utils.build.repository import MemoryRepository, share_hash


def missing():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Absent")


class Attachment:
    next_id = 50000

    def __init__(self, filename, data):
        type(self).next_id += 1
        self.id = self.next_id
        self.filename = filename
        self.data = data
        self.size = len(data)

    async def read(self):
        return self.data


def attachment(file):
    file.fp.seek(0)
    return Attachment(file.filename, file.fp.read())


class Message:
    def __init__(self, channel, identifier, content, attachments=()):
        self.channel = channel
        self.id = identifier
        self.author = channel.bot.user
        self.content = content
        self.attachments = list(attachments)
        self.pinned = False

    async def edit(self, *, content, attachments, **kwargs):
        if self.channel.edit_failure == "before":
            raise OSError("Écriture refusée")
        self.content = content
        self.attachments = [attachment(item) for item in attachments]
        self.channel.root_edits += 1
        if self.channel.fetch_failure_after_edit:
            self.channel.fail_fetch = True
        if self.channel.after_edit:
            self.channel.after_edit()
        if self.channel.edit_failure == "after":
            raise OSError("Accusé perdu")
        return self

    async def pin(self, **kwargs):
        if self.channel.fail_pin:
            raise OSError("Épinglage refusé")
        self.pinned = True

    async def delete(self):
        if self.channel.fail_delete:
            raise OSError("Suppression refusée")
        del self.channel.messages[self.id]
        self.channel.deleted.append(self.id)


class Channel:
    def __init__(self, bot):
        self.bot = bot
        self.id = 300
        self.guild = SimpleNamespace(id=100, filesize_limit=8 * 1024 * 1024)
        self.messages = {}
        self.next_id = 1000
        self.deleted = []
        self.sent = []
        self.root_edits = 0
        self.edit_failure = None
        self.fail_fetch = False
        self.fetch_failure_after_edit = False
        self.after_edit = None
        self.fail_pin = False
        self.fail_delete = False
        self.fail_history = False
        self.fail_pins = False
        self.send_failure = None
        self.send_after = None

    async def send(self, content, *, file=None, **kwargs):
        if self.send_failure == "before":
            raise OSError("Envoi refusé")
        self.next_id += 1
        message = Message(self, self.next_id, content, [attachment(file)] if file else [])
        self.messages[message.id] = message
        self.sent.append(message)
        if self.send_after:
            self.send_after(message)
        if self.send_failure == "after":
            raise OSError("Accusé d'envoi perdu")
        return message

    async def fetch_message(self, identifier):
        if self.fail_fetch:
            raise OSError("Lecture indisponible")
        if identifier not in self.messages:
            raise missing()
        return self.messages[identifier]

    async def pins(self):
        if self.fail_pins:
            raise OSError("Lecture des épingles refusée")
        return [message for message in self.messages.values() if message.pinned]

    async def history(self, *, limit=None):
        if self.fail_history:
            raise OSError("Historique refusé")
        for message in reversed(list(self.messages.values())):
            yield message


class Leadership:
    def __init__(self):
        self.allowed = True
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if not self.allowed:
            raise BuildError("Leadership perdu")


@pytest.fixture
def discord_store():
    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    channel = Channel(bot)
    leadership = Leadership()
    bot.ensure_build_leadership = leadership
    return bot, channel, leadership


async def reopen(discord_store, quota=20):
    bot, channel, leadership = discord_store
    repo = ConsoleRepository(bot, quota=quota, channel=channel, leadership=leadership)
    await repo.open()
    return repo


@pytest_asyncio.fixture
async def console(discord_store, catalog, rules):
    repo = await reopen(discord_store)
    await repo.put_snapshot("catalog", catalog.id, canonical(catalog))
    await repo.put_snapshot("rules", rules.id, canonical(rules))
    return repo


async def create(repo, actor, build, operation="create"):
    return await repo.commit(actor, build, None, operation, digest(operation), digest("report"))


def quote(catalog, **changes):
    value = {
        "schema_version": 1, "id": str(uuid4()), "server": "Serveur synthétique",
        "template_ref": catalog.items[0].ref, "template_revision": catalog.items[0].revision,
        "jet_signature": digest("jet synthétique"), "kamas": 12345,
        "observed_at": datetime.now(timezone.utc).isoformat(), "label": "Objet synthétique",
    }
    return {**value, **changes}


@pytest.mark.asyncio
async def test_bootstrap_pins_only_root_and_ignores_foreign_messages(discord_store):
    _, channel, _ = discord_store
    foreign = await channel.send(f"{MARKER} root forged")
    foreign.author = SimpleNamespace(id=456)
    repo = await reopen(discord_store)
    assert repo.durable
    assert repo._root["revision"] == 0
    assert len(await channel.pins()) == 1
    assert foreign.id in channel.messages


@pytest.mark.asyncio
@pytest.mark.parametrize("verified_return", [True, False])
async def test_default_resolution_prefers_verified_lock_among_multiple_guild_consoles(discord_store, monkeypatch, verified_return):
    from unittest.mock import AsyncMock
    import utils.build.console_repository as storage
    bot, channel, _ = discord_store
    other = Channel(bot)
    other.id = 301
    other.guild = SimpleNamespace(id=101, filesize_limit=8 * 1024 * 1024)
    bot.guilds = [other.guild, channel.guild]
    bot._lock_channel_id = channel.id
    bot.get_channel = lambda identifier: channel if identifier == channel.id else other
    bot.ensure_build_leadership = AsyncMock(return_value=channel if verified_return else None)
    monkeypatch.setattr(storage, "resolve_console_channel", lambda guild: channel if guild.id == 100 else other)
    repo = ConsoleRepository(bot)
    await repo.open()
    assert repo.channel is channel
    assert repo._root["guild_id"] == 100
    assert not other.messages
    assert await repo.list(Actor(guild_id=100, user_id=200)) == ()
    with pytest.raises(NotFound):
        await repo.list(Actor(guild_id=101, user_id=200))


@pytest.mark.asyncio
async def test_default_resolution_still_rejects_ambiguity_without_verified_channel(discord_store, monkeypatch):
    import utils.build.console_repository as storage
    bot, channel, _ = discord_store
    other = Channel(bot)
    other.id = 301
    other.guild = SimpleNamespace(id=101, filesize_limit=8 * 1024 * 1024)
    bot.guilds = [channel.guild, other.guild]
    monkeypatch.setattr(storage, "resolve_console_channel", lambda guild: channel if guild.id == 100 else other)
    with pytest.raises(BuildError, match="console Discord unique"):
        await ConsoleRepository(bot).open()
    assert not channel.messages and not other.messages


@pytest.mark.asyncio
async def test_verified_console_change_during_session_is_rejected(discord_store):
    from unittest.mock import AsyncMock
    bot, channel, _ = discord_store
    bot.ensure_build_leadership = AsyncMock(return_value=channel)
    repo = ConsoleRepository(bot)
    await repo.open()
    other = Channel(bot)
    other.id = 301
    bot.ensure_build_leadership.return_value = other
    with pytest.raises(BuildError, match="console Build a changé"):
        await repo.list(Actor(guild_id=100, user_id=200))
    assert not other.messages


@pytest.mark.asyncio
async def test_restart_recovers_build_history_receipts_catalog_and_share(console, discord_store, actor, build, catalog):
    saved = await create(console, actor, build)
    changed = await console.commit(actor, revised(saved, name="Après redémarrage"), 1,
                                   "rename", digest("rename"), digest("report2"))
    receipt = await console.share(actor, changed, "share", digest("share"))
    await console.close()
    restored = await reopen(discord_store)
    assert await restored.get(actor, saved.id) == changed
    assert tuple(row.revision for row in await restored.revisions(actor, saved.id)) == (2, 1)
    assert await restored.replay(actor, "create", digest("create")) == {"build": saved.model_dump(mode="json")}
    assert await restored.shared(Actor(guild_id=100, user_id=201), receipt["code"]) == changed
    assert await restored.snapshot("catalog", catalog.id) == canonical(catalog)
    assert len(await discord_store[1].pins()) == 1


@pytest.mark.asyncio
async def test_catalog_not_resent_on_member_mutation(console, discord_store, actor, build):
    before = list(discord_store[1].sent)
    saved = await create(console, actor, build)
    snapshots = deepcopy(console._root["snapshots"])
    await console.commit(actor, revised(saved, name="Nouveau nom"), 1,
                         "rename", digest("rename"), digest("report"))
    assert console._root["snapshots"] == snapshots
    assert len(discord_store[1].sent) - len(before) == 2


@pytest.mark.asyncio
async def test_two_repositories_serialize_cas_and_double_click(console, discord_store, actor, build):
    import asyncio
    other = await reopen(discord_store)
    saved = await create(console, actor, build)
    results = await asyncio.gather(*(
        repo.commit(actor, revised(saved, name=name), 1, name, digest(name), digest("report"))
        for repo, name in ((console, "a"), (other, "b"))
    ), return_exceptions=True)
    assert sum(isinstance(result, Conflict) for result in results) == 1
    before = discord_store[1].root_edits
    repeated = await asyncio.gather(*(create(repo, actor, build) for repo in (console, other)))
    assert repeated == [saved, saved]
    assert discord_store[1].root_edits == before
    with pytest.raises(Conflict):
        await console.replay(actor, "create", digest("different"))


@pytest.mark.asyncio
async def test_quota_under_concurrent_creations(console, actor, build):
    import asyncio
    console.quota = 1
    results = await asyncio.gather(*(
        create(console, actor, revised(build, id=str(uuid4())), f"create:{index}")
        for index in range(4)
    ), return_exceptions=True)
    assert sum(isinstance(result, BuildError) for result in results) == 3
    assert len(await console.list(actor)) == 1


@pytest.mark.asyncio
async def test_lost_root_ack_is_confirmed_by_read(console, discord_store, actor, build):
    channel = discord_store[1]
    channel.edit_failure = "after"
    saved = await create(console, actor, build)
    assert saved.revision == 1
    channel.edit_failure = None
    restored = await reopen(discord_store)
    assert await create(restored, actor, build) == saved
    assert len(await restored.list(actor)) == 1


@pytest.mark.asyncio
async def test_root_ack_and_read_lost_reconcile_without_duplicate(console, discord_store, actor, build):
    channel = discord_store[1]
    channel.edit_failure = "after"
    channel.fetch_failure_after_edit = True
    with pytest.raises(BuildError, match="incertain"):
        await create(console, actor, build)
    channel.fail_fetch = False
    channel.fetch_failure_after_edit = False
    channel.edit_failure = None
    restored = await reopen(discord_store)
    saved = await create(restored, actor, build)
    assert saved.revision == 1
    assert len(await restored.list(actor)) == 1


@pytest.mark.asyncio
async def test_failed_root_edit_preserves_previous_generation(console, discord_store, actor, build):
    saved = await create(console, actor, build)
    channel = discord_store[1]
    old = deepcopy(console._root)
    channel.edit_failure = "before"
    with pytest.raises(BuildError, match="confirmée"):
        await console.commit(actor, revised(saved, name="Non engagé"), 1,
                             "rename", digest("rename"), digest("report"))
    assert console._root == old
    assert console._active_ids(old) <= channel.messages.keys()
    channel.edit_failure = None
    restored = await reopen(discord_store)
    assert await restored.get(actor, saved.id) == saved
    assert await restored.replay(actor, "rename", digest("rename")) is None


@pytest.mark.asyncio
async def test_uncertain_unchanged_root_blocks_other_writes_until_late_confirmation(console, discord_store, actor, build):
    channel = discord_store[1]
    channel.edit_failure = "before"
    with pytest.raises(BuildError, match="confirmée"):
        await create(console, actor, build)
    channel.edit_failure = None
    restored = await reopen(discord_store)
    assert await restored.list(actor) == ()
    with pytest.raises(BuildError, match="nouvelles écritures suspendues"):
        await create(restored, actor, revised(build, id=str(uuid4())), "other")
    pending = console._pending_roots[channel.id]
    content, file = console._root_content(pending)
    try:
        await console._root_message.edit(content=content, attachments=[file] if file else [])
    finally:
        if file:
            file.close()
    saved = await create(restored, actor, build)
    assert saved.revision == 1
    assert len(await restored.list(actor)) == 1


@pytest.mark.asyncio
async def test_confirmed_forbidden_edit_allows_retry_after_permissions_restore(console, discord_store, actor, build):
    from unittest.mock import AsyncMock
    message = console._root_message
    original = message.edit
    message.edit = AsyncMock(side_effect=discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Permission refusée",
    ))
    with pytest.raises(BuildError, match="confirmée"):
        await create(console, actor, build)
    assert not console._pending_roots
    message.edit = original
    assert (await create(console, actor, build)).revision == 1


@pytest.mark.asyncio
async def test_deleted_active_member_snapshot_blocks_cached_reads_and_mutations(console, discord_store, actor, build):
    saved = await create(console, actor, build)
    descriptor = console._root["members"]["100:200"]
    del discord_store[1].messages[descriptor["parts"][0]["id"]]
    before = discord_store[1].root_edits
    with pytest.raises(BuildError):
        await console.get(actor, saved.id)
    with pytest.raises(BuildError):
        await console.commit(actor, revised(saved, name="Depuis le cache"), 1,
                             "rename", digest("rename"), digest("report"))
    assert discord_store[1].root_edits == before


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "replaced_attachment", "wrong_author"])
async def test_changed_catalog_after_load_blocks_mutation_and_cached_snapshot(console, discord_store, actor, build, catalog, damage):
    saved = await create(console, actor, build)
    descriptor = console._root["snapshots"]["catalog"][catalog.id]
    channel = discord_store[1]
    message = channel.messages[descriptor["parts"][0]["id"]]
    if damage == "missing":
        del channel.messages[message.id]
    elif damage == "replaced_attachment":
        previous = message.attachments[0]
        message.attachments = [Attachment(previous.filename, previous.data)]
    else:
        message.author = SimpleNamespace(id=123)
    before = channel.root_edits
    with pytest.raises(BuildError):
        await console.commit(actor, revised(saved, name="Interdit"), 1,
                             "rename", digest("rename"), digest("report"))
    with pytest.raises(BuildError):
        await console.snapshot("catalog", catalog.id)
    with pytest.raises(BuildError):
        await console.put_snapshot("catalog", catalog.id, canonical(catalog))
    assert channel.root_edits == before


@pytest.mark.asyncio
async def test_orphan_blob_from_lost_send_does_not_become_state(console, discord_store, actor, build):
    channel = discord_store[1]
    channel.send_failure = "after"
    with pytest.raises(BuildError):
        await create(console, actor, build)
    channel.send_failure = None
    restored = await reopen(discord_store)
    assert await restored.list(actor) == ()
    assert len(await channel.pins()) == 1


@pytest.mark.asyncio
async def test_creation_ack_lost_finds_existing_root(discord_store):
    channel = discord_store[1]
    channel.send_failure = "after"
    repo = await reopen(discord_store)
    assert repo._opened
    assert len(channel.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fail_history", "fail_pins", "fail_pin"])
async def test_permission_failure_never_opens_empty_database(discord_store, failure):
    channel = discord_store[1]
    setattr(channel, failure, True)
    with pytest.raises(BuildError):
        await reopen(discord_store)
    if failure != "fail_pin":
        assert not channel.messages


@pytest.mark.asyncio
async def test_missing_root_with_fragments_refuses_empty_restart(console, discord_store, actor, build):
    await create(console, actor, build)
    channel = discord_store[1]
    del channel.messages[console._root_message.id]
    before = len(channel.messages)
    with pytest.raises(BuildError, match="racine Build a disparu"):
        await reopen(discord_store)
    assert len(channel.messages) == before


@pytest.mark.asyncio
async def test_duplicate_roots_are_not_selected_by_recency(console, discord_store):
    channel = discord_store[1]
    original = console._root_message
    channel.next_id += 1
    duplicate = Message(channel, channel.next_id, original.content, original.attachments)
    channel.messages[duplicate.id] = duplicate
    with pytest.raises(BuildError, match="Plusieurs racines"):
        await reopen(discord_store)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing", "bytes", "author", "root"])
async def test_corrupt_last_generation_never_falls_back(console, discord_store, actor, build, corruption):
    saved = await create(console, actor, build)
    channel = discord_store[1]
    channel.fail_delete = True
    await console.commit(actor, revised(saved, name="Dernière génération"), 1,
                         "rename", digest("rename"), digest("report"))
    descriptor = console._root["members"]["100:200"]
    message = channel.messages[descriptor["parts"][0]["id"]]
    if corruption == "missing":
        del channel.messages[message.id]
    elif corruption == "bytes":
        message.attachments[0].data = b"x" * message.attachments[0].size
    elif corruption == "author":
        message.author = SimpleNamespace(id=432)
    else:
        console._root_message.content += "x"
    with pytest.raises(BuildError):
        await reopen(discord_store)


@pytest.mark.asyncio
async def test_fragmented_snapshots_hashes_and_kinds(discord_store):
    channel = discord_store[1]
    channel.guild.filesize_limit = 2048
    repo = await reopen(discord_store)
    raw = canonical({"spells": "é" * 2300})
    identifier = digest(raw)
    await repo.put_snapshot("spells", identifier, raw)
    descriptor = repo._root["snapshots"]["spells"][identifier]
    assert len(descriptor["parts"]) == 3
    assert all(part["size"] <= 2048 for part in descriptor["parts"])
    restored = await reopen(discord_store)
    assert await restored.latest_snapshot("spells") == raw
    await restored.put_snapshot("attacks", digest("attacks"), "{}")
    with pytest.raises(BuildError, match="Collision"):
        await restored.put_snapshot("spells", identifier, "autre")


@pytest.mark.asyncio
async def test_oversized_blob_refused_before_send(console, discord_store, monkeypatch):
    import utils.build.console_repository as storage
    monkeypatch.setattr(storage, "MAX_BLOB_BYTES", 10)
    before = len(discord_store[1].sent)
    with pytest.raises(BuildError):
        await console.put_snapshot("attacks", digest("large"), "x" * 11)
    assert len(discord_store[1].sent) == before


@pytest.mark.asyncio
async def test_leadership_loss_before_and_after_commit_stops_confirmation(console, discord_store, actor, build):
    channel, leadership = discord_store[1:]
    leadership.allowed = False
    with pytest.raises(BuildError, match="Leadership"):
        await create(console, actor, build)
    leadership.allowed = True
    channel.after_edit = lambda: setattr(leadership, "allowed", False)
    with pytest.raises(BuildError, match="Leadership"):
        await create(console, actor, build)
    leadership.allowed = True
    channel.after_edit = None
    restored = await reopen(discord_store)
    assert len(await restored.list(actor)) == 1
    assert (await create(restored, actor, build)).revision == 1


@pytest.mark.asyncio
async def test_publication_claim_and_revocation_survive_restart(console, discord_store, actor, build):
    saved = await create(console, actor, build)
    receipt = await console.share(actor, saved, "share", digest("share"))
    assert await console.claim_publication(actor, receipt["code"])
    restored = await reopen(discord_store)
    with pytest.raises(BuildError, match="Publication déjà"):
        await restored.claim_publication(actor, receipt["code"])
    await restored.publication(actor, receipt["code"], "sent", 777, 888)
    restored = await reopen(discord_store)
    assert await restored.claim_publication(actor, receipt["code"]) is False
    await restored.revoke(actor, receipt["code"])
    restored = await reopen(discord_store)
    with pytest.raises(NotFound):
        await restored.shared(actor, receipt["code"])


@pytest.mark.asyncio
async def test_delete_removes_history_shares_receipts_and_old_member_blobs(console, discord_store, actor, build):
    saved = await create(console, actor, build)
    receipt = await console.share(actor, saved, "share", digest("share"))
    old_ids = {part["id"] for part in console._root["members"]["100:200"]["parts"]}
    await console.delete(actor, saved.id, 1, "delete", digest("delete"))
    assert not old_ids & discord_store[1].messages.keys()
    restored = await reopen(discord_store)
    assert await restored.list(actor) == ()
    assert await restored.replay(actor, "create", digest("create")) is None
    assert await restored.replay(actor, "delete", digest("delete")) == {"deleted": saved.id}
    assert restored._members["100:200"]["history"] == {}
    with pytest.raises(NotFound):
        await restored.shared(actor, receipt["code"])


@pytest.mark.asyncio
async def test_deferred_cleanup_resumes_and_never_removes_other_modules(console, discord_store, actor, build):
    channel = discord_store[1]
    unrelated = await channel.send("===BOTJOBS=== important")
    saved = await create(console, actor, build)
    old_ids = {part["id"] for part in console._root["members"]["100:200"]["parts"]}
    channel.fail_delete = True
    changed = await console.commit(actor, revised(saved, name="Final"), 1,
                                   "rename", digest("rename"), digest("report"))
    assert old_ids <= channel.messages.keys()
    channel.fail_delete = False
    restored = await reopen(discord_store)
    assert await restored.get(actor, saved.id) == changed
    assert not old_ids & channel.messages.keys()
    assert unrelated.id in channel.messages


@pytest.mark.asyncio
async def test_isolation_covers_reads_writes_receipts_and_shares(console, actor, build):
    saved = await create(console, actor, build)
    receipt = await console.share(actor, saved, "share", digest("share"))
    other = Actor(guild_id=100, user_id=201)
    assert await console.list(other) == ()
    assert await console.replay(other, "create", digest("create")) is None
    with pytest.raises(NotFound):
        await console.get(other, saved.id)
    with pytest.raises(NotFound):
        await console.revisions(other, saved.id)
    with pytest.raises(NotFound):
        await console.delete(other, saved.id, 1, "delete", digest("delete"))
    with pytest.raises(NotFound):
        await console.revoke(other, receipt["code"])
    with pytest.raises(NotFound):
        await console.shared(Actor(guild_id=101, user_id=200), receipt["code"])


@pytest.mark.asyncio
async def test_history_twenty_and_receipts_thirty_days(console, discord_store, actor, build, monkeypatch):
    import utils.build.repository as memory
    earlier = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    monkeypatch.setattr(memory, "now", lambda: earlier)
    saved = await create(console, actor, build)
    monkeypatch.undo()
    for number in range(22):
        saved = await console.commit(actor, revised(saved, name=f"Version {number}"), saved.revision,
                                     f"rename:{number}", digest(number), digest("report"))
    restored = await reopen(discord_store)
    assert len(await restored.revisions(actor, saved.id)) == 20
    assert await restored.replay(actor, "create", digest("create")) is None
    assert await restored.replay(actor, "rename:0", digest(0)) is not None


@pytest.mark.asyncio
async def test_personal_prices_are_validated_detached_isolated_and_durable(console, discord_store, actor, catalog):
    value = quote(catalog)
    saved = await console.set_price(actor, value)
    value["kamas"] = 1
    saved["kamas"] = 2
    restored = await reopen(discord_store)
    prices = await restored.prices(actor)
    assert prices[0]["kamas"] == 12345
    other = Actor(guild_id=100, user_id=201)
    assert await restored.prices(other) == ()
    with pytest.raises(NotFound):
        await restored.delete_price(other, value["id"])
    with pytest.raises(BuildError, match="Prix personnel invalide"):
        await restored.set_price(actor, quote(catalog, owner_id=201))
    await restored.delete_price(actor, value["id"])
    restored = await reopen(discord_store)
    assert await restored.prices(actor) == ()


@pytest.mark.asyncio
async def test_memory_supports_price_contract_and_extra_snapshot_families(actor, catalog):
    repo = MemoryRepository()
    value = quote(catalog)
    await repo.set_price(actor, value)
    assert (await repo.prices(actor))[0] == {**value, "jets": []}
    with pytest.raises(BuildError):
        await repo.set_price(actor, quote(catalog, kamas=-1))
    await repo.delete_price(actor, value["id"])
    assert await repo.prices(actor) == ()
    for kind in ("spells", "attacks"):
        await repo.put_snapshot(kind, digest(kind), "{}")
        assert await repo.latest_snapshot(kind) == "{}"
