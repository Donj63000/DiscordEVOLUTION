import types
from datetime import datetime, timedelta
import pytest

from ia import IACog, IASession

class DummyChat:
    def __init__(self):
        self.history = []
        self.last = types.SimpleNamespace(text="dummy")
    def send_message(self, content):
        self.history.append(content)
        self.last.text = "reply"

class DummyCtx:
    def __init__(self, guild=None):
        self.guild = guild
        self.author = types.SimpleNamespace(id=1)
        self.channel = types.SimpleNamespace(id=2)
        self.replies = []
    async def reply(self, content, mention_author=False):
        self.replies.append(content)

class DummyChannel:
    def __init__(self):
        self.last_reply = None
    async def send(self, content, mention_author=False):
        self.last_reply = content
    async def typing(self):
        class _T:
            async def __aenter__(self):
                return None
            async def __aexit__(self, exc_type, exc, tb):
                return None
        return _T()

class DummyMessage:
    def __init__(self):
        self.content = "hello"
        self.author = types.SimpleNamespace(id=1, bot=False)
        self.channel = DummyChannel()
    async def reply(self, content, mention_author=False):
        self.channel.last_reply = content

@pytest.mark.asyncio
async def test_dm_session_uses_pro_model(monkeypatch):
    cog = IACog(bot=object())
    monkeypatch.setattr(cog, "_new_chat", lambda *a, **k: DummyChat())
    ctx = DummyCtx(guild=None)
    await cog.ia_start_command(ctx)
    assert ctx.author.id in cog.sessions
    assert cog.sessions[ctx.author.id].model_name == "gemini-pro-2.5"

@pytest.mark.asyncio
async def test_quota_bascule_to_flash(monkeypatch):
    cog = IACog(bot=object())
    session = IASession(
        model_name="gemini-pro-2.5",
        chat=DummyChat(),
        start_ts=datetime.utcnow(),
        last_activity=datetime.utcnow(),
    )
    cog.sessions[1] = session
    monkeypatch.setattr(cog, "_new_chat", lambda *a, **k: DummyChat())
    msg = DummyMessage()
    await cog._handle_quota_and_retry(session, msg)
    assert session.model_name == "gemini-1.5-flash"
    assert "Flash" in msg.channel.last_reply

@pytest.mark.asyncio
async def test_purge_expired_session_removes():
    cog = IACog(bot=object())
    past = datetime.utcnow() - timedelta(minutes=61)
    session = IASession(
        model_name="gemini-1.5-flash",
        chat=DummyChat(),
        start_ts=past,
        last_activity=past,
    )
    cog.sessions[1] = session
    await cog.purge_expired_sessions()
    assert not cog.sessions
