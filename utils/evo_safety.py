"""Bornes, filtrage et permissions exécutés par Python, indépendamment du modèle."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
from urllib.parse import urlsplit

from utils.evo_config import EvoError

_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{12,}|mfa\.[A-Za-z0-9_-]{16,}|"
    r"(?:postgres(?:ql)?://|https://discord(?:app)?\.com/api/webhooks/)\S+|"
    r"[A-Za-z0-9_-]{23,30}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,}|"
    r"(?i:(?:api[_ -]?key|password|mot de passe|token|secret)\s*[:=]\s*)[^\s,;]{8,}"
)
_URL = re.compile(r"https?://[^\s<>()\[\]\"']+")
_ALLOWED_SOURCES = {"wiki.moon-bot.io", "xixou.io", "discord.com", "discord.gg"}


def clean(value: object, maximum: int = 400) -> str:
    text = str(value or "")
    text = "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and c not in "\u200b\u200c\u200d\u202e"))
    return _SECRET.sub("[secret retiré]", text)[:maximum]


def compact(value: object, *, max_string: int = 400, max_list: int = 15, depth: int = 0):
    if depth > 7:
        return "[profondeur limitée]"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if abs(value) > 10**15 or (isinstance(value, float) and not math.isfinite(value)):
            return None
        return value
    if isinstance(value, str):
        return clean(value, max_string)
    if isinstance(value, (list, tuple)):
        return [compact(x, max_string=max_string, max_list=max_list, depth=depth+1) for x in value[:max_list]]
    if isinstance(value, dict):
        return {
            clean(k, 70): compact(v, max_string=max_string, max_list=max_list, depth=depth+1)
            for k, v in list(value.items())[:40]
        }
    return clean(value, max_string)


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def bounded_json(value: object, limit: int = 6500) -> str:
    """Ne coupe jamais un document JSON en plein milieu d'un nombre ou d'un objet."""
    value = compact(value)
    text = json_text(value)
    if len(text.encode("utf-8")) <= limit:
        return text
    if isinstance(value, dict):
        value["tronque"] = True
        for key, item in list(value.items()):
            if isinstance(item, list):
                value[key] = item[:5]
        text = json_text(value)
        if len(text.encode("utf-8")) <= limit:
            return text
    return json_text({"erreur": "Résultat trop volumineux : précise l'objet, le niveau ou le type recherché."})


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Clé dupliquée")
        result[key] = value
    return result


def parse_arguments(raw: str, schema: dict) -> dict:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4096:
        raise EvoError("Arguments d'outil trop longs.")
    def invalid(_):
        raise ValueError("Nombre non fini")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid)
        validate(value, schema)
    except (ValueError, TypeError, RecursionError):
        raise EvoError("Arguments d'outil invalides ; précise ta demande.") from None
    return value


def validate(value, schema):
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    kind = ("null" if value is None else "boolean" if type(value) is bool else
            "integer" if type(value) is int else "number" if type(value) is float else
            "string" if isinstance(value, str) else "object" if isinstance(value, dict) else
            "array" if isinstance(value, list) else "invalid")
    if kind not in kinds and not (kind == "integer" and "number" in kinds):
        raise ValueError("Type")
    if value is None:
        return
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Enum")
    if kind == "object":
        props = schema["properties"]
        if set(value) != set(schema["required"]):
            raise ValueError("Champs absents ou supplémentaires")
        for key, child in value.items():
            validate(child, props[key])
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10):
            raise ValueError("Taille")
        for child in value:
            validate(child, schema["items"])
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 180):
            raise ValueError("Longueur")
    elif kind in {"integer", "number"}:
        if not math.isfinite(value) or not schema.get("minimum", -10000) <= value <= schema.get("maximum", 100000):
            raise ValueError("Nombre")


@dataclass
class ToolContext:
    bot: object
    guild: object
    channel: object
    member: object
    config: object
    # Une session /exo est privée. Sa divulgation demande un consentement explicite
    # dans la question et non une décision du LLM.
    allow_private_fm: bool = False
    sources: set[str] = field(default_factory=set)

    def check(self) -> None:
        if self.guild is None or self.guild.id != self.config.guild_id:
            raise EvoError("Evo n'est pas activé sur ce serveur.")
        if self.channel is None or self.channel.id not in self.config.channel_ids:
            raise EvoError("Utilise /evo dans un salon autorisé par le Staff.")
        if getattr(getattr(self.channel, "guild", None), "id", None) != self.guild.id:
            raise EvoError("Salon hors du serveur configuré.")
        member = self.guild.get_member(self.member.id)
        if member is None or getattr(member, "bot", False):
            raise EvoError("Ce membre n'est plus accessible sur le serveur.")
        self.member = member
        me = self.guild.me
        if me is None:
            raise EvoError("Les permissions du bot ne sont pas encore disponibles.")
        for subject in (member, me):
            perms = self.channel.permissions_for(subject)
            if not perms.view_channel or not perms.send_messages:
                raise EvoError("Permission de lecture ou d'écriture manquante dans ce salon.")

    def readable_here(self, channel) -> bool:
        """Une réponse publique ne doit pas recopier une autre audience privée."""
        if channel is None or getattr(getattr(channel, "guild", None), "id", None) != self.guild.id:
            return False
        if channel.id != self.channel.id and not channel.permissions_for(self.guild.default_role).view_channel:
            return False
        return all(channel.permissions_for(subject).view_channel for subject in (self.member, self.guild.me))

    def source(self, url: str) -> str:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return ""
        if (parsed.scheme == "https" and parsed.hostname in _ALLOWED_SOURCES
                and not parsed.username and not parsed.password and port in (None, 443)
                and len(url) <= 500):
            self.sources.add(url)
            return url
        return ""


def output_text(text: str, sources: set[str], limit: int = 1800) -> str:
    def verified(match):
        url = match.group().rstrip(".,;!")
        return url if url in sources else "[lien non vérifié]"
    text = _URL.sub(verified, clean(text, 10000)).replace("@", "@\u200b").strip()
    # Discord compte la longueur en unités UTF-16, pas en points de code Python.
    raw = text.encode("utf-16-le")
    if len(raw) > limit * 2:
        text = raw[: (limit - 30) * 2].decode("utf-16-le", errors="ignore").rstrip() + "\n… On peut préciser ensuite."
    return text
