from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from iastaff import IAStaff


class FakeMember:
    def __init__(self, member_id, display_name):
        self.id = member_id
        self.display_name = display_name
        self.name = display_name


class FakeGuild:
    def __init__(self, members=None):
        self._members = members or []

    def get_member(self, member_id):
        for member in self._members:
            if member.id == member_id:
                return member
        return None

    @property
    def members(self):
        return list(self._members)


class FakeContext:
    def __init__(self, members=None):
        self.channel = SimpleNamespace(id=777)
        self.author = SimpleNamespace(id=111)
        self.guild = FakeGuild(members or [])
        self.invoke = AsyncMock()


class StubJobCog:
    def __init__(self):
        self.jobs_data = {}

    async def load_from_console(self, guild):
        self.last_guild = guild

    def resolve_job_name(self, name):
        low = (name or "").lower()
        if "bijou" in low:
            return "Bijoutier"
        if "paysan" in low:
            return "Paysan"
        return None

    def suggest_similar_jobs(self, name, limit=6):
        return ["Bijoutier"]

    def get_user_jobs(self, user_id, user_name=None):
        entry = self.jobs_data.get(str(user_id))
        if entry and "jobs" in entry:
            return entry["jobs"]
        if user_name:
            lowered = user_name.lower()
            for data in self.jobs_data.values():
                if data.get("name", "").lower() == lowered:
                    return data.get("jobs", {})
        return {}


@pytest.fixture
def iastaff_tools_cog(monkeypatch):
    monkeypatch.setattr("iastaff.AsyncOpenAI", None)
    bot = SimpleNamespace(
        guilds=[],
        get_channel=lambda _: None,
    )
    bot.wait_until_ready = AsyncMock(return_value=None)
    bot.is_ready = lambda: True
    bot.get_command = lambda name: None
    cog = IAStaff(bot)
    cog.enable_tools = True
    return cog


@pytest.mark.asyncio
async def test_dispatch_command_tool_create_activity_invokes_command(iastaff_tools_cog):
    ctx = FakeContext()
    dummy_command = object()
    iastaff_tools_cog.bot.get_command = lambda name: dummy_command if name == "activite" else None

    ack = await iastaff_tools_cog._dispatch_command_tool(
        ctx,
        "create_activity",
        {"title": "Donjon Blop", "datetime": "16/11/2025 21:00", "description": "6 places"},
    )

    ctx.invoke.assert_awaited_once_with(
        dummy_command,
        action="creer",
        args="Donjon Blop 16/11/2025 21:00 6 places",
    )
    assert "Donjon Blop" in ack


@pytest.mark.asyncio
async def test_dispatch_command_tool_missing_command_raises(iastaff_tools_cog):
    ctx = FakeContext()
    iastaff_tools_cog.bot.get_command = lambda name: None

    with pytest.raises(RuntimeError):
        await iastaff_tools_cog._dispatch_command_tool(ctx, "start_organisation", {})


@pytest.mark.asyncio
async def test_try_chat_with_tools_runs_dispatch_when_tool_call_present(iastaff_tools_cog):
    ctx = FakeContext()
    tool_call = SimpleNamespace(function=SimpleNamespace(name="list_activities", arguments="{}"))
    fake_resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[tool_call]))])
    create_mock = AsyncMock(return_value=fake_resp)
    iastaff_tools_cog.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )
    iastaff_tools_cog._dispatch_command_tool = AsyncMock(return_value="Liste envoyée.")

    messages = [{"role": "user", "content": [{"type": "input_text", "text": "Montre les activités"}]}]
    result = await iastaff_tools_cog._try_chat_with_tools(ctx, messages)

    assert result == "Liste envoyée."
    iastaff_tools_cog._dispatch_command_tool.assert_awaited_once_with(ctx, "list_activities", {})


@pytest.mark.asyncio
async def test_dispatch_command_tool_show_warnings_resolves_member(iastaff_tools_cog):
    member = FakeMember(222, "ModZero")
    ctx = FakeContext(members=[member])
    warnings_cmd = object()
    iastaff_tools_cog.bot.get_command = lambda name: warnings_cmd if name == "warnings" else None

    ack = await iastaff_tools_cog._dispatch_command_tool(
        ctx,
        "show_warnings",
        {"member": "<@222>"},
    )

    ctx.invoke.assert_awaited_once_with(warnings_cmd, member=member)
    assert "ModZero" in ack


@pytest.mark.asyncio
async def test_dispatch_command_tool_stats_enable_handles_group_command(iastaff_tools_cog):
    ctx = FakeContext()
    stats_on_cmd = object()
    iastaff_tools_cog.bot.get_command = lambda name: stats_on_cmd if name == "stats on" else None

    ack = await iastaff_tools_cog._dispatch_command_tool(ctx, "stats_enable", {})

    ctx.invoke.assert_awaited_once_with(stats_on_cmd)
    assert "activée" in ack


@pytest.mark.asyncio
async def test_dispatch_command_tool_job_lookup_profession_passes_positionals(iastaff_tools_cog):
    ctx = FakeContext()
    job_cmd = object()
    iastaff_tools_cog.bot.get_command = lambda name: job_cmd if name == "job" else None
    iastaff_tools_cog._summarize_job_profession = AsyncMock(return_value="Il y a 2 bijoutiers.")

    ack = await iastaff_tools_cog._dispatch_command_tool(
        ctx,
        "job_lookup_profession",
        {"profession": "paysan"},
    )

    ctx.invoke.assert_awaited_once_with(job_cmd, "paysan")
    assert "- `!job paysan`" in ack
    assert "Il y a 2 bijoutiers." in ack


@pytest.mark.asyncio
async def test_dispatch_command_tool_start_event_requires_details(iastaff_tools_cog):
    ctx = FakeContext()
    event_cmd = object()
    iastaff_tools_cog.bot.get_command = lambda name: event_cmd if name == "event" else None

    with pytest.raises(RuntimeError):
        await iastaff_tools_cog._dispatch_command_tool(ctx, "start_event", {"title": "Raid"})


@pytest.mark.asyncio
async def test_dispatch_command_tool_start_event_formats_summary(iastaff_tools_cog):
    ctx = FakeContext()
    event_cmd = object()
    iastaff_tools_cog.bot.get_command = lambda name: event_cmd if name == "event" else None

    ack = await iastaff_tools_cog._dispatch_command_tool(
        ctx,
        "start_event",
        {"title": "Raid Café", "date_time": "Samedi 21h", "description": "Farm de guilde."},
    )

    ctx.invoke.assert_awaited_once_with(event_cmd)
    assert "Commandes exécutées" in ack
    assert "Raid Café" in ack
    assert "Samedi 21h" in ack


def test_format_command_summary_includes_commands(iastaff_tools_cog):
    summary = iastaff_tools_cog._format_command_summary(["!job liste"], "OK")
    assert "Commandes exécutées" in summary
    assert "- `!job liste`" in summary
    assert "OK" in summary


@pytest.mark.asyncio
async def test_summarize_job_profession_lists_members(iastaff_tools_cog):
    member = FakeMember(222, "ModZero")
    ctx = FakeContext(members=[member])
    stub = StubJobCog()
    stub.jobs_data = {
        str(member.id): {"name": member.display_name, "jobs": {"Bijoutier": 80}},
        "custom": {"name": "Gamma", "jobs": {"Bijoutier": 40}},
    }
    iastaff_tools_cog.bot.get_cog = lambda name: stub if name == "JobCog" else None

    summary = await iastaff_tools_cog._summarize_job_profession(ctx, "bijoutier")

    assert "Il y a **2**" in summary
    assert "ModZero" in summary
    assert "Gamma" in summary


@pytest.mark.asyncio
async def test_summarize_job_player_uses_member_lookup(iastaff_tools_cog):
    member = FakeMember(333, "Crafter")
    ctx = FakeContext(members=[member])
    stub = StubJobCog()
    stub.jobs_data = {
        str(member.id): {"name": member.display_name, "jobs": {"Bijoutier": 90, "Paysan": 50}}
    }
    iastaff_tools_cog.bot.get_cog = lambda name: stub if name == "JobCog" else None

    summary = await iastaff_tools_cog._summarize_job_player(ctx, "<@333>")

    assert "Crafter" in summary
    assert "Bijoutier" in summary
    assert "Paysan" in summary
