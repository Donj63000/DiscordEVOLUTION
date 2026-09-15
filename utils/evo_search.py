"""Hosted OpenAI search configuration and provenance; never a local arbitrary fetch."""
from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import re
from urllib.parse import urlsplit

from utils.evo_config import EvoError
from utils.evo_safety import clean

# Native search does not execute HTTP requests to result URLs on the bot host.
RETRO_DOMAINS = (
    "wiki.moon-bot.io", "xixou.io", "dofus-retro.com",
    "support.ankama.com", "dofus.jeuxonline.info",
)
# Search content is additional input, not counted by /responses/input_tokens.
# This is conservative reservation headroom, NOT a provider-side hard token cap.
SEARCH_INPUT_HEADROOM = 16_000


def search_tool(scope: str) -> dict:
    if scope not in {"dofus_retro", "general"}:
        raise EvoError("Périmètre de recherche inconnu.")
    tool = {"type": "web_search", "search_context_size": "low"}
    if scope == "dofus_retro":
        tool["filters"] = {"allowed_domains": list(RETRO_DOMAINS)}
    return tool


def web_call_count(response: dict) -> int:
    outputs = response.get("output")
    if not isinstance(outputs, list):
        raise EvoError("Résultat de recherche Web invalide.")
    return sum(isinstance(item, dict) and item.get("type") == "web_search_call" for item in outputs)


def citation_url(value: object) -> str:
    """Validate display-only links. This grants no permission to fetch their host."""
    if (not isinstance(value, str) or len(value) > 350 or clean(value, 350) != value
            or re.search(r"[\s\\<>\[\]\"'()]", value)):
        return ""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or "." not in host
                or host.endswith((".local", ".internal", ".localhost", ".test"))):
            return ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return ""  # IP literals are unnecessary as public documentary citations.
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split(".")):
            return ""
        # Prevent a model/search result from distributing credential or webhook URLs.
        if re.search(r"(?:token|api[_-]?key|password|secret|signature)=", parsed.query, re.I):
            return ""
        if host in {"discord.com", "discordapp.com"} and "/api/webhooks/" in parsed.path:
            return ""
        return value
    except ValueError:
        return ""


def search_result(response: dict, scope: str) -> dict:
    outputs = response.get("output", [])
    calls = [item for item in outputs if isinstance(item, dict) and item.get("type") == "web_search_call"]
    if len(calls) != 1 or calls[0].get("status") != "completed":
        raise EvoError("La recherche Web n'a pas fourni de recherche terminée ; aucun fait confirmé.")
    if any(not isinstance(item, dict) or item.get("type") not in
           {"reasoning", "web_search_call", "message"} for item in outputs):
        raise EvoError("Résultat Web inattendu.")
    sources, texts = {}, []
    for item in outputs:
        if item.get("type") != "message":
            continue
        for block in item.get("content", []):
            if not isinstance(block, dict) or block.get("type") != "output_text":
                continue
            cited = False
            for annotation in block.get("annotations", []):
                if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                    continue
                url = citation_url(annotation.get("url"))
                if not url:
                    continue
                if scope == "dofus_retro":
                    host = urlsplit(url).hostname
                    if not any(host == domain or host.endswith("." + domain) for domain in RETRO_DOMAINS):
                        continue
                sources.setdefault(url, {"titre": clean(annotation.get("title"), 100), "url": url})
                cited = True
            # A message with no provider citation is not Web evidence.
            if cited and isinstance(block.get("text"), str):
                text = re.sub(r".*?", "", block["text"])
                texts.append(clean(text, 2600))
    if not sources or not texts:
        raise EvoError("Aucune source Web vérifiable reçue. Je ne peux pas confirmer cette information.")
    text = "\n".join(texts)
    return {
        "extraits": [text[i:i + 380] for i in range(0, min(len(text), 2280), 380)],
        "sources": list(sources.values())[:5],
        "consulte_le": datetime.now(timezone.utc).isoformat(),
        "jeu": "Dofus Rétro" if scope == "dofus_retro" else "Sujet général",
        "limites": "Synthèse sourcée du moteur de recherche, pas une extraction structurée de fiche. "
                   "Vérifier la version du jeu, la date des articles et les contradictions avec les API.",
    }
