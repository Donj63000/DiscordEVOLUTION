#!/usr/bin/env python3
"""Diagnostic de données/dépendances sans écriture en base ni connexion Discord.

--public-panoplies : contrôle HTTP OPT-IN des pages des tables embarquées.
Ce contrôle compare les données, pas les règles au moteur du jeu.
"""
from __future__ import annotations
import argparse
import asyncio
from importlib import metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-panoplies', action='store_true', help='Comparer les tables embarquées aux pages publiques Xixou (réseau explicite).')
    args = parser.parse_args()
    report = {'module': 'Evolution Build', 'controle': 'données et dépendances, pas certification en jeu',
              'dependances': {}, 'tables': [], 'reseau_execute': False}
    missing = []
    for name in ('discord.py', 'asyncpg', 'pydantic', 'Pillow', 'aiohttp'):
        try:
            report['dependances'][name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            report['dependances'][name] = 'absent'
            missing.append(name)
    try:
        from utils.build.models import SetDefinition
        from utils.build.rules import load_rules
        payload = json.loads((ROOT / 'data/build/catalog_overrides_v1.json').read_text(encoding='utf-8'))
        tables = tuple(SetDefinition.model_validate(row) for row in payload['sets'])
        if len({table.ref for table in tables}) != len(tables):
            raise ValueError('Identité de panoplie dupliquée.')
        for table in tables:
            counts = [tier.pieces for tier in table.tiers]
            if counts != list(range(1, max(counts) + 1)):
                raise ValueError('Seuils non continus.')
            report['tables'].append({'nom': table.name, 'seuils': len(counts), 'source': table.source})
        rules = load_rules()
        report['regles'] = {'version': rules.version, 'bases_validees': rules.base_verified,
                            'paliers_valides': rules.allocation_verified,
                            'derivees_validees': rules.derivatives_verified,
                            'restrictions_validees': rules.restrictions_verified}
        if args.public_panoplies:
            report['reseau_execute'] = True
            report['controle_public'] = asyncio.run(check_public(tables))
    except Exception as exc:
        # Pas de DSN, variables d'environnement ou détails d'authentification.
        report['erreur'] = type(exc).__name__
    report['dependances_absentes'] = missing
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed_public = any(row['statut'] != 'conforme' for row in report.get('controle_public', ()))
    return int(bool(missing or report.get('erreur') or failed_public))


async def check_public(tables):
    import aiohttp
    from utils.build.set_source import PublicSetLoader, parse_set
    loader = PublicSetLoader()
    results = []
    def signature(table):
        return {tier.pieces: sorted((effect.kind, effect.stat, effect.low, effect.high)
                                   for effect in tier.effects) for tier in table.tiers}
    try:
        async with asyncio.timeout(60):
            for table in tables:
                try:
                    fresh = parse_set(await loader.fetch(table.source), table.ref, table.source)
                    results.append({'nom': table.name, 'statut': 'conforme' if signature(fresh) == signature(table) else 'divergence'})
                except (ValueError, OSError, TimeoutError, aiohttp.ClientError) as exc:
                    results.append({'nom': table.name, 'statut': 'à vérifier', 'erreur': type(exc).__name__})
                await asyncio.sleep(0.5)
    except TimeoutError:
        results.append({'statut': 'limite globale atteinte'})
    finally:
        await loader.close()
    return results


if __name__ == '__main__':
    raise SystemExit(main())
