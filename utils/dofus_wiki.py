"""Je consulte le wiki Rétro avec un cache borné et des identités vérifiées."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import json
import logging
import re
import time
import unicodedata
from urllib.parse import urlsplit

import aiohttp
from rapidfuzz.fuzz import WRatio


log = logging.getLogger(__name__)
WIKI_ORIGIN = "https://wiki.moon-bot.io"
INDEX_PATHS = ("/api/items.json", "/api/monsters.json", "/api/search-index.json")
EQUIPMENT_TYPES = (
    "Amulette", "Anneau", "Arc", "Arbalète", "Arme magique", "Baguette", "Bâton",
    "Botte", "Bouclier", "Cape", "Ceinture", "Chapeau", "Dague", "Dofus", "Epée",
    "Familier", "Faux", "Hache", "Marteau", "Outil", "Pelle", "Pioche", "Sac à dos",
)
EQUIPMENT_ALIASES = {
    "coiffe": "Chapeau", "coiffes": "Chapeau", "bottes": "Botte",
    "epee": "Epée", "epees": "Epée", "dagues": "Dague", "sac": "Sac à dos",
    "anneaux": "Anneau", "marteaux": "Marteau", "chapeaux": "Chapeau",
    "sacs a dos": "Sac à dos", "armes magiques": "Arme magique",
}


def clean_text(value: object) -> str:
    """Je retire les échappements de la source sans interpréter son contenu."""
    text = unescape(str(value or ""))
    text = re.sub(r"\\+(['\"])", r"\1", text)
    return " ".join(text.split())


def search_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", clean_text(value).casefold())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def wiki_path(value: object, section: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc != "wiki.moon-bot.io":
            return None
    if parsed.query or parsed.fragment:
        return None
    pattern = rf"/{section}/[a-z0-9][a-z0-9_-]*(?:\.(?:png|webp|jpg))?/?"
    return parsed.path if re.fullmatch(pattern, parsed.path) else None


class WikiError(Exception):
    """Je fournis une explication lisible lorsqu'une fiche n'est pas exploitable."""


@dataclass(frozen=True)
class WikiEntry:
    kind: str
    identifier: str
    name: str
    category: str
    level: int | str
    path: str
    key: str
    variant: int = 0
    variant_count: int = 0

    @property
    def token(self) -> str:
        return f"{self.kind}:{self.identifier}"

    @property
    def url(self) -> str:
        return WIKI_ORIGIN + self.path

    @property
    def label(self) -> str:
        base = f"{self.name} · Niv. {self.level} · {self.category}"
        if self.variant:
            return f"{base[:75]} · Variante {self.variant}/{self.variant_count}"
        return base[:100]


@dataclass(frozen=True)
class WikiDetail:
    entry: WikiEntry
    data: dict
    icon: str | None
    stale: bool


@dataclass
class CacheEntry:
    value: object
    fetched_at: float


class MonsterPage(HTMLParser):
    """Je relève uniquement le lien JSON et l'image de la fiche sélectionnée."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.detail_paths: set[str] = set()
        self.icon: str | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        href = attributes.get("href") or ""
        if tag == "a" and re.fullmatch(r"/api/monster/[1-9][0-9]*\.json", href):
            self.detail_paths.add(href)
        if tag == "meta" and attributes.get("property") == "og:image":
            path = wiki_path(attributes.get("content"), "icons")
            if path:
                self.icon = WIKI_ORIGIN + path


def parse_entries(data: object, kind: str) -> tuple[WikiEntry, ...]:
    if not isinstance(data, list) or not data or len(data) > 50000:
        raise WikiError("Le catalogue du wiki est momentanément illisible. Réessaie plus tard.")
    entries = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        path = wiki_path(row.get("url"), "items" if kind == "item" else "monstres")
        name = clean_text(row.get("name"))
        if not path or not name or len(name) > 250:
            continue
        if kind == "item":
            identifier, level = row.get("id"), row.get("level")
            if type(identifier) is not int or identifier <= 0:
                continue
            if type(level) is not int or not 0 <= level <= 1000:
                level = "?"
            category = clean_text(row.get("type")) or "Objet"
        else:
            identifier = path.strip("/").split("/")[-1]
            level = clean_text(row.get("level_min")) or "?"
            category = "Monstre"
        entry = WikiEntry(kind, str(identifier), name, category, level, path, search_key(name))
        entries[entry.token] = entry
    if not entries:
        raise WikiError("Le catalogue du wiki ne contient aucune fiche exploitable.")
    if kind == "monster":
        groups: dict[str, list[WikiEntry]] = {}
        for entry in entries.values():
            groups.setdefault(entry.label[:75], []).append(entry)
        for group in groups.values():
            if len(group) > 1:
                for index, entry in enumerate(sorted(group, key=lambda item: item.path), 1):
                    entries[entry.token] = replace(
                        entry, variant=index, variant_count=len(group),
                    )
    log.debug("Wiki: index_parsed kind=%s entries=%s", kind, len(entries))
    return tuple(entries.values())


def parse_icons(data: object) -> dict[str, str]:
    if not isinstance(data, list) or len(data) > 50000:
        raise WikiError("L'index des images est indisponible.")
    icons = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        path = wiki_path(row.get("u"), "items") or wiki_path(row.get("u"), "monstres")
        icon = wiki_path(row.get("i"), "icons")
        if path and icon:
            icons[path] = WIKI_ORIGIN + icon
    return icons


def find_entries(entries: tuple[WikiEntry, ...], query: str, limit: int | None = None):
    """Je distingue les correspondances exactes des suggestions approximatives."""
    query = query.strip()
    if query.startswith(("item:", "monster:")):
        return [entry for entry in entries if entry.token == query], False
    key = search_key(query)
    if not key:
        return sorted(entries, key=lambda entry: (entry.key, str(entry.level)))[:limit], False
    exact = [entry for entry in entries if entry.key == key]
    if exact:
        return exact[:limit], False
    matches = [entry for entry in entries if all(word in entry.key for word in key.split())]
    if matches:
        matches.sort(key=lambda entry: (not entry.key.startswith(key), entry.key, entry.path))
        return matches[:limit], False
    if len(key) < 3:
        return [], False
    scored = [(WRatio(key, entry.key), entry) for entry in entries]
    scored.sort(key=lambda pair: (-pair[0], pair[1].key, pair[1].path))
    suggestion_limit = 25 if limit is None else min(limit, 25)
    return [entry for score, entry in scored if score >= 75][:suggestion_limit], True


def equipment_category(value: str) -> str:
    key = search_key(value)
    if key in EQUIPMENT_ALIASES:
        return EQUIPMENT_ALIASES[key]
    for category in EQUIPMENT_TYPES:
        if key in {search_key(category), search_key(category) + "s"}:
            return category
    raise WikiError("Choisis un type d'équipement proposé, par exemple Chapeau, Anneau ou Botte.")


def equipment_suggestions(value: str, limit: int = 25) -> list[str]:
    """Je propose les mêmes catégories avec leur nom courant ou leur nom officiel."""
    key = search_key(value)
    suggestions = []
    for category in EQUIPMENT_TYPES:
        names = {search_key(category), search_key(category) + "s"}
        names.update(alias for alias, target in EQUIPMENT_ALIASES.items() if target == category)
        if any(key in name for name in names):
            suggestions.append(category)
    return suggestions[:limit]


def retry_after_delay(value: str, *, now: float | None = None) -> float:
    """Je respecte les deux formats HTTP du délai, avec une attente de 1 minute à 1 jour."""
    value = value.strip()
    delay = 60.0
    if re.fullmatch(r"[0-9]+", value):
        delay = float(value)
    else:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is not None:
                delay = retry_at.timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            pass
    return min(86400.0, max(60.0, delay))


class DofusWikiClient:
    """Je partage les téléchargements concurrents et je garde un secours limité à un jour."""

    def __init__(self, *, ttl: float = 3600, timeout: float = 10, clock=time.monotonic):
        self.ttl = max(60, min(float(ttl), 86400))
        self.timeout = max(1, min(float(timeout), 30))
        self.clock = clock
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task] = {}
        self._retry_at: OrderedDict[str, float] = OrderedDict()
        self._semaphore = asyncio.Semaphore(2)
        self._session: aiohttp.ClientSession | None = None
        self._closed = False
        self._backoff_until = 0.0

    def peek(self, path: str):
        cached = self._cache.get(path)
        if cached and self.clock() - cached.fetched_at <= 86400:
            return cached.value
        return None

    def is_stale(self, path: str) -> bool:
        cached = self._cache.get(path)
        return cached is None or self.clock() - cached.fetched_at >= self.ttl

    async def _fetch(self, path: str, *, html=False):
        allowed = (
            r"/api/(?:items|monsters|search-index)\.json"
            r"|/api/(?:item|monster)/[1-9][0-9]*\.json"
            r"|/monstres/[a-z0-9][a-z0-9_-]*/"
        )
        if not re.fullmatch(allowed, path):
            raise WikiError("Cette adresse ne correspond pas à une fiche du wiki.")
        if self._closed:
            raise WikiError("L'encyclopédie est en cours de redémarrage. Réessaie dans un instant.")
        async with asyncio.timeout(self.timeout), self._semaphore:
            if self._backoff_until > self.clock():
                raise WikiError("Le wiki reçoit trop de demandes. Réessaie un peu plus tard.")
            if self._session is None:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    headers={"User-Agent": "EvolutionBOT/1.0 (Dofus Retro encyclopedia)"},
                    cookie_jar=aiohttp.DummyCookieJar(),
                )
            log.debug("Wiki: request path=%s", path)
            async with self._session.get(WIKI_ORIGIN + path, allow_redirects=False) as response:
                if response.status != 200:
                    log.debug("Wiki: http_error path=%s status=%s", path, response.status)
                    if response.status == 429:
                        delay = retry_after_delay(response.headers.get("Retry-After", "60"))
                        self._backoff_until = max(self._backoff_until, self.clock() + delay)
                        log.debug("Wiki: rate_limited path=%s retry_after=%s", path, delay)
                    if response.status == 404:
                        raise WikiError("Cette fiche n'est plus disponible sur le wiki.")
                    raise WikiError("Le wiki ne répond pas pour le moment. Réessaie dans une minute.")
                expected = "text/html" if html else "application/json"
                if response.content_type != expected:
                    raise WikiError("Le wiki a renvoyé une réponse inattendue. Réessaie plus tard.")
                payload = bytearray()
                max_bytes = 8 * 1024 * 1024 if path in INDEX_PATHS else 256 * 1024
                async for chunk in response.content.iter_chunked(65536):
                    payload.extend(chunk)
                    if len(payload) > max_bytes:
                        raise WikiError("La réponse du wiki est trop volumineuse pour être consultée.")
                try:
                    decoded = payload.decode("utf-8-sig")
                    return decoded if html else json.loads(decoded)
                except (ValueError, UnicodeError) as exc:
                    raise WikiError("La réponse du wiki est illisible. Réessaie plus tard.") from exc

    async def _refresh(self, path, parser, html):
        try:
            value = parser(await self._fetch(path, html=html))
            self._cache[path] = CacheEntry(value, self.clock())
            self._cache.move_to_end(path)
            while len(self._cache) > 259:
                oldest = next(key for key in self._cache if key not in INDEX_PATHS)
                del self._cache[oldest]
            self._retry_at.pop(path, None)
            log.debug("Wiki: cache_refreshed path=%s", path)
            return value
        except (aiohttp.ClientError, asyncio.TimeoutError, WikiError) as exc:
            self._retry_at[path] = self.clock() + 30
            self._retry_at.move_to_end(path)
            while len(self._retry_at) > 259:
                self._retry_at.popitem(last=False)
            fallback = self.peek(path)
            if fallback is not None:
                log.debug("Wiki: cached_fallback path=%s reason=%s", path, type(exc).__name__)
                return fallback
            log.debug("Wiki: unavailable path=%s reason=%s", path, type(exc).__name__)
            if isinstance(exc, WikiError):
                raise
            raise WikiError("Le wiki ne répond pas pour le moment. Réessaie dans une minute.") from exc

    async def resource(self, path, parser=lambda value: value, *, html=False):
        cached = self.peek(path)
        if cached is not None and not self.is_stale(path):
            return cached
        if self._retry_at.get(path, 0) > self.clock():
            if cached is not None:
                return cached
            raise WikiError("Le wiki est temporairement indisponible. Réessaie dans une minute.")
        task = self._inflight.get(path)
        if task is None:
            task = asyncio.create_task(self._refresh(path, parser, html))
            self._inflight[path] = task

            def finished(completed):
                self._inflight.pop(path, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def items(self) -> tuple[WikiEntry, ...]:
        return await self.resource(INDEX_PATHS[0], lambda data: parse_entries(data, "item"))

    async def monsters(self) -> tuple[WikiEntry, ...]:
        return await self.resource(INDEX_PATHS[1], lambda data: parse_entries(data, "monster"))

    async def warmup(self):
        outcomes = await asyncio.gather(
            self.items(), self.monsters(), self.resource(INDEX_PATHS[2], parse_icons),
            return_exceptions=True,
        )
        for path, outcome in zip(INDEX_PATHS, outcomes):
            if isinstance(outcome, Exception):
                log.debug("Wiki: warmup_deferred path=%s reason=%s", path, type(outcome).__name__)

    async def detail(self, entry: WikiEntry) -> WikiDetail:
        icon = (self.peek(INDEX_PATHS[2]) or {}).get(entry.path)
        if entry.kind == "item":
            path = f"/api/item/{entry.identifier}.json"
        else:
            def parse_page(text):
                page = MonsterPage()
                page.feed(text)
                if len(page.detail_paths) != 1:
                    raise WikiError("La fiche de ce monstre ne permet pas de retrouver ses statistiques.")
                return next(iter(page.detail_paths)), page.icon

            path, page_icon = await self.resource(entry.path, parse_page, html=True)
            icon = icon or page_icon

        def parse_detail(data):
            if not isinstance(data, dict) or data.get("url") != entry.url:
                raise WikiError("La fiche reçue ne correspond pas à la sélection. Réessaie plus tard.")
            if search_key(data.get("name")) != entry.key:
                raise WikiError("Le nom de la fiche a changé. Relance ta recherche dans un instant.")
            if entry.kind == "item" and data.get("id") != int(entry.identifier):
                raise WikiError("L'identifiant de cette fiche est incohérent.")
            if entry.kind == "monster" and path != f"/api/monster/{data.get('id')}.json":
                raise WikiError("L'identifiant de ce monstre est incohérent.")
            return data

        data = await self.resource(path, parse_detail)
        parse_detail(data)
        index_path = INDEX_PATHS[1 if entry.kind == "monster" else 0]
        stale = self.is_stale(path) or self.is_stale(index_path)
        return WikiDetail(entry, data, icon, stale)

    async def close(self):
        self._closed = True
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._session:
            await self._session.close()
        log.debug("Wiki: client_closed")
