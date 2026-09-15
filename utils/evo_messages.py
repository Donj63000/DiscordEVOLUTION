"""Découpage Discord pur, sans réseau, en unités UTF-16."""

import re


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def split_messages(text: str, limit: int = 1900) -> list[str]:
    """Preserve all content, leave safety room and close/reopen fenced code."""
    if not isinstance(text, str) or type(limit) is not int or not 100 <= limit <= 1950:
        raise ValueError("Texte ou limite Discord invalide.")
    rest = text.strip()
    parts = []
    fenced = False
    while rest:
        prefix = "```\n" if fenced else ""
        room = limit - utf16_length(prefix) - 5  # closing fence reserve
        candidate = rest.encode("utf-16-le")[:room * 2].decode("utf-16-le", errors="ignore")
        if len(candidate) < len(rest):
            # Keep URLs/Markdown together when a nearby boundary is available.
            boundary = max(candidate.rfind("\n"), candidate.rfind(" "))
            if boundary >= len(candidate) * 0.85:
                candidate = candidate[:boundary]
            # Never cut a verified URL or a short Markdown citation across messages.
            for match in re.finditer(r"\[[^\]\n]{0,150}\]\(https?://[^)\s]+\)|https?://[^\s]+", rest):
                if match.start() < len(candidate) < match.end() and match.start() >= len(candidate) // 2:
                    candidate = rest[:match.start()].rstrip()
                    break
            # Never split a run of three backticks between messages.
            if candidate.endswith("`") and rest[len(candidate):].startswith("`"):
                candidate = candidate.rstrip("`")
        if not candidate:
            raise ValueError("Découpage Discord impossible.")
        rest = rest[len(candidate):].lstrip()
        if candidate.count("```") % 2:
            fenced = not fenced
        part = prefix + candidate.rstrip() + ("\n```" if fenced else "")
        if utf16_length(part) > limit:
            raise ValueError("Message Discord trop long.")
        parts.append(part)
    return parts or ["…"]
