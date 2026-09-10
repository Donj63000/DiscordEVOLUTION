import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

from job import JobCog, STAFF_ROLE_NAME


class FakeContext:
    def __init__(self, author, guild_id=1, channel_id=99):
        self.author = author
        self.guild = SimpleNamespace(id=guild_id)
        self.channel = SimpleNamespace(id=channel_id)
        self.sent_messages = []

    async def send(self, content=None, *, embed=None, file=None):
        payload = SimpleNamespace(content=content, embed=embed, file=file)
        self.sent_messages.append(payload)
        return payload


class FakeConsoleMessage:
    def __init__(self, content="", *, pinned=False, message_id=123):
        self.id = message_id
        self.content = content
        self.pinned = pinned
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FailingConsoleMessage(FakeConsoleMessage):
    async def delete(self):
        response = SimpleNamespace(status=500, reason="internal error", text="error")
        raise discord.HTTPException(response, "delete failed")


class FakeConsoleChannel:
    def __init__(self, messages, channel_id=456):
        self.id = channel_id
        self.messages = list(messages)

    async def history(self, limit=None, oldest_first=False):
        for message in self.messages:
            yield message


@pytest.fixture
def job_cog():
    fake_bot = SimpleNamespace(
        guilds=[],
        user=SimpleNamespace(id=777, name="bot"),
    )
    fake_bot.get_all_members = lambda: []

    async def fake_wait_for(*args, **kwargs):
        await asyncio.sleep(0)

    fake_bot.wait_for = fake_wait_for

    cog = JobCog(fake_bot)
    cog.initialized = True

    async def fake_load_from_console(_guild):
        return True

    async def fake_dump_data(_guild):
        return True

    cog.load_from_console = fake_load_from_console
    cog.dump_data_to_console = fake_dump_data
    cog.save_data_local = lambda: None
    return cog


async def invoke_job(cog, ctx, *args):
    await cog.job_command.callback(cog, ctx, *args)


async def invoke_clear(cog, ctx, *args):
    await cog.clear_console_command.callback(cog, ctx, *args)


class JobConfirmationDriver:
    """Je simule les réponses et les délais Discord sans réseau ni attente réelle."""

    def __init__(self, cog):
        self.cog = cog
        self.listeners = []
        self.started = asyncio.Queue()
        self.commands = []

    def wait_for(self, event, *, timeout, check):
        assert event == "message"
        assert timeout == 30.0
        future = asyncio.get_running_loop().create_future()
        self.listeners.append((check, future))
        self.started.put_nowait(future)
        return future

    def start(self, ctx, *args):
        task = asyncio.create_task(invoke_job(self.cog, ctx, *args))
        self.commands.append(task)
        return task

    async def next_confirmation(self):
        return await asyncio.wait_for(self.started.get(), timeout=2.0)

    def reply(self, ctx, content):
        message = SimpleNamespace(
            author=ctx.author, channel=ctx.channel, guild=ctx.guild, content=content
        )
        for check, future in self.listeners:
            if not future.done() and check(message):
                future.set_result(message)

    async def close(self):
        for task in self.commands:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.commands, return_exceptions=True)


@pytest_asyncio.fixture
async def confirmations(job_cog):
    driver = JobConfirmationDriver(job_cog)
    job_cog.bot.wait_for = driver.wait_for
    job_cog.save_data_local = Mock()
    job_cog.dump_data_to_console = AsyncMock(return_value=True)

    async def send_embed(ctx, embed):
        return await ctx.send(embed=embed)

    job_cog.send_logo_embed = send_embed
    try:
        yield driver
    finally:
        await driver.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_prefix", [(), ("add",)])
@pytest.mark.parametrize("replacement_prefix", [(), ("add",)])
async def test_new_job_submission_silently_replaces_confirmation(
    job_cog, confirmations, previous_prefix, replacement_prefix
):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    previous_ctx = FakeContext(author)
    previous = confirmations.start(
        previous_ctx, *previous_prefix, "forgeur", "de", "dague", "100"
    )
    waiter = await confirmations.next_confirmation()
    replacement_ctx = FakeContext(author)

    await invoke_job(job_cog, replacement_ctx, *replacement_prefix, "forgeur", "d'armes", "100")
    await asyncio.wait_for(previous, timeout=2.0)
    confirmations.reply(previous_ctx, "oui")

    assert waiter.cancelled()
    assert [msg.embed.title for msg in previous_ctx.sent_messages] == ["Confirmation"]
    assert [msg.embed.title for msg in replacement_ctx.sent_messages] == ["Mise à jour du métier"]
    assert job_cog.jobs_data["123"]["jobs"] == {"Forgeur d’armes": 100}
    job_cog.save_data_local.assert_called_once_with()
    job_cog.dump_data_to_console.assert_awaited_once_with(replacement_ctx.guild)


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_with_known_job", [False, True])
async def test_new_unknown_job_keeps_only_its_own_confirmation(
    job_cog, confirmations, finish_with_known_job
):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    previous_ctx = FakeContext(author)
    previous = confirmations.start(previous_ctx, "Métier précédent", "50")
    previous_waiter = await confirmations.next_confirmation()
    current_ctx = FakeContext(author)
    current = confirmations.start(current_ctx, "Nouveau métier", "75")
    current_waiter = await confirmations.next_confirmation()
    await asyncio.wait_for(previous, timeout=2.0)

    if finish_with_known_job:
        await invoke_job(job_cog, FakeContext(author), "Mineur", "100")
        expected_jobs = {"Mineur": 100}
    else:
        confirmations.reply(current_ctx, "oui")
        expected_jobs = {"Nouveau métier": 75}
    await asyncio.wait_for(current, timeout=2.0)

    assert previous_waiter.cancelled()
    assert current_waiter.done()
    assert [msg.embed.title for msg in previous_ctx.sent_messages] == ["Confirmation"]
    assert job_cog.jobs_data["123"]["jobs"] == expected_jobs
    job_cog.save_data_local.assert_called_once_with()
    job_cog.dump_data_to_console.assert_awaited_once_with(current_ctx.guild)
    assert not job_cog._job_submissions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "author_id, guild_id, channel_id",
    [(456, 1, 99), (123, 1, 100), (123, 2, 99)],
)
async def test_job_submission_preserves_other_confirmation_scopes(
    job_cog, confirmations, author_id, guild_id, channel_id
):
    original_author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    previous_ctx = FakeContext(original_author)
    previous = confirmations.start(previous_ctx, "Métier précédent", "50")
    previous_waiter = await confirmations.next_confirmation()
    other_author = SimpleNamespace(id=author_id, display_name="Hero", roles=[])
    other_ctx = FakeContext(other_author, guild_id=guild_id, channel_id=channel_id)

    await invoke_job(job_cog, other_ctx, "Mineur", "100")

    assert not previous.done()
    assert not previous_waiter.done()
    previous_waiter.set_exception(asyncio.TimeoutError())
    await asyncio.wait_for(previous, timeout=2.0)
    assert previous_ctx.sent_messages[-1].embed.title == "Confirmation expirée"
    assert "Métier précédent" in previous_ctx.sent_messages[-1].embed.description
    assert job_cog.jobs_data[str(author_id)]["jobs"] == {"Mineur": 100}
    job_cog.save_data_local.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [(), ("me",), ("liste",), ("Mineur", "0"), ("Mineur", "101"), ("add", "Mineur", "oops")],
)
async def test_queries_and_invalid_submissions_preserve_confirmation(job_cog, confirmations, args):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    previous_ctx = FakeContext(author)
    previous = confirmations.start(previous_ctx, "Métier précédent", "50")
    waiter = await confirmations.next_confirmation()

    await invoke_job(job_cog, FakeContext(author), *args)

    assert not previous.done()
    assert not waiter.done()
    confirmations.reply(previous_ctx, "non")
    await asyncio.wait_for(previous, timeout=2.0)
    assert previous_ctx.sent_messages[-1].embed.title == "Commande terminée"
    assert job_cog.jobs_data == {}
    job_cog.save_data_local.assert_not_called()
    job_cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["oui", "OUI", "non", "cancel", None])
async def test_job_confirmation_preserves_answers_and_names_expired_request(
    job_cog, confirmations, reply
):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    ctx = FakeContext(author)
    task = confirmations.start(ctx, "Métier personnalisé", "75")
    waiter = await confirmations.next_confirmation()

    if reply is None:
        waiter.set_exception(asyncio.TimeoutError())
    else:
        confirmations.reply(ctx, reply)
    await asyncio.wait_for(task, timeout=2.0)

    if reply is not None and reply.lower() == "oui":
        assert job_cog.jobs_data["123"]["jobs"] == {"Métier personnalisé": 75}
        assert ctx.sent_messages[-1].embed.title == "Nouveau métier créé"
        job_cog.save_data_local.assert_called_once_with()
        job_cog.dump_data_to_console.assert_awaited_once_with(ctx.guild)
    else:
        assert job_cog.jobs_data == {}
        job_cog.save_data_local.assert_not_called()
        job_cog.dump_data_to_console.assert_not_awaited()
        if reply is None:
            embed = ctx.sent_messages[-1].embed
            assert embed.title == "Confirmation expirée"
            assert all(value in embed.description for value in ("Métier personnalisé", "75", "Hero"))
        else:
            assert ctx.sent_messages[-1].embed.title == "Commande terminée"
    assert not job_cog._job_submissions


@pytest.mark.asyncio
async def test_job_confirmation_ignores_unrelated_replies(job_cog, confirmations):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    ctx = FakeContext(author)
    task = confirmations.start(ctx, "Métier personnalisé", "75")
    waiter = await confirmations.next_confirmation()
    other_author = SimpleNamespace(id=456, display_name="Other", roles=[])

    confirmations.reply(FakeContext(other_author), "oui")
    confirmations.reply(FakeContext(author, channel_id=100), "oui")
    confirmations.reply(ctx, "un autre message")
    assert not waiter.done()
    confirmations.reply(ctx, "oui")
    await asyncio.wait_for(task, timeout=2.0)
    assert job_cog.jobs_data["123"]["jobs"] == {"Métier personnalisé": 75}


@pytest.mark.asyncio
@pytest.mark.parametrize("load_number", [1, 2])
@pytest.mark.parametrize("previous_prefix", [(), ("add",)])
async def test_replacement_during_console_load_prevents_late_confirmation(
    job_cog, confirmations, load_number, previous_prefix
):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    previous_ctx = FakeContext(author)
    load_started = asyncio.Event()
    release_load = asyncio.Event()
    previous_loads = 0

    async def delayed_load(_guild):
        nonlocal previous_loads
        if asyncio.current_task() is previous:
            previous_loads += 1
            if previous_loads == load_number:
                load_started.set()
                await release_load.wait()
        return True

    job_cog.load_from_console = delayed_load
    previous = confirmations.start(previous_ctx, *previous_prefix, "Métier précédent", "50")
    await asyncio.wait_for(load_started.wait(), timeout=2.0)
    await invoke_job(job_cog, FakeContext(author), "Mineur", "100")
    release_load.set()
    await asyncio.wait_for(previous, timeout=2.0)

    assert confirmations.started.empty()
    assert previous_ctx.sent_messages == []
    assert job_cog.jobs_data["123"]["jobs"] == {"Mineur": 100}
    job_cog.save_data_local.assert_called_once_with()
    assert not job_cog._job_submissions


@pytest.mark.asyncio
async def test_replacement_during_prompt_send_prevents_late_waiter(job_cog, confirmations):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    previous_ctx = FakeContext(author)
    prompt_started = asyncio.Event()
    release_prompt = asyncio.Event()
    send_embed = job_cog.send_logo_embed

    async def delayed_send(ctx, embed):
        if ctx is previous_ctx:
            prompt_started.set()
            await release_prompt.wait()
        return await send_embed(ctx, embed)

    job_cog.send_logo_embed = delayed_send
    previous = confirmations.start(previous_ctx, "Métier précédent", "50")
    await asyncio.wait_for(prompt_started.wait(), timeout=2.0)
    await invoke_job(job_cog, FakeContext(author), "Mineur", "100")
    release_prompt.set()
    await asyncio.wait_for(previous, timeout=2.0)

    assert confirmations.started.empty()
    assert [msg.embed.title for msg in previous_ctx.sent_messages] == ["Confirmation"]
    assert job_cog.jobs_data["123"]["jobs"] == {"Mineur": 100}
    assert not job_cog._job_submissions


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "reply"])
async def test_replacement_wins_over_ready_confirmation_outcome(job_cog, confirmations, outcome):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    ctx = FakeContext(author)
    previous = confirmations.start(ctx, "Métier précédent", "50")
    waiter = await confirmations.next_confirmation()

    if outcome == "timeout":
        waiter.set_exception(asyncio.TimeoutError())
    else:
        confirmations.reply(ctx, "oui")
    await invoke_job(job_cog, FakeContext(author), "Mineur", "100")
    await asyncio.wait_for(previous, timeout=2.0)

    assert [msg.embed.title for msg in ctx.sent_messages] == ["Confirmation"]
    assert job_cog.jobs_data["123"]["jobs"] == {"Mineur": 100}
    job_cog.save_data_local.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_module", [False, True])
async def test_job_confirmation_cleans_up_on_cancellation_or_unload(
    job_cog, confirmations, close_module
):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    ctx = FakeContext(author)
    task = confirmations.start(ctx, "Métier personnalisé", "75")
    waiter = await confirmations.next_confirmation()

    if close_module:
        job_cog.cog_unload()
        await asyncio.wait_for(task, timeout=2.0)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert waiter.cancelled()
    assert [msg.embed.title for msg in ctx.sent_messages] == ["Confirmation"]
    assert not job_cog._job_submissions
    job_cog.save_data_local.assert_not_called()
    job_cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_add_updates_jobs_data(job_cog):
    author = SimpleNamespace(id=123, display_name="Hero", roles=[])
    ctx = FakeContext(author)

    await invoke_job(job_cog, ctx, "add", "Mineur", "75")

    assert job_cog.jobs_data["123"]["jobs"]["Mineur"] == 75
    assert ctx.sent_messages
    embed = ctx.sent_messages[-1].embed
    assert embed is not None
    assert "Mineur" in embed.description
    assert "75" in embed.description


@pytest.mark.asyncio
async def test_job_me_lists_existing_jobs(job_cog):
    author_id = "321"
    job_cog.jobs_data = {
        author_id: {
            "name": "Crafter",
            "jobs": {"Mineur": 50, "Boulanger": 100},
        }
    }
    author = SimpleNamespace(id=int(author_id), display_name="Crafter", roles=[])
    ctx = FakeContext(author)

    await invoke_job(job_cog, ctx, "me")

    embed = ctx.sent_messages[-1].embed
    assert embed.title.endswith("Crafter")
    names = [field.name for field in embed.fields]
    assert "Mineur" in names
    assert "Boulanger" in names


@pytest.mark.asyncio
async def test_job_prune_requires_staff_role(job_cog):
    author = SimpleNamespace(id=999, display_name="NoStaff", roles=[])
    ctx = FakeContext(author)

    await invoke_job(job_cog, ctx, "prune")

    embed = ctx.sent_messages[-1].embed
    assert "Staff" in embed.description


@pytest.mark.asyncio
async def test_job_prune_staff_triggers_cleanup(job_cog):
    staff_role = SimpleNamespace(name=STAFF_ROLE_NAME)
    author = SimpleNamespace(id=555, display_name="Mod", roles=[staff_role])
    ctx = FakeContext(author)
    job_cog.prune_jobs = AsyncMock(return_value=2)

    await invoke_job(job_cog, ctx, "prune")

    job_cog.prune_jobs.assert_awaited_once()
    embed = ctx.sent_messages[-1].embed
    assert "2" in embed.description


@pytest.mark.asyncio
async def test_clear_console_preserves_persistent_snapshots(job_cog):
    author = SimpleNamespace(id=555, display_name="Mod", roles=[SimpleNamespace(name=STAFF_ROLE_NAME)])
    ctx = FakeContext(author)
    persistent = FakeConsoleMessage("===BOTSTATS===\n```json\n{}\n```")
    branding = FakeConsoleMessage("===BOTBRANDING===\n```json\n{}\n```")
    pinned = FakeConsoleMessage("message épinglé", pinned=True)
    transient = FakeConsoleMessage("debug temporaire")
    channel = FakeConsoleChannel([persistent, branding, pinned, transient])
    job_cog.get_console_channel = AsyncMock(return_value=channel)

    await invoke_clear(job_cog, ctx, "console", "CONFIRMER")

    assert persistent.deleted is False
    assert branding.deleted is False
    assert pinned.deleted is False
    assert transient.deleted is True
    embed = ctx.sent_messages[-1].embed
    assert "Messages supprimés" in embed.description
    assert "**1**" in embed.description


@pytest.mark.asyncio
async def test_clear_console_ignores_delete_api_error(job_cog):
    author = SimpleNamespace(id=555, display_name="Mod", roles=[SimpleNamespace(name=STAFF_ROLE_NAME)])
    ctx = FakeContext(author)
    failing = FailingConsoleMessage("debug impossible à supprimer")
    stable = FakeConsoleMessage("===BOTJOBS===\n```json\n{}\n```")
    channel = FakeConsoleChannel([failing, stable])
    job_cog.get_console_channel = AsyncMock(return_value=channel)

    await invoke_clear(job_cog, ctx, "console", "CONFIRMER")

    embed = ctx.sent_messages[-1].embed
    assert embed.title == "Nettoyage effectué"
    assert "Messages supprimés : **0**" in embed.description
    assert "conservés : **1**" in embed.description


@pytest.mark.asyncio
async def test_on_member_remove_deletes_entry_and_persists(job_cog):
    guild = SimpleNamespace(id=77)
    member = SimpleNamespace(id=444, display_name="Crafter", guild=guild)
    job_cog.jobs_data = {
        "444": {
            "name": "Crafter",
            "jobs": {"Mineur": 80},
        }
    }
    job_cog.save_data_local = Mock()
    job_cog.dump_data_to_console = AsyncMock()

    await job_cog.on_member_remove(member)

    assert "444" not in job_cog.jobs_data
    job_cog.save_data_local.assert_called_once_with()
    job_cog.dump_data_to_console.assert_awaited_once_with(guild)


@pytest.mark.asyncio
async def test_on_member_remove_without_match_skips_persistence(job_cog):
    guild = SimpleNamespace(id=88)
    member = SimpleNamespace(id=999, display_name="Absent", guild=guild)
    job_cog.jobs_data = {
        "123": {
            "name": "Present",
            "jobs": {"Boulanger": 100},
        }
    }
    initial_data = dict(job_cog.jobs_data)
    job_cog.save_data_local = Mock()
    job_cog.dump_data_to_console = AsyncMock()

    await job_cog.on_member_remove(member)

    assert job_cog.jobs_data == initial_data
    job_cog.save_data_local.assert_not_called()
    job_cog.dump_data_to_console.assert_not_awaited()


@pytest.mark.asyncio
async def test_initialize_data_blocks_local_fallback_when_disabled(monkeypatch, tmp_path):
    local_file = tmp_path / "jobs_data.json"
    local_file.write_text('{"123": {"name": "Local", "jobs": {"Mineur": 20}}}', encoding="utf-8")
    monkeypatch.setattr("job.DATA_FILE", str(local_file))
    monkeypatch.setattr("job.JOB_ALLOW_LOCAL_FALLBACK", False)

    fake_bot = SimpleNamespace(guilds=[SimpleNamespace(id=1)], user=SimpleNamespace(id=777, name="bot"))
    cog = JobCog(fake_bot)

    async def fake_load_from_console(_guild):
        return False

    cog.load_from_console = fake_load_from_console
    cog.migrate_legacy_keys = AsyncMock()

    await cog.initialize_data()

    assert cog.jobs_data == {}
    cog.migrate_legacy_keys.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_initialize_data_uses_local_fallback_when_enabled(monkeypatch, tmp_path):
    local_file = tmp_path / "jobs_data.json"
    local_file.write_text('{"123": {"name": "Local", "jobs": {"Mineur": 20}}}', encoding="utf-8")
    monkeypatch.setattr("job.DATA_FILE", str(local_file))
    monkeypatch.setattr("job.JOB_ALLOW_LOCAL_FALLBACK", True)

    fake_bot = SimpleNamespace(guilds=[SimpleNamespace(id=1)], user=SimpleNamespace(id=777, name="bot"))
    cog = JobCog(fake_bot)

    async def fake_load_from_console(_guild):
        return False

    cog.load_from_console = fake_load_from_console
    cog.migrate_legacy_keys = AsyncMock()

    await cog.initialize_data()

    assert cog.jobs_data["123"]["name"] == "Local"
    assert cog.jobs_data["123"]["jobs"]["Mineur"] == 20
    cog.migrate_legacy_keys.assert_awaited_once_with()
