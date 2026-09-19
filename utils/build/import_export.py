"""Format Evolution JSON borné, sans code, URLs à charger ou identité importée."""
from __future__ import annotations
import json
from .models import Build, BuildError, Profile, SlotItem, canonical, new_id, revised
MAX_IMPORT = 256 * 1024


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise BuildError("Clé JSON dupliquée.")
        result[key] = value
    return result


def parse_json(raw: bytes, maximum=MAX_IMPORT):
    if not isinstance(raw, bytes) or len(raw) > maximum:
        raise BuildError("JSON trop volumineux (256 Kio maximum).")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(BuildError("Nombre JSON invalide.")))
        todo, count = [(value, 0)], 0
        while todo:
            obj, depth = todo.pop()
            count += 1
            if depth > 12 or count > 10000:
                raise BuildError("JSON trop profond/complexe.")
            if isinstance(obj, dict):
                todo.extend((v, depth + 1) for v in obj.values())
            elif isinstance(obj, list):
                todo.extend((v, depth + 1) for v in obj)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BuildError("Fichier JSON invalide.") from exc


def export_build(build):
    return (canonical({"format": "evolution-build", "version": 1, "name": build.name,
                       "catalog_id": build.catalog_id, "rules_id": build.rules_id,
                       "profile": build.profile.model_dump(mode="json"),
                       "slots": [s.model_dump(mode="json") for s in build.slots]}) + "\n").encode("utf-8")


def import_build(raw, actor):
    obj = parse_json(raw)
    allowed = {"format", "version", "name", "catalog_id", "rules_id", "profile", "slots", "owner_id", "guild_id", "id", "revision", "visibility"}
    if not isinstance(obj, dict) or set(obj) - allowed or obj.get("format") != "evolution-build" or type(obj.get("version")) is not int or obj["version"] != 1:
        raise BuildError("Format Evolution Build version 1 attendu.")
    # Les identités étrangères ne sont JAMAIS réutilisées.
    try:
        slots = tuple(SlotItem.model_validate(r) for r in obj["slots"])
        if len(slots) > 16:
            raise BuildError("Trop d'emplacements.")
        slots = [dict(slot=r.slot, item=revised(r.item, id=new_id()).model_dump(mode="json")) for r in slots]
        return Build(guild_id=actor.guild_id, owner_id=actor.user_id, name=obj["name"],
                     catalog_id=obj["catalog_id"], rules_id=obj["rules_id"],
                     profile=Profile.model_validate(obj["profile"]), slots=slots)
    except (KeyError, ValueError, TypeError) as exc:
        raise BuildError("Profil, jets ou emplacements d'import invalides.") from exc
