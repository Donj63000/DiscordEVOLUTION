#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for normalising OpenAI model names from environment variables."""

from __future__ import annotations

import os
from typing import Mapping


def _normalise_model_name(value: str) -> str:
    """Lowercase the model id and standardise separators."""
    lowered = value.strip().lower()
    return lowered.replace(' ', '-').replace('_', '-').replace('--', '-')


def resolve_openai_model(env_var: str, default: str, aliases: Mapping[str, str] | None = None) -> str:
    """Return a canonical model id for the given environment variable."""
    raw = (os.getenv(env_var) or '').strip()
    if not raw:
        return default
    key = _normalise_model_name(raw)
    alias_map = aliases or {}
    if key in alias_map:
        mapped = alias_map[key]
        return mapped or default
    return key


def resolve_staff_model(default: str = "gpt-4o-mini") -> str:
    aliases = {
        "gpt5": "gpt-5",
        "gpt-5.0": "gpt-5",
        "gpt-5": "gpt-5",
    }
    return resolve_openai_model("OPENAI_STAFF_MODEL", default, aliases)
