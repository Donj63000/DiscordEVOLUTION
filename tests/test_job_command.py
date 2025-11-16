import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
