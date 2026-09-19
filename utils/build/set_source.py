"""Bonus publiés par Xixou : lecture HTML bornée, aucun jet ni seuil inventé.

Les chemins viennent uniquement de l'index public, pas d'une URL utilisateur.
La source publie les BONUS TOTAUX par nombre de pièces. Le parseur rejette une
page tronquée, une identité différente ou une structure ambiguë. Les descriptions
et les scripts ne sont jamais interprétés comme des effets.
"""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import re
import time
from urllib.parse import urljoin, urlsplit

import aiohttp

from .conditions import norm
from .effects import parse_lines
from .models import BuildError, SetDefinition, SetTier

ORIGIN = "https://xixou.io"
INDEX = ORIGIN + "/encyclopedie/panoplies/"
MAX_BYTES = 2 * 1024 * 1024
BLOCKS = {"h1", "h2", "h3", "p", "li", "td", "th", "tr", "div", "section", "br", "ul"}


class PublicDocument(HTMLParser):
    """Extrait du texte et des liens, sans dépendance à une classe CSS fragile."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = []
        self.buffer = []
        self.links = []
        self.anchor = None
        self.title = []
        self.in_title = False
        self.skip = 0

    def flush(self):
        text = " ".join("".join(self.buffer).split())
        if text:
            self.lines.append(text)
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        if self.skip:
            return
        if tag in BLOCKS:
            self.flush()
        if tag == "h1":
            self.in_title = True
        if tag == "a":
            self.anchor = [dict(attrs).get("href", ""), []]

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "h1":
            self.in_title = False
        if tag == "a" and self.anchor is not None:
            self.links.append((self.anchor[0], " ".join("".join(self.anchor[1]).split())))
            self.anchor = None
        if tag in BLOCKS:
            self.flush()

    def handle_data(self, data):
        if self.skip:
            return
        self.buffer.append(data)
        if self.in_title:
            self.title.append(data)
        if self.anchor is not None:
            self.anchor[1].append(data)


def document(raw: str) -> PublicDocument:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BYTES:
        raise BuildError("Page de panoplie trop volumineuse.")
    parser = PublicDocument()
    parser.feed(raw)
    parser.close()
    parser.flush()
    return parser


def allowed_url(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.netloc == "xixou.io"
            and not parts.query and not parts.fragment
            and re.fullmatch(r"/encyclopedie/panoplies/(?:[a-z0-9-]+/)?", parts.path) is not None)


def parse_index(raw: str) -> dict[str, str]:
    result, conflicts = {}, set()
    for href, name in document(raw).links:
        url = urljoin(INDEX, href)
        if not name or not allowed_url(url) or url == INDEX:
            continue
        key = norm(name)
        if key in result and result[key] != url:
            conflicts.add(key)
        result[key] = url
    for key in conflicts:
        result.pop(key, None)
    if not result:
        raise BuildError("Index public des panoplies vide ou non reconnu.")
    return result


def parse_set(raw: str, expected_ref: str, source: str) -> SetDefinition:
    if not allowed_url(source) or source == INDEX:
        raise BuildError("Source de panoplie non autorisée.")
    doc = document(raw)
    title = " ".join("".join(doc.title).split())
    if norm(title) != norm(expected_ref):
        raise BuildError("La page ne correspond pas à la panoplie demandée.")
    try:
        start = next(i for i, line in enumerate(doc.lines) if norm(line) == "bonus de panoplie") + 1
    except StopIteration as exc:
        raise BuildError("Table des bonus absente de la page.") from exc
    # Cette phrase identifie les bornes explicitement annoncées par la source.
    intro = norm(" ".join(doc.lines[:start]))
    bounds = re.search(r"active des bonus,? de (\d+) a (\d+) pieces", intro)
    count = re.search(r"composee de (\d+) pieces", intro)
    if not bounds or not count:
        raise BuildError("Bornes des bonus non documentées : import refusé.")
    first, last = map(int, bounds.groups())
    if not 2 <= first <= last <= 16 or int(count[1]) != last:
        raise BuildError("Nombre de pièces de la panoplie incohérent.")
    rows, current, finished = {}, None, False
    for line in doc.lines[start:]:
        normalized = norm(line).strip(" |")
        if "toutes les panoplies" in normalized:
            finished = True
            break
        heading = re.fullmatch(r"(\d+) pieces?\s*\|?", normalized)
        if heading:
            current = int(heading[1])
            if current in rows or not first <= current <= last:
                raise BuildError("Seuil de panoplie dupliqué ou inattendu.")
            rows[current] = []
        elif normalized and normalized != "|":
            if current is None:
                raise BuildError("En-tête de bonus non reconnu.")
            rows[current].append(line)
    if not finished or set(rows) != set(range(first, last + 1)) or any(not row for row in rows.values()):
        raise BuildError("Table de panoplie incomplète : aucun bonus ajouté.")
    tiers = [SetTier(pieces=n, effects=()) for n in range(1, first)]
    for pieces, lines in sorted(rows.items()):
        effects = parse_lines(lines)
        if any(e.kind not in {"stat", "cosmetic"} or e.low != e.high for e in effects):
            raise BuildError("Un bonus publié reste non interprété : table non importée.")
        tiers.append(SetTier(pieces=pieces, effects=effects))
    return SetDefinition(ref=norm(expected_ref), name=title, tiers=tuple(tiers), source=source)


class PublicSetLoader:
    """Enrichissement best effort, séquentiel et plafonné hors boucle de calcul.

    Les tables réussies sont archivées dans le catalogue ; un échec conserve les
    tables précédentes. Pas de transmission de clé Xixou à ces pages publiques.
    """
    def __init__(self, *, max_pages=20, budget_seconds=25):
        self.max_pages = max(1, min(max_pages, 50))
        self.budget_seconds = max(1, min(budget_seconds, 60))
        self.session = None
        self.index = {}
        self.index_at = 0.0
        self.retry_after = {}
        self.closed = False

    async def close(self):
        self.closed = True
        if self.session:
            await self.session.close()
            self.session = None

    async def fetch(self, url):
        if self.closed:
            raise BuildError("Chargeur public arrêté.")
        if not allowed_url(url):
            raise BuildError("URL de catalogue public non autorisée.")
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
                headers={"User-Agent": "EvolutionBuild/1.1 (+Discord; Xixou attribution)"},
                trust_env=False,
            )
        async with self.session.get(url, allow_redirects=False) as response:
            if response.status != 200 or response.content_type not in {"text/html", "application/xhtml+xml"}:
                raise BuildError("Page publique indisponible ou format différent.")
            if response.content_length is not None and response.content_length > MAX_BYTES:
                raise BuildError("Page publique trop volumineuse.")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise BuildError("Page publique trop volumineuse.")
            return data.decode("utf-8", errors="strict")

    async def enrich(self, refs, definitions):
        known = {definition.ref: definition for definition in definitions}
        needed = sorted(set(refs) - known.keys())
        added = failed = 0
        if not needed:
            return tuple(known.values()), ("Bonus publiés : aucune table manquante pour les objets indexés.",)
        try:
            async with asyncio.timeout(self.budget_seconds):
                if not self.index or time.monotonic() - self.index_at > 86400:
                    self.index = await asyncio.to_thread(parse_index, await self.fetch(INDEX))
                    self.retry_after = {ref: until for ref, until in self.retry_after.items() if ref in self.index}
                    self.index_at = time.monotonic()
                eligible = [ref for ref in needed if ref in self.index and self.retry_after.get(ref, 0) <= time.monotonic()]
                # Une page cassée ne doit pas affamer toutes les suivantes.
                targets = [(ref, self.index[ref]) for ref in eligible[:self.max_pages]]
                for ref, url in targets:
                    if self.closed:
                        break
                    try:
                        known[ref] = await asyncio.to_thread(parse_set, await self.fetch(url), ref, url)
                        added += 1
                        self.retry_after.pop(ref, None)
                    except (BuildError, ValueError, aiohttp.ClientError, TimeoutError):
                        failed += 1
                        self.retry_after[ref] = time.monotonic() + 600
                    await asyncio.sleep(0.5)
        except (BuildError, ValueError, aiohttp.ClientError, TimeoutError):
            failed += 1
        missing = len(set(refs) - known.keys())
        return tuple(known.values()), (
            f"Tables publiques ajoutées : {added} ; échecs/limites : {failed} ; manquantes : {missing}.",
            "Les bonus absents restent inconnus. /build actualiser poursuit le chargement borné.",
        )
