"""Contrat des anciennes et nouvelles sauvegardes métiers."""
from types import SimpleNamespace as NS

import pytest

from tests_jobs.helpers import Attachment, encoded, header
from utils.job_snapshot import (
    InvalidJobSnapshot, JobSnapshotReader, MAX_SNAPSHOT_BYTES, decode_jobs_payload,
)

DATA = {
    "101": {"name": "Artisan à tester", "jobs": {"Mineur": 100, "Tailleur": 80}},
    "ancienne-fiche": {"name": "Ancienne fiche", "jobs": {"Bûcheron": 90}},
}


def reader_message(*, content=None, attachments=None, author=900):
    reader = JobSnapshotReader(NS(user=NS(id=900)))
    message = NS(
        id=10, author=NS(id=author), webhook_id=None,
        content=header(DATA) if content is None else content,
        attachments=[Attachment(encoded(DATA))] if attachments is None else attachments,
    )
    return reader, message


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", " (fichier)", " \n"])
async def test_attachment_does_not_require_word_fichier(suffix):
    reader, message = reader_message(content=header(DATA, suffix))
    assert await reader.extract_payload(message) == DATA
    assert message.attachments[0].reads == 1


@pytest.mark.asyncio
async def test_legacy_attachment_without_marker_is_accepted_only_from_this_bot():
    reader, message = reader_message(content="")
    assert await reader.extract_payload(message) == DATA
    message.author.id = 901
    assert await reader.extract_payload(message) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["json", ""])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
async def test_inline_legacy_formats(label, newline):
    content = f"===BOTJOBS==={newline}```{label}{newline}{encoded(DATA).decode()}{newline}```"
    reader, message = reader_message(content=content, attachments=[])
    assert await reader.extract_payload(message) == DATA


@pytest.mark.asyncio
async def test_utf8_bom_is_supported_without_altering_names_or_levels():
    reader, message = reader_message(attachments=[Attachment(b"\xef\xbb\xbf" + encoded(DATA))])
    assert await reader.extract_payload(message) == DATA


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["other_user", "webhook", "unidentified_bot", "quoted_marker"])
async def test_untrusted_messages_are_not_used(kind):
    reader, message = reader_message()
    if kind == "other_user":
        message.author.id = 123
    elif kind == "webhook":
        message.webhook_id = 55
    elif kind == "unidentified_bot":
        reader.bot.user = None
    else:
        message.attachments = []
        message.content = "Exemple de ===BOTJOBS===\n```json\n{}\n```"
    assert await reader.extract_payload(message) is None


@pytest.mark.asyncio
async def test_author_identity_is_compared_by_id_not_display_name():
    reader, message = reader_message()
    message.author.name = "ancien nom du bot"
    reader.bot.user.name = "nouveau nom du bot"
    assert await reader.extract_payload(message) == DATA


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"{", b"\xff", b"[]", b'{"101":null}', b'{"101":{"jobs":[]}}'])
async def test_invalid_attachment_never_falls_back_to_inline_preview(raw):
    reader, message = reader_message(
        content=header(DATA) + "\n```json\n{}\n```",
        attachments=[Attachment(raw)],
    )
    with pytest.raises(InvalidJobSnapshot):
        await reader.extract_payload(message)


@pytest.mark.asyncio
async def test_attachment_transport_failure_is_not_treated_as_empty():
    attachment = Attachment(encoded(DATA))
    attachment.error = OSError("connexion interrompue")
    reader, message = reader_message(attachments=[attachment])
    with pytest.raises(OSError):
        await reader.extract_payload(message)


@pytest.mark.asyncio
async def test_announced_missing_file_is_not_replaced_by_a_preview():
    reader, message = reader_message(
        content="===BOTJOBS=== (fichier)\n```json\n{}\n```", attachments=[],
    )
    with pytest.raises(InvalidJobSnapshot, match="attaché"):
        await reader.extract_payload(message)


@pytest.mark.asyncio
async def test_multiple_matching_attachments_are_rejected():
    reader, message = reader_message(attachments=[Attachment(encoded(DATA)), Attachment(b"{}")])
    with pytest.raises(InvalidJobSnapshot, match="ambigu"):
        await reader.extract_payload(message)


@pytest.mark.asyncio
async def test_oversized_attachment_is_rejected_before_download():
    reader, message = reader_message()
    message.attachments[0].size = MAX_SNAPSHOT_BYTES + 1
    with pytest.raises(InvalidJobSnapshot, match="8 Mio"):
        await reader.extract_payload(message)
    assert message.attachments[0].reads == 0


@pytest.mark.asyncio
async def test_etag_mismatch_is_rejected():
    reader, message = reader_message(content="===BOTJOBS=== etag:" + "0" * 32)
    with pytest.raises(InvalidJobSnapshot, match="etag"):
        await reader.extract_payload(message)


@pytest.mark.asyncio
async def test_invalid_unicode_escape_is_rejected_even_without_etag():
    reader, message = reader_message(
        content="===BOTJOBS===",
        attachments=[Attachment(b'{"101":{"name":"\\ud800","jobs":{}}}')],
    )
    with pytest.raises(InvalidJobSnapshot):
        await reader.extract_payload(message)


@pytest.mark.parametrize("raw", [
    b'{"101":{},"101":{}}',
    b'{"101":{"jobs":{"Mineur":50,"Mineur":100}}}',
    b'{"101":{"jobs":{"Mineur":NaN}}}',
    b'{"101":{"jobs":{"Mineur":Infinity}}}',
])
def test_json_ambiguities_are_not_silently_normalized(raw):
    with pytest.raises(InvalidJobSnapshot):
        decode_jobs_payload(raw)


@pytest.mark.asyncio
async def test_explicit_empty_snapshot_is_distinct_from_missing_payload():
    reader, message = reader_message(content="===BOTJOBS===", attachments=[Attachment(b"{}")])
    assert await reader.extract_payload(message) == {}
    message.attachments = []
    with pytest.raises(InvalidJobSnapshot):
        await reader.extract_payload(message)
