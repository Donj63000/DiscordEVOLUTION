#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for normalising OpenAI model names from environment variables."""

from __future__ import annotations

import os
from typing import Any, Mapping


def _clean_env(value: str, prefixes: tuple[str, ...] | None = None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if prefixes is None:
        return raw
    lowered = raw.lower()
    if any(lowered.startswith(prefix) for prefix in prefixes):
        return raw
    return ""


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


def build_async_openai_client(client_cls: Any, *, timeout: float | None = None) -> Any:
    if client_cls is None:
        return None
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    project = _clean_env(os.getenv("OPENAI_PROJECT") or os.getenv("OPENAI_PROJECT_ID") or "", ("proj-", "proj_"))
    organization = _clean_env(os.getenv("OPENAI_ORG_ID") or os.getenv("OPENAI_ORGANIZATION") or "", ("org-", "org_"))
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip()
    kwargs: dict[str, Any] = {"api_key": api_key}
    if project:
        kwargs["project"] = project
    if organization:
        kwargs["organization"] = organization
    if base_url:
        kwargs["base_url"] = base_url
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        return client_cls(**kwargs)
    except TypeError:
        kwargs.pop("project", None)
        return client_cls(**kwargs)
