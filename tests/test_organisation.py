﻿import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import organisation


class DummyResponses:
    def __init__(self, payloads):
        self._payloads = list(payloads)

    async def create(self, **kwargs):
        if not self._payloads:
            raise AssertionError("No more payloads prepared")
        payload = self._payloads.pop(0)
        return SimpleNamespace(output_text=json.dumps(payload), output=[])


class DummyClient:
    def __init__(self, payloads):
        self.responses = DummyResponses(payloads)


@pytest.mark.asyncio
async def test_planner_step_collects_and_ready(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    cog = organisation.OrganisationCog(bot=MagicMock())
    cog._client = DummyClient([
        {
            "status": "ask",
            "next_question": "Quand souhaites-tu lancer cet evenement ?",
            "collected": {"event_type": "Donjon"},
            "summary": None,
        },
        {
            "status": "ready",
            "next_question": None,
            "collected": {"date_time": "Samedi 20h"},
            "summary": "Sortie donjon samedi 20h",
        },
    ])
    session = organisation.OrganisationSession(
        user_id=1,
        guild_id=1,
        channel_id=123,
        context={"guild": "Evolution", "organiser": "Staff"},
    )

    payload = await cog._planner_step(session, initial=True)
    assert payload["status"] == "ask"
    assert session.collected["event_type"] == "Donjon"
    assert session.last_question.startswith("Quand souhaites-tu")

    payload = await cog._planner_step(session, user_message="Samedi 20h")
    assert payload["status"] == "ready"
    assert session.collected["date_time"] == "Samedi 20h"
    assert session.summary == "Sortie donjon samedi 20h"


@pytest.mark.asyncio
async def test_generate_announcement_payload(monkeypatch):
    monkeypatch.setattr(organisation, "AsyncOpenAI", None)
    cog = organisation.OrganisationCog(bot=MagicMock())
    cog._client = DummyClient([
        {
            "title": "Sortie Donjon",
            "body": "Rendez-vous samedi 20h a Astrub pour enchaine les donjons.",
            "cta": "Inscris-toi sur le canal organisation",
            "mentions": "@here",
            "summary": "Samedi 20h - donjon organise par Staff",
        }
    ])
    session = organisation.OrganisationSession(
        user_id=1,
        guild_id=1,
        channel_id=123,
        context={"guild": "Evolution", "organiser": "Staff"},
        collected={"event_type": "Donjon", "date_time": "Samedi 20h"},
        summary="Sortie donjon samedi 20h",
    )

    payload = await cog._generate_announcement(
        session,
        organiser="Staff",
        channel=SimpleNamespace(name="organisation"),
    )

    assert payload["title"] == "Sortie Donjon"
    ctx = SimpleNamespace(author=SimpleNamespace(display_name="Staff"))
    mentions, embed = cog._format_announcement(ctx, payload)
    assert mentions == "@here"
    assert embed.title == "Sortie Donjon"
    assert "Rendez-vous" in embed.description
