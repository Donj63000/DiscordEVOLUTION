import logging

from utils import openai_config


def test_normalise_staff_model_handles_aliases():
    assert openai_config.normalise_staff_model("  GPT5 mini  ") == "gpt-5-mini"
    assert openai_config.normalise_staff_model("gpt-5") == "gpt-5"


def test_resolve_reasoning_effort_only_for_gpt5(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "LOW")
    result = openai_config.resolve_reasoning_effort("gpt-5-mini")
    assert result == {"effort": "low"}
    assert openai_config.resolve_reasoning_effort("gpt-4o-mini") is None
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)


def test_resolve_reasoning_effort_invalid_value(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "extreme")
    caplog.set_level(logging.WARNING)

    assert openai_config.resolve_reasoning_effort("gpt-5") is None
    assert "OPENAI_REASONING_EFFORT" in caplog.text
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
