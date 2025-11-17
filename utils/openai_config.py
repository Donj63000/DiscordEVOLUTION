#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for normalising OpenAI model names from environment variables."""

from __future__ import annotations

import os
import logging
from typing import Any, Mapping

log = logging.getLogger(__name__)


STAFF_MODEL_ALIASES: dict[str, str] = {
    "gpt5": "gpt-5",
    "gpt-5.0": "gpt-5",
    "gpt-5": "gpt-5",
    "gpt5-mini": "gpt-5-mini",
    "gpt-5-mini": "gpt-5-mini",
    "gpt-5m": "gpt-5-mini",
    "gpt5m": "gpt-5-mini",
    "mini-5": "gpt-5-mini",
}

ALLOWED_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}


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


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


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
    raw = (os.getenv("OPENAI_STAFF_MODEL") or "").strip()
    if not raw:
        return default
    resolved = normalise_staff_model(raw, default=default)
    return resolved or default


def normalise_staff_model(value: str, default: str | None = None) -> str:
    raw = (value or "").strip()
    if not raw:
        return default or ""
    key = _normalise_model_name(raw)
    resolved = STAFF_MODEL_ALIASES.get(key, key)
    return resolved or (default or "")


def build_async_openai_client(client_cls: Any, *, timeout: float | None = None) -> Any:
    if client_cls is None:
        return None
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    project = _clean_env(os.getenv("OPENAI_PROJECT") or os.getenv("OPENAI_PROJECT_ID") or "", ("proj-", "proj_"))
    organization_raw = (os.getenv("OPENAI_ORG_ID") or os.getenv("OPENAI_ORGANIZATION") or "").strip()
    organization = _clean_env(organization_raw, ("org-", "org_"))
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip()
    kwargs: dict[str, Any] = {"api_key": api_key}
    if project:
        kwargs["project"] = project
    use_org = organization and _truthy(os.getenv("OPENAI_FORCE_ORG"))
    if organization and not use_org:
        log.debug(
            "OPENAI_ORG_ID/OPENAI_ORGANIZATION provided but ignored. "
            "Set OPENAI_FORCE_ORG=1 to force the header when needed."
        )
    if use_org:
        kwargs["organization"] = organization
    if base_url:
        kwargs["base_url"] = base_url
    if timeout is not None:
        kwargs["timeout"] = timeout
    removed_org_env: list[tuple[str, str]] = []
    try:
        if organization_raw:
            for key in ("OPENAI_ORG_ID", "OPENAI_ORGANIZATION"):
                value = os.environ.pop(key, None)
                if value is not None:
                    removed_org_env.append((key, value))
        try:
            return client_cls(**kwargs)
        except TypeError:
            kwargs.pop("project", None)
            return client_cls(**kwargs)
    finally:
        for key, value in removed_org_env:
            os.environ[key] = value


def resolve_reasoning_effort(model: str) -> dict[str, str] | None:
    effort = (os.getenv("OPENAI_REASONING_EFFORT") or "").strip().lower()
    if not effort or not model:
        return None
    if not model.strip().lower().startswith("gpt-5"):
        return None
    if effort not in ALLOWED_REASONING_EFFORTS:
        log.warning(
            "OPENAI_REASONING_EFFORT=%s ignoré. Valeurs acceptées: %s",
            effort,
            ", ".join(sorted(ALLOWED_REASONING_EFFORTS)),
        )
        return None
    return {"effort": effort}
