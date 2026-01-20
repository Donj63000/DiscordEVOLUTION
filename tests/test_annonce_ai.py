import pytest

from cogs.annonce_ai import AnnounceAICog


def _make_payload() -> str:
    return (
        '{"variants":['
        '{"style":"Bref","title":"A","description":"B"},'
        '{"style":"Standard","title":"C","description":"D"},'
        '{"style":"RP","title":"E","description":"F"}'
        ']}'
    )


def test_parse_variants_payload_from_codeblock():
    cog = object.__new__(AnnounceAICog)
    payload = _make_payload()
    text = f"```json\n{payload}\n```"
    data = cog._parse_variants_payload(text)
    assert "variants" in data
    assert len(data["variants"]) == 3


def test_parse_variants_payload_from_wrapped_text():
    cog = object.__new__(AnnounceAICog)
    payload = _make_payload()
    text = f"Voici la reponse demandee:\n{payload}\nMerci."
    data = cog._parse_variants_payload(text)
    assert data["variants"][0]["style"] == "Bref"


def test_parse_variants_payload_raises_on_invalid():
    cog = object.__new__(AnnounceAICog)
    with pytest.raises(RuntimeError, match="JSON illisible"):
        cog._parse_variants_payload("{\"variants\":[")
