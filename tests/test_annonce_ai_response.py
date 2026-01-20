from types import SimpleNamespace
import json

from cogs import annonce_ai


def test_extract_response_text_prefers_output_text():
    resp = SimpleNamespace(output_text=" bonjour ", output=[])
    assert annonce_ai._extract_response_text(resp) == "bonjour"


def test_extract_response_text_reads_output_json():
    payload = {"variants": [{"style": "A", "title": "B", "description": "C"}]}
    resp = SimpleNamespace(
        output=[{"content": [{"type": "output_json", "json": payload}]}],
        output_text="",
    )
    text = annonce_ai._extract_response_text(resp)
    assert json.loads(text) == payload
