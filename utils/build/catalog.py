"""Adaptateur Xixou contrôlé + instantanés locaux. Aucune route API hypothétique."""
from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
import re
import time
from collections import defaultdict
from types import SimpleNamespace
from .conditions import norm, parse, recognized
from .diagnostics import failure_summary, log_failure
from .effects import parse_lines
from .models import (Catalog, CatalogIssue, ItemTemplate, SetDefinition, WeaponMetadata,
                     BuildError, digest, canonical, SLOTS)

log = logging.getLogger(__name__)
API_SOURCE = "https://xixou.io/api/v1/equipements.json"

CATEGORY_SLOTS = {
    "chapeau": ("coiffe",), "coiffe": ("coiffe",), "cape": ("cape",), "sac": ("cape",),
    "sac a dos": ("cape",), "amulette": ("amulette",), "ceinture": ("ceinture",),
    "botte": ("bottes",), "bottes": ("bottes",), "anneau": ("anneau_1", "anneau_2"),
    "bouclier": ("bouclier",), "familier": ("compagnon",), "monture": ("compagnon",),
    "dragodinde": ("compagnon",), "dofus": tuple(f"dofus_{i}" for i in range(1, 7)),
    "pierre ame": ("arme",), "filet capture": ("arme",),
}
for weapon in ("arc", "arbalete", "arme magique", "baguette", "baton", "dague", "epee", "faux", "hache", "marteau", "outil", "pelle", "pioche"):
    CATEGORY_SLOTS[weapon] = ("arme",)


def freeze_catalog(items, sets=(), *, generated_at="", source_hash=None, diagnostics=(), audit=None):
    body = dict(schema_version=2 if audit is not None else 1, generated_at=generated_at, source_hash=source_hash or digest([i.model_dump(mode="json") for i in items]),
                normalizer="evolution-build-1.1", items=[i.model_dump(mode="json") for i in sorted(items, key=lambda i: i.ref)],
                sets=[s.model_dump(mode="json") for s in sorted(sets, key=lambda s: s.ref)], diagnostics=list(diagnostics))
    if audit is not None:
        body["audit"] = [issue.model_dump(mode="json") for issue in audit]
    return Catalog(id=digest(body), **body)


def verify_snapshot(catalog):
    body = catalog.model_dump(mode="json")
    identifier = body.pop("id")
    if digest(body) != identifier:
        raise BuildError("Empreinte du catalogue incohérente.")
    for item in catalog.items:
        body = item.model_dump(mode="json")
        rev = body.pop("revision")
        if digest(body) != rev:
            raise BuildError("Empreinte d'un objet incohérente.")


def strings(value, *, field):
    if value is None:
        return ()
    if isinstance(value, str):
        value = (value,) if value.strip() else ()
    if not isinstance(value, (list, tuple)) or len(value) > 100 or any(not isinstance(v, str) or len(v) > 600 for v in value):
        raise ValueError(f"Format de {field} non pris en charge.")
    return tuple(v.strip() for v in value if v.strip())


def weapon_metadata(row, source):
    """Je conserve seulement les valeurs explicitement fournies par la source."""
    if "arme_stats" not in row:
        return None
    lines = strings(row["arme_stats"], field="arme_stats")
    result = {}
    patterns = (
        (r"^PA\s*:\s*(\d+)$", "ap_cost"),
        (r"^Bonus coup critiques?\s*:\s*([+-]?\d+)$", "critical_bonus"),
        (r"^Critique\s*:\s*1/(\d+)(?:\s*-\s*Échec\s*:\s*1/\d+)?$", "critical_denominator"),
    )
    for line in lines:
        for pattern, key in patterns:
            match = re.fullmatch(pattern, line, re.I)
            if match:
                value = int(match[1])
                if key in result and result[key] != value:
                    raise ValueError("Métadonnées d'arme contradictoires.")
                result[key] = value
        failure = re.search(r"(?:^| - )Échec\s*:\s*1/(\d+)$", line, re.I)
        if failure:
            value = int(failure[1])
            if "failure_denominator" in result and result["failure_denominator"] != value:
                raise ValueError("Probabilité d'échec contradictoire.")
            result["failure_denominator"] = value
    return WeaponMetadata(**result, raw_lines=lines, source=source)


def normalize(entries, payload, overrides=None):
    """Rapprochement ID ET nom/niveau/catégorie ; sinon identité exacte unique.

    Le contrat public ne documente pas la structure des bonus de panoplie. Seules
    les tables normalisées et sourcées des overrides sont utilisées pour les bonus.
    Aucune interpolation de noms en URL, aucun effet transmis à une IA.
    """
    from utils.xixou_api import equipment_category, identity_key, _integer
    if not isinstance(payload, dict) or payload.get("famille") != "equipements" or not isinstance(payload.get("data"), dict):
        raise BuildError("Structure du catalogue Xixou inattendue.")
    if len(canonical(payload).encode()) > 16 * 1024 * 1024:
        raise BuildError("Catalogue trop volumineux.")
    overrides = overrides or {"schema_version": 1, "items": {}, "sets": []}
    if set(overrides) != {"schema_version", "items", "sets"} or overrides["schema_version"] != 1 or not isinstance(overrides["items"], dict):
        raise BuildError("Structure des corrections de catalogue invalide.")
    sets = tuple(SetDefinition.model_validate(s) for s in overrides["sets"])
    by_id, by_identity = {}, defaultdict(list)
    for entry in entries:
        if entry.kind != "item":
            continue
        key = (identity_key(entry.name), entry.level, equipment_category(entry.category))
        by_id.setdefault(str(entry.identifier), []).append(entry)
        by_identity[key].append(entry)
    items, rejected, ambiguous, seen, audit = {}, 0, set(), 0, []
    allowed_overrides = {"conditions", "conditions_known", "two_handed", "unique", "set_ref", "effects", "recipe", "recipe_known", "weapon"}
    for category, rows in payload["data"].items():
        if not isinstance(rows, list):
            raise BuildError("Famille de catalogue tronquée/invalide.")
        for row in rows:
            seen += 1
            if seen > 30000:
                raise BuildError("Nombre de lignes excessif.")
            issue_context = dict(category=str(category)[:180], row=seen,
                                 name=str(row.get("name", ""))[:180] if isinstance(row, dict) else "",
                                 source_hash=digest(row))
            def issue(code, text, severity="warning", ref="", source=API_SOURCE):
                audit.append(CatalogIssue(code=code, text=text[:600], severity=severity,
                                          ref=ref, source=source, **issue_context))
            try:
                if not isinstance(row, dict):
                    raise ValueError("Ligne invalide.")
                name, level = row.get("name"), _integer(row.get("level"))
                cat = equipment_category(category)
                slots = CATEGORY_SLOTS.get(cat)
                if any(norm(str(effect)) == "objet saisonnier" for effect in row.get("effets", ())):
                    issue("SEASONAL_ITEM", "Objet saisonnier exclu du catalogue Retro officiel permanent.", "excluded")
                    rejected += 1
                    continue
                if not slots or not isinstance(name, str) or level is None or not 1 <= level <= 200:
                    issue("CATEGORY_UNSUPPORTED" if not slots else "IDENTITY_INVALID",
                          "Catégorie non équipable dans ce builder." if not slots else "Nom ou niveau invalide.", "excluded")
                    rejected += 1
                    continue
                identity = (identity_key(name), level, cat)
                rawid = row.get("id")
                identifier = _integer(rawid)
                if rawid is not None and identifier is None:
                    raise ValueError("ID externe invalide.")
                matches = by_id.get(str(identifier), ()) if identifier is not None else by_identity.get(identity, ())
                matches = [e for e in matches if (identity_key(e.name), e.level, equipment_category(e.category)) == identity]
                if not matches and identifier is None:
                    entry = SimpleNamespace(token="xixou:" + digest(identity)[:24], url=API_SOURCE)
                    issue("API_IDENTITY", "Identité exacte issue de Xixou ; absente de l'index secondaire.",
                          "info", entry.token)
                elif len(matches) != 1:
                    raise ValueError("Identité absente/ambiguë.")
                else:
                    entry = matches[0]
                effects_raw = row.get("effets", row.get("effects"))
                if effects_raw is None:
                    raise ValueError("Effets absents, pas une liste vide certifiée.")
                effects = parse_lines(strings(effects_raw, field="effets"))
                conditions = strings(row.get("conditions"), field="conditions")
                set_raw = row.get("panoplie")
                warning = []
                if "panoplie" not in row:
                    warning.append("Appartenance à une panoplie non fournie ; absence de bonus non certifiée.")
                if isinstance(set_raw, (list, tuple)) and len(set_raw) == 1 and isinstance(set_raw[0], str):
                    set_raw = set_raw[0]
                if isinstance(set_raw, str):
                    set_ref = norm(set_raw) or None
                elif set_raw is None or set_raw == []:
                    set_ref = None
                else:
                    # Le format peut contenir des bonus, mais sa sémantique doit être vérifiée.
                    set_name = set_raw.get("nom", set_raw.get("name")) if isinstance(set_raw, dict) else None
                    set_ref = norm(set_name) if isinstance(set_name, str) and set_name else None
                    warning.append("Format structuré de panoplie non validé : corrections sourcées nécessaires.")
                body = dict(ref=entry.token, name=name, category=cat, level=level, allowed_slots=slots,
                            effects=[e.model_dump(mode="json") for e in effects], set_ref=set_ref,
                            conditions=conditions, conditions_known="conditions" in row and row["conditions"] is not None,
                            source=entry.url, warnings=warning, two_handed=None, unique=None,
                            recipe=[], recipe_known=False, image_urls=[])
                metadata = weapon_metadata(row, entry.url)
                if metadata is not None:
                    body["weapon"] = metadata
                if "recette" in row and isinstance(row["recette"], list) and not row["recette"]:
                    body["recipe_known"] = True
                correction = overrides["items"].get(entry.token)
                if correction:
                    if not isinstance(correction, dict) or not correction.get("source") or set(correction) - (allowed_overrides | {"source"}):
                        raise BuildError("Correction d'objet invalide ou non sourcée.")
                    body["correction_sources"] = [correction["source"]]
                    body.update({k: v for k, v in correction.items() if k in allowed_overrides})
                    if "set_ref" in correction:
                        body["warnings"] = []
                # Valider puis hacher la représentation CANONIQUE (avec tous les défauts).
                candidate = ItemTemplate(revision="0" * 64, **body)
                data = candidate.model_dump(mode="json")
                data.pop("revision")
                candidate = ItemTemplate(revision=digest(data), **data)
                for effect in candidate.effects:
                    if effect.kind in {"unknown", "context"}:
                        issue("EFFECT_UNKNOWN" if effect.kind == "unknown" else "EFFECT_CONTEXT",
                              effect.text, ref=entry.token, source=entry.url)
                if not candidate.conditions_known:
                    issue("CONDITIONS_MISSING", "Conditions non fournies.", ref=entry.token)
                for condition in candidate.conditions:
                    if not recognized(parse(condition)):
                        issue("CONDITION_UNKNOWN", condition, ref=entry.token, source=entry.url)
                for text in candidate.warnings:
                    issue("ITEM_COVERAGE", text, ref=entry.token, source=entry.url)
                if candidate.set_ref and candidate.set_ref not in {s.ref for s in sets}:
                    issue("SET_MISSING", candidate.set_ref, ref=entry.token, source=entry.url)
                if candidate.ref in items and candidate != items[candidate.ref]:
                    ambiguous.add(candidate.ref)
                    issue("IDENTITY_CONFLICT", "Réponses contradictoires pour la même identité ; objet exclu.",
                          "excluded", candidate.ref)
                items[candidate.ref] = candidate
            except BuildError:
                raise
            except (ValueError, TypeError, KeyError) as exc:
                rejected += 1
                issue("IDENTITY_AMBIGUOUS" if "Identité absente" in str(exc) else "ROW_INVALID",
                      "Identité absente/ambiguë." if "Identité absente" in str(exc)
                      else "Ligne rejetée : " + str(exc).split("\n", 1)[0], "excluded")
    for ref in ambiguous:
        items.pop(ref, None)
    if not items:
        raise BuildError("Aucun objet rapproché de façon fiable ; ancien catalogue conservé.")
    diagnostics = (f"Lignes reçues : {seen}", f"Objets rapprochés : {len(items)}", f"Lignes exclues : {rejected}",
                   f"Identités contradictoires exclues : {len(ambiguous)}", f"Tables de panoplie sourcées : {len(sets)}")
    log.debug("Build catalogue: received=%s accepted=%s rejected=%s issues=%s", seen, len(items), rejected, len(audit))
    return freeze_catalog(tuple(items.values()), sets, generated_at=str(payload.get("genere_le", ""))[:100],
                          source_hash=digest({"payload": payload, "overrides": overrides}), diagnostics=diagnostics,
                          audit=tuple(audit))


def search(catalog, query="", slot=None, level=200, limit=25, *, offset=0, priority=None, minimum=1):
    if slot is not None and slot not in SLOTS:
        raise BuildError("Emplacement inconnu.")
    from .models import STAT_LABELS
    if type(offset) is not int or not 0 <= offset <= 20000 or priority is not None and priority not in STAT_LABELS:
        raise BuildError("Pagination ou tri inconnu.")
    if type(minimum) is not int or not 1 <= minimum <= level <= 200:
        raise BuildError("Intervalle de niveaux invalide.")
    q = norm(query)
    pool = [i for i in catalog.items if minimum <= i.level <= level and (slot is None or slot in i.allowed_slots)]
    if q:
        from rapidfuzz.fuzz import WRatio
        ranked = [(1000 if i.ref == query else 200 if q == norm(i.name) else 110 if q in norm(i.name) else WRatio(q, norm(i.name)), i) for i in pool]
        pool = [i for score, i in sorted(ranked, key=lambda row: (-row[0], row[1].name, row[1].ref)) if score >= 60]
    else:
        pool.sort(key=lambda i: (-i.level, i.name, i.ref))
    if priority is not None:
        # Stable : pertinence du nom puis niveau départagent les jets égaux.
        pool.sort(key=lambda i: -sum(e.high for e in i.effects if e.kind == "stat" and e.stat == priority))
    return tuple(pool[offset:offset + max(1, min(limit, 25))])


class CatalogService:
    def __init__(self, repository, wiki_provider, overrides_path=None, set_loader=None):
        self.repository = repository
        self.set_loader = set_loader
        self.wiki_provider = wiki_provider
        self.overrides_path = Path(overrides_path) if overrides_path else Path(__file__).resolve().parents[2] / "data/build/catalog_overrides_v1.json"
        self.latest = None
        self.lock = asyncio.Lock()
        self.last_error = ""
        self.refreshed_at = 0.0
        self.phase = "non_demarre"
        self._pending_candidate = None

    async def restore(self):
        self.phase = "restauration_console"
        try:
            obj = await self.repository.latest_snapshot("catalog")
            if obj is None:
                # Un transfert terminé n'est pas encore un snapshot engagé.
                # Seul le repository peut valider et référencer ces fragments.
                recover = getattr(self.repository, "recover_catalog", None)
                if recover is not None:
                    obj = await recover()
            if obj is not None:
                catalog = await asyncio.to_thread(Catalog.model_validate_json, obj)
                await asyncio.to_thread(verify_snapshot, catalog)
                self.latest = catalog
                self.phase = "pret"
                self.last_error = ""
            return self.latest
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = ("Catalogue : échec à l'étape restauration_console. "
                               + failure_summary(exc))
            log_failure(log, "build catalog restore failed", exc)
            if isinstance(exc, BuildError):
                raise
            raise BuildError(self.last_error) from exc

    async def _persist_pending(self):
        """Réessayer les mêmes octets ; ne pas créer une nouvelle génération source."""
        candidate = self._pending_candidate
        if candidate is None:
            raise BuildError("Aucun catalogue préparé à archiver.")
        self.phase = "archivage_console"
        payload = await asyncio.to_thread(canonical, candidate)
        await self.repository.put_snapshot("catalog", candidate.id, payload)
        # L'accès aux créations n'est ouvert qu'après confirmation de la racine.
        self.latest = candidate
        self._pending_candidate = None
        self.last_error = ""
        self.phase = "pret"
        self.refreshed_at = time.monotonic()
        return candidate

    async def refresh(self):
        async with self.lock:
            try:
                if self._pending_candidate is not None:
                    return await self._persist_pending()
                self.phase = "configuration"
                wiki = self.wiki_provider()
                if wiki is None or getattr(wiki, "_closed", False) or not getattr(getattr(wiki, "enrichment_client", None), "enabled", False):
                    raise BuildError("Catalogue indisponible : activer l'enrichissement Xixou dans le wiki.")
                self.phase = "sources"
                async with asyncio.timeout(45):
                    entries, payload = await asyncio.gather(wiki.client.items(), wiki.enrichment_client.catalog("equipements"))
                if not payload:
                    raise BuildError("Catalogue Xixou indisponible ; copie précédente conservée.")
                self.phase = "corrections"
                raw = await asyncio.to_thread(self.overrides_path.read_bytes)
                if len(raw) > 4 * 1024 * 1024:
                    raise BuildError("Corrections trop volumineuses.")
                overrides = json.loads(raw)
                self.phase = "normalisation"
                candidate = await asyncio.to_thread(normalize, entries, payload, overrides)
                if self.set_loader is not None:
                    self.phase = "panoplies"
                    # Les corrections locales font autorité ; conserver les tables
                    # publiques archivées au lieu de les retélécharger à chaque clic.
                    previous = dict(self.latest.by_set) if self.latest else {}
                    previous.update(candidate.by_set)
                    refs = {i.set_ref for i in candidate.items if i.set_ref}
                    definitions, notes = await self.set_loader.enrich(refs, tuple(previous.values()))
                    candidate = freeze_catalog(candidate.items, definitions,
                        generated_at=candidate.generated_at,
                        source_hash=digest({"api": candidate.source_hash,
                                           "sets": [d.model_dump(mode="json") for d in sorted(definitions, key=lambda d: d.ref)]}),
                        diagnostics=candidate.diagnostics + notes,
                        audit=tuple(issue for issue in candidate.audit
                                    if issue.code != "SET_MISSING" or issue.text not in {d.ref for d in definitions}))
                self.phase = "verification"
                await asyncio.to_thread(verify_snapshot, candidate)
                self._pending_candidate = candidate
                return await self._persist_pending()
            except asyncio.CancelledError:
                # Si l'archivage a commencé, le candidat reste disponible pour la
                # reprise. Au redémarrage, les fragments validés servent de relais.
                raise
            except Exception as exc:
                hints = {
                    "configuration": "Vérifier le wiki et la configuration XIXOU_API_KEY.",
                    "sources": "Vérifier l'accès aux sources wiki/Xixou.",
                    "corrections": "Vérifier le fichier BUILD_CATALOG_OVERRIDES.",
                    "normalisation": "Vérifier le format et les identités du catalogue source.",
                    "panoplies": "Vérifier l'enrichissement des panoplies.",
                    "verification": "Vérifier les empreintes du catalogue normalisé.",
                    "archivage_console": "Vérifier #console, le verrou et les permissions Discord.",
                }
                self.last_error = (
                    f"Catalogue : échec à l'étape {self.phase}. "
                    + hints.get(self.phase, "Consulter les logs techniques de l'hébergeur.")
                    + (" Copie précédente conservée." if self.latest is not None else " Aucun catalogue engagé.")
                    + " Cause : " + failure_summary(exc)
                )
                log_failure(log, f"build catalog refresh failed phase={self.phase}", exc)
                if self.latest is None:
                    raise BuildError(self.last_error) from exc
                return self.latest
