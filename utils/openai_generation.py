"""Appels texte OpenAI bornés pour les assistants staff et annonces."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Mapping

from utils.openai_config import resolve_reasoning_effort


log = logging.getLogger(__name__)


class OpenAIGenerationError(RuntimeError):
    """Message public contrôlé, sans corps d'erreur du fournisseur."""


def generation_options(
    model: str,
    max_output_tokens: int,
    temperature: float,
    *,
    chat: bool = False,
    tool_calling: bool = False,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "max_completion_tokens" if chat else "max_output_tokens": max_output_tokens,
        "store": False,
    }
    name = model.strip().lower()
    reasoning = resolve_reasoning_effort(name)
    # Les petits budgets Discord incluent les tokens de raisonnement : un effort
    # faible laisse plus de place au texte utile, sans changer le modèle choisi.
    if reasoning is None and re.fullmatch(r"gpt-5(?:-mini|-nano)?(?:-\d{4}-\d{2}-\d{2})?", name):
        reasoning = {"effort": "low"}
    if chat and tool_calling:
        if re.match(r"^gpt-(?:6-astra|6\.1-sol)(?:-|$)", name):
            raise OpenAIGenerationError(
                "Ce modèle exige Responses pour les outils Discord. "
                "Fais choisir au staff un modèle compatible avec le routeur Chat "
                "ou désactiver les outils IA Staff pour les échanges de texte."
            )
        version = re.match(r"^gpt-5\.(\d+)(?:-|$)", name)
        if (version and int(version.group(1)) >= 4) or re.fullmatch(
            r"gpt-6-(?:sol|luna)(?:-\d{4}-\d{2}-\d{2})?", name
        ):
            # À partir de GPT-5.4, les fonctions Chat nécessitent none. Cette
            # restriction ne concerne ni Responses ni les anciennes générations.
            reasoning = {"effort": "none"}
    if reasoning:
        if chat:
            options["reasoning_effort"] = reasoning["effort"]
        else:
            options["reasoning"] = reasoning
    # Ces familles n'acceptent pas toutes temperature avec le raisonnement actif.
    # L'omettre préserve leur valeur par défaut et évite un rejet HTTP 400.
    if not name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        options["temperature"] = temperature
    return options


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _message_text(message: Any) -> str:
    content = _field(message, "content", [])
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, (list, tuple)):
        return ""
    texts = []
    for part in content:
        if _field(part, "type") not in {"output_text", "text"}:
            continue
        value = _field(part, "text", "")
        if not isinstance(value, str):
            value = _field(value, "value", "")
        if isinstance(value, str) and value.strip():
            texts.append(value.strip())
    return "\n".join(texts)


def extract_generated_text(response: Any) -> str:
    """Lire uniquement le texte assistant, jamais les arguments/outils/raisonnements."""
    direct = _field(response, "output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    texts = []
    for item in _field(response, "output", []) or []:
        if _field(item, "type", "message") != "message":
            continue
        text = _message_text(item)
        if text:
            texts.append(text)
    if texts:
        return "\n".join(texts)
    choices = _field(response, "choices", []) or []
    if choices:
        return _message_text(_field(choices[0], "message"))
    return ""


def validate_response(response: Any) -> None:
    """Une sortie tronquée ne doit pas être publiée ou exécutée comme complète."""
    status = _field(response, "status")
    if status == "incomplete":
        raise OpenAIGenerationError("La réponse IA est incomplète. Demande une réponse plus courte.")
    if status in {"failed", "cancelled", "queued", "in_progress"} or _field(response, "error"):
        raise OpenAIGenerationError("Le service IA n'a pas terminé la génération. Réessaie plus tard.")
    for choice in _field(response, "choices", []) or []:
        finish_reason = _field(choice, "finish_reason")
        if finish_reason == "length":
            raise OpenAIGenerationError("La réponse IA est incomplète. Demande une réponse plus courte.")
        if finish_reason == "content_filter" or _field(_field(choice, "message"), "refusal"):
            raise OpenAIGenerationError("Le service IA n'a pas pu répondre à cette demande. Reformule-la.")
    for item in _field(response, "output", []) or []:
        for part in _field(item, "content", []) or []:
            if _field(part, "type") == "refusal":
                raise OpenAIGenerationError("Le service IA n'a pas pu répondre à cette demande. Reformule-la.")


def require_generated_text(response: Any) -> str:
    validate_response(response)
    text = extract_generated_text(response)
    if not text:
        raise OpenAIGenerationError("Le service IA a renvoyé une réponse vide. Reformule ta demande.")
    return text


def public_openai_error(exc: Exception) -> OpenAIGenerationError:
    """Conserver un diagnostic opérateur sans recopier clé, prompt ou réponse API."""
    status = getattr(exc, "status_code", None)
    log.warning(
        "OpenAI request failed: type=%s status=%s request_id=%s",
        type(exc).__name__,
        status,
        getattr(exc, "request_id", None),
    )
    if isinstance(exc, TimeoutError) or type(exc).__name__ == "APITimeoutError":
        return OpenAIGenerationError("Le service IA a dépassé le délai de réponse. Réessaie plus tard.")
    if status == 429:
        return OpenAIGenerationError("Limite OpenAI atteinte. Réessaie plus tard ou fais vérifier le quota par le staff.")
    if status in {400, 401, 403, 404, 405, 422}:
        return OpenAIGenerationError("La configuration OpenAI doit être vérifiée par le staff.")
    return OpenAIGenerationError("Le service IA est temporairement indisponible. Réessaie plus tard.")


def _responses_unavailable(exc: Exception) -> bool:
    # Un rejet d'endpoint n'a pas lancé de génération. Un timeout, quota, 5xx
    # transitoire ou texte vide ne permet jamais d'inférer cela.
    status = getattr(exc, "status_code", None)
    if status not in {404, 405, 501}:
        return False
    body = getattr(exc, "body", None)
    error = _field(body, "error", body)
    if _field(error, "code") == "model_not_found" or _field(error, "param") == "model":
        return False
    return True


async def generate_openai_text(
    client: Any,
    *,
    response_request: dict[str, Any],
    chat_request: dict[str, Any],
    timeout: float,
) -> str:
    """Un seul appel génératif; repli Chat réservé aux anciens SDK/proxys."""
    try:
        async with asyncio.timeout(timeout):
            create = getattr(getattr(client, "responses", None), "create", None)
            if callable(create):
                try:
                    response = await create(**response_request)
                except Exception as exc:
                    if not _responses_unavailable(exc):
                        raise
                    log.info("OpenAI Responses endpoint unavailable; considering Chat compatibility")
                else:
                    return require_generated_text(response)
            # Retirer les outils en silence produirait une réponse sans consulter
            # les sources demandées. La configuration doit être corrigée à la place.
            if response_request.get("tools"):
                raise OpenAIGenerationError(
                    "La recherche IA exige une API et un SDK compatibles avec Responses. "
                    "Demande au staff de vérifier la configuration."
                )
            response = await client.chat.completions.create(**chat_request)
            return require_generated_text(response)
    except OpenAIGenerationError:
        raise
    except Exception as exc:
        raise public_openai_error(exc) from exc
