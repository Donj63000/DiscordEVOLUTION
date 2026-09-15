"""Je consulte quelques pages publiques autorisées et n'en garde que les passages utiles."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
import logging
import re
import socket
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import aiohttp

from utils.dofus_wiki import search_key
from utils.evo_config import EvoError
from utils.evo_safety import clean


log = logging.getLogger(__name__)
WEB_HOSTS = frozenset({
    "wiki.moon-bot.io", "www.dofus-retro.com", "dofus-retro.com", "support.ankama.com",
})
WEB_START_PAGES = (
    "https://wiki.moon-bot.io/",
    "https://www.dofus-retro.com/fr",
    "https://support.ankama.com/hc/fr/articles/360036747353--RETRO-Retrouver-mon-achat-sur-DOFUS-R%C3%A9tro",
)
MAX_PAGE_BYTES = 256 * 1024
MAX_PAGE_TEXT = 60_000
MAX_WEB_PAGES = 2
_URL = re.compile(r"https://[^\s<>\[\]\"']+")


def public_page_url(value):
    """Je refuse comptes, paramètres, ports, autres domaines et chemins non documentaires."""
    if not isinstance(value, str) or len(value) > 350 or any(ord(c) < 33 for c in value):
        raise EvoError("Adresse de source invalide.")
    try:
        parsed = urlsplit(value)
        path = unquote(parsed.path)
        allowed = (parsed.scheme == "https" and parsed.hostname in WEB_HOSTS
                   and parsed.port in (None, 443)
                   and parsed.username is None and parsed.password is None
                   and not parsed.query and not parsed.fragment and "\\" not in path
                   and not any(part in {".", ".."} for part in path.split("/")))
    except ValueError:
        allowed = False
    if not allowed:
        raise EvoError("Evo consulte uniquement les pages publiques Moon-Bot et Dofus Rétro autorisées.")
    if parsed.hostname == "wiki.moon-bot.io":
        valid = path == "/" or bool(re.fullmatch(r"/(?:items|monstres|guides)(?:/[\w-]+)?/?", path))
    elif parsed.hostname == "support.ankama.com":
        valid = bool(re.fullmatch(r"/hc/fr/articles/\d+-[\w-]+/?", path)) and "retro" in search_key(path)
    else:
        valid = path in {"/fr", "/fr/"} or bool(re.fullmatch(
            r"/fr/(?:mmorpg/)?(?:actualites|jeu)(?:/[\w-]+){0,4}/?", path,
        ))
    if not valid:
        raise EvoError("Cette adresse ne désigne pas une page documentaire Dofus Rétro autorisée.")
    return urlunsplit(("https", parsed.hostname, parsed.path or "/", "", ""))


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Une source autorisée ne peut pas rediriger sa résolution vers le réseau privé."""

    def __init__(self):
        self.resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if host not in WEB_HOSTS:
            raise OSError("Source non autorisée")
        records = await self.resolver.resolve(host, port, family)
        if not records or any(not ipaddress.ip_address(row["host"]).is_global for row in records):
            raise OSError("Adresse réseau non publique")
        return records

    async def close(self):
        await self.resolver.close()


class PageText(HTMLParser):
    """Je retire les zones interactives, scripts et contenus masqués avant toute sélection."""

    ignored = frozenset({"script", "style", "noscript", "nav", "footer", "form", "svg",
                         "iframe", "template"})
    void = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"})
    blocks = frozenset({"p", "h1", "h2", "h3", "h4", "li", "tr", "div", "section", "article", "main", "br"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.title = []
        self.links = []
        self.size = 0

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        hidden = (tag in self.ignored or "hidden" in attrs
                  or (attrs.get("aria-hidden") or "").strip().lower() == "true"
                  or re.search(r"display\s*:\s*none|(?:content-)?visibility\s*:\s*hidden",
                               attrs.get("style") or "", re.I))
        suppressed = bool(hidden or self.stack and self.stack[-1][1])
        if not suppressed and tag == "a" and attrs.get("href") and len(self.links) < 150:
            self.links.append(attrs["href"])
        if not suppressed and tag in self.blocks:
            self.parts.append("\n")
        if tag not in self.void:
            self.stack.append((tag, suppressed))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in self.blocks and not (self.stack and self.stack[-1][1]):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.size >= MAX_PAGE_TEXT or self.stack and self.stack[-1][1]:
            return
        text = data[:MAX_PAGE_TEXT - self.size]
        self.size += len(text)
        if any(tag == "title" for tag, _ in self.stack):
            self.title.append(text)
        else:
            self.parts.append(text)


def extract_page(html, url):
    parser = PageText()
    parser.feed(html)
    blocks = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    passages = []
    for block in blocks:
        if len(block) < 35:
            continue
        for offset in range(0, len(block), 320):
            passage = clean(block[offset:offset + 320], 320)
            if passage not in passages:
                passages.append(passage)
    if not passages:
        raise EvoError("La page ne fournit pas de texte documentaire lisible. Aucun contenu inventé.")
    links = []
    for href in parser.links:
        try:
            linked = public_page_url(urljoin(url, href))
        except EvoError:
            continue
        if linked not in links and linked != url:
            links.append(linked)
    return {
        "titre": clean(" ".join(parser.title), 160), "source": url,
        "passages": passages, "liens": links,
        "consulte_le": datetime.now(timezone.utc).isoformat(),
        "texte_partiel": parser.size >= MAX_PAGE_TEXT,
    }


def page_excerpt(page, question):
    words = {word for word in search_key(question).split() if len(word) >= 4} - {
        "avec", "pour", "dans", "quelle", "quelles", "quels", "comment", "dofus", "retro",
    }
    ranked = sorted(enumerate(page["passages"]), key=lambda row: (
        -sum(word in search_key(row[1]).split() for word in words), row[0],
    ))[:5]
    ranked.sort()
    links = sorted(page["liens"], key=lambda url: -sum(word in search_key(unquote(url)) for word in words))[:6]
    return {
        "titre": page["titre"], "source": page["source"],
        "extraits": [text for _, text in ranked], "liens_documentaires": links,
        "consulte_le": page["consulte_le"],
        "couverture": "Extraits ciblés, pas la page complète. La date de consultation n'est pas celle de publication.",
        "texte_partiel": page["texte_partiel"],
    }


class EvoWeb:
    def __init__(self, *, fetch=None, clock=time.monotonic):
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.cache = OrderedDict()
        self.slots = asyncio.Semaphore(2)

    async def _fetch(self, url):
        resolver = PublicResolver()
        connector = aiohttp.TCPConnector(resolver=resolver, limit=2)
        try:
            async with aiohttp.ClientSession(
                connector=connector, cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
                timeout=aiohttp.ClientTimeout(total=10, connect=5),
                headers={"User-Agent": "EvolutionBOT/1.0 (Dofus Retro public documentation)"},
            ) as session:
                current = url
                for redirect in range(3):
                    current = public_page_url(current)
                    async with session.get(current, allow_redirects=False) as response:
                        if response.status in {301, 302, 303, 307, 308} and redirect < 2:
                            location = response.headers.get("Location")
                            if not location:
                                raise EvoError("La source renvoie une redirection inexploitable.")
                            current = public_page_url(urljoin(current, location))
                            continue
                        if response.status != 200:
                            log.debug("evo web unavailable host=%s status=%s", urlsplit(current).hostname, response.status)
                            raise EvoError("Cette page refuse la lecture automatique ou est indisponible. Aucun contournement ni contenu inventé.")
                        if response.content_type not in {"text/html", "application/xhtml+xml"}:
                            raise EvoError("Seules les pages HTML publiques sont consultées.")
                        payload = bytearray()
                        async for chunk in response.content.iter_chunked(16384):
                            payload.extend(chunk)
                            if len(payload) > MAX_PAGE_BYTES:
                                raise EvoError("La page est trop volumineuse pour cette consultation ciblée.")
                        return payload.decode("utf-8", errors="replace"), current
                raise EvoError("La source redirige trop souvent.")
        except (aiohttp.ClientError, OSError, TimeoutError):
            log.debug("evo web transport unavailable host=%s", urlsplit(url).hostname)
            raise EvoError("La source Web ne répond pas. Les outils du catalogue restent prioritaires.") from None
        finally:
            await resolver.close()

    async def read(self, ctx, url, question):
        await ctx.ensure_access()
        url = public_page_url(url)
        requested = set()
        for value in _URL.findall(ctx.request_text):
            try:
                requested.add(public_page_url(value.rstrip(".,;!?)")))
            except EvoError:
                continue
        known = set(WEB_START_PAGES) | ctx.sources | ctx.web_links | requested
        if url not in known:
            raise EvoError("Choisis une adresse demandée par le membre, fournie par les outils ou présente dans les liens d'une page déjà consultée.")
        if url not in ctx.web_pages and len(ctx.web_pages) >= MAX_WEB_PAGES:
            raise EvoError("Deux pages Web ont déjà été consultées pour cette demande. Utilise leurs résultats.")
        ctx.web_pages.add(url)
        async with self.slots:
            cached = self.cache.get(url)
            if cached and self.clock() - cached[0] < 600:
                page = cached[1]
                self.cache.move_to_end(url)
                log.debug("evo web cache hit host=%s", urlsplit(url).hostname)
            else:
                html, final_url = await self.fetch(url)
                page = extract_page(html, public_page_url(final_url))
                self.cache[url] = (self.clock(), page)
                self.cache.move_to_end(url)
                while len(self.cache) > 64:
                    self.cache.popitem(last=False)
                log.debug("evo web page read host=%s passages=%s", urlsplit(url).hostname, len(page["passages"]))
        await ctx.ensure_access()
        result = page_excerpt(page, question)
        ctx.web_links.update(result["liens_documentaires"])
        ctx.source(result["source"])
        return result
