"""Je contrôle les données publiques sans modifier le catalogue du bot ni Discord."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from utils.build.catalog import normalize, verify_snapshot
from utils.build.diagnostics import catalog_audit
from utils.dofus_wiki import DofusWikiClient
from utils.xixou_api import XixouClient


async def audit():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    client = XixouClient(os.getenv("XIXOU_API_KEY", ""), timeout=30)
    wiki = DofusWikiClient(timeout=30)
    try:
        if not client.enabled:
            return {"status": "unavailable", "reason": "Clé Xixou absente."}
        payload, entries = await asyncio.gather(client.catalog("equipements"), wiki.items())
        if not payload:
            return {"status": "unavailable", "reason": "Catalogue Xixou indisponible ; aucun catalogue remplacé."}
        overrides_path = Path(__file__).resolve().parents[1] / "data/build/catalog_overrides_v1.json"
        overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
        catalog = normalize(entries, payload, overrides)
        verify_snapshot(catalog)
        return {"status": "ok", **catalog_audit(catalog)}
    except Exception as exc:
        return {"status": "unavailable", "reason": type(exc).__name__}
    finally:
        await client.close()
        await wiki.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Rapport public détaillé facultatif.")
    args = parser.parse_args()
    result = asyncio.run(audit())
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {key: value for key, value in result.items() if key != "issues"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
