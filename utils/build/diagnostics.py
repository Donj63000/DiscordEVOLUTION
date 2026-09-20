"""État de couverture sans données privées ni secrets de configuration."""
from collections import Counter


def catalog_coverage(catalog):
    counts = Counter()
    membership = Counter()
    for item in catalog.items:
        counts["objets"] += 1
        counts["conditions_absentes"] += not item.conditions_known
        counts["effets_inconnus"] += sum(effect.kind == "unknown" for effect in item.effects)
        counts["objets_partiels"] += bool(item.warnings)
        if item.set_ref:
            membership[item.set_ref] += 1
    available = catalog.by_set
    missing = sorted(set(membership) - set(available))
    return {
        **dict(counts),
        "catalogue": catalog.id,
        "panoplies_referencees": len(membership),
        "tables_panoplies_disponibles": len(set(membership) & set(available)),
        "tables_panoplies_manquantes": missing,
        "diagnostics_par_code": dict(Counter(issue.code for issue in catalog.audit)),
        "lignes_exclues": sum(issue.severity == "excluded" for issue in catalog.audit),
        "familles": dict(Counter(item.category for item in catalog.items)),
        "portee": "La présence d'une table ne certifie ni les règles du jeu ni chaque objet.",
    }


def catalog_audit(catalog):
    """Je fournis une preuve exploitable par objet sans exposer de configuration."""
    return {"schema_version": 1, "coverage": catalog_coverage(catalog),
            "source_hash": catalog.source_hash, "generated_at": catalog.generated_at,
            "issues": [issue.model_dump(mode="json") for issue in catalog.audit]}


def coverage_text(catalog):
    info = catalog_coverage(catalog)
    missing = info["tables_panoplies_manquantes"]
    return (f"Objets : {info.get('objets', 0)} · effets inconnus : {info.get('effets_inconnus', 0)}\n"
            f"Panoplies : {info['tables_panoplies_disponibles']}/{info['panoplies_referencees']} tables présentes\n"
            + ("Manquantes : " + ", ".join(missing[:8]) + (f" (+{len(missing)-8})" if len(missing)>8 else "")
               if missing else "Aucune table manquante parmi les panoplies référencées.")
            + "\nLes règles non qualifiées restent signalées, sans certification automatique.")


def failure_summary(error):
    """Chaîne de types/codes techniques, sans URL, jeton, profil ni valeur Pydantic."""
    rows, seen = [], set()
    while error is not None and id(error) not in seen and len(rows) < 6:
        seen.add(id(error))
        row = type(error).__name__
        status = getattr(error, "status", None)
        code = getattr(error, "code", None)
        if type(status) is int:
            row += f" HTTP {status}"
        if type(code) is int:
            row += f" code {code}"
        rows.append(row)
        error = error.__cause__ or error.__context__
    return " <- ".join(rows)


def log_failure(logger, context, error):
    """Trace des emplacements de code, sans sérialiser les exceptions ni leurs entrées."""
    import traceback

    frames, seen, current = [], set(), error
    while current is not None and id(current) not in seen and len(seen) < 6:
        seen.add(id(current))
        frames.append(type(current).__name__ + ":\n" + "\n".join(
            f"  {frame.filename}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(current.__traceback__, limit=8)))
        current = current.__cause__ or current.__context__
    logger.error("%s causes=%s\n%s", context, failure_summary(error), "\n".join(frames))
