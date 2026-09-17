#!/usr/bin/env python3
"""Commande locale : Monte-Carlo, comparaison indépendante, extraction de relevés."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

# Exécution depuis la racine OU par chemin absolu, sans installer le bot.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.exo_session import EXPORT_LIMIT, import_session
from utils.fm_retro_audit import collect, evaluate, monte_carlo
from utils.fm_retro_observations import DEFAULT_PATH, load_corpus


def save(path: Path, payload: dict, *, corpus: bool = False) -> None:
    """Valide entièrement avant publication et refuse tout écrasement."""
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if corpus:
        with tempfile.TemporaryDirectory(prefix="fm-corpus-") as directory:
            temporary = Path(directory) / "corpus.json"
            temporary.write_bytes(encoded)
            load_corpus(temporary)
    # Lien créé en exclusif : aucun écrasement et aucun fichier partiel publié.
    # Le temporaire est voisin de la cible pour rester sur le même système de fichiers.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".fm-", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    mc = commands.add_parser("monte-carlo", help="Invariants et distributions du modèle, pas du serveur")
    mc.add_argument("--draws", type=int, default=100000)
    mc.add_argument("--seed", type=int, default=129)
    mc.add_argument("--output", type=Path, required=True)
    evaluation = commands.add_parser("evaluate", help="Confronte le moteur aux séances de validation")
    evaluation.add_argument("--corpus", type=Path, default=DEFAULT_PATH)
    evaluation.add_argument("--draws-per-context", type=int, default=2000)
    evaluation.add_argument("--max-contexts", type=int, default=100)
    evaluation.add_argument("--seed", type=int, default=129)
    evaluation.add_argument("--output", type=Path, required=True)
    extraction = commands.add_parser("collect", help="Extrait le suivi manuel, jamais la simulation")
    extraction.add_argument("snapshot", type=Path)
    extraction.add_argument("--session", required=True)
    extraction.add_argument("--split", choices=("train", "validation"), required=True)
    extraction.add_argument("--corpus-id", required=True)
    extraction.add_argument("--url", required=True)
    extraction.add_argument("--server", required=True)
    extraction.add_argument("--game-version", required=True)
    extraction.add_argument("--profession-level", type=int, choices=(100,), required=True)
    extraction.add_argument("--date", required=True, help="Date de capture ISO, AAAA-MM-JJ")
    extraction.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "monte-carlo":
            result = monte_carlo(args.draws, args.seed)
            code = 0 if result["passed"] else 1
        elif args.command == "evaluate":
            result = evaluate(load_corpus(args.corpus), args.draws_per_context, args.seed, args.max_contexts)
            code = 0 if result["status"] != "insufficient_data" else 2
        else:
            with args.snapshot.open("rb") as handle:
                raw = handle.read(EXPORT_LIMIT + 1)
            session = import_session(raw)
            result = collect(session, session_id=args.session, split=args.split, corpus_id=args.corpus_id,
                             source={"url": args.url, "server": args.server,
                                     "game_version": args.game_version, "date": args.date, "profession_level": args.profession_level})
            code = 0
        save(args.output, result, corpus=args.command == "collect")
        print(json.dumps({"output": str(args.output), "exit_code": code,
                          "status": result.get("status", result.get("kind", "declaration_exported"))},
                         ensure_ascii=False))
        return code
    except (ValueError, OSError) as exc:
        print(f"Validation FM refusée : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
