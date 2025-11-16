#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from utils.ticket_text import TICKET_FIELD_MAX_CHARS, format_ticket_body, sanitize_ticket_text


class DummyAttachment:
    def __init__(self, filename: str, url: str):
        self.filename = filename
        self.url = url


class DummyMessage:
    def __init__(self, content: str = "", attachments: list | None = None):
        self.content = content
        self.attachments = attachments or []


def test_sanitize_ticket_text_strips_mentions_and_whitespace():
    raw = "@everyone  Salut   \n\n\nEquipe"
    cleaned = sanitize_ticket_text(raw)
    assert "@everyone" not in cleaned
    assert "\n\n\n" not in cleaned


def test_format_ticket_body_includes_attachments():
    message = DummyMessage(
        content="@here bug trouvé",
        attachments=[DummyAttachment("capture.png", "https://cdn/files/capture.png")],
    )
    result = format_ticket_body(message)
    assert "@here" not in result
    assert "capture.png" in result
    assert "https://cdn/files/capture.png" in result


def test_format_ticket_body_is_truncated_to_embed_limit():
    long_text = "x" * (TICKET_FIELD_MAX_CHARS + 200)
    message = DummyMessage(content=long_text)
    result = format_ticket_body(message)
    assert len(result) <= TICKET_FIELD_MAX_CHARS
    assert result.endswith("...")
