"""Consultation Web bornée et sans réseau, avec sources et HTTP entièrement simulés."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from utils.evo_config import EvoError
from utils.evo_web import (
    EvoWeb, MAX_PAGE_BYTES, MAX_PAGE_TEXT, PublicResolver, WEB_START_PAGES,
    extract_page, page_excerpt, public_page_url,
)


START = "https://wiki.moon-bot.io/"
PAGE = "https://wiki.moon-bot.io/items/objet-test/"
OTHER = "https://wiki.moon-bot.io/monstres/monstre-test/"
THIRD = "https://wiki.moon-bot.io/guides/guide-test/"
HTML = "<title>Documentation</title><p>Une description publique assez longue pour fournir un extrait documentaire.</p>"


class Context:
    def __init__(self, *, request="", sources=()):
        self.request_text = request
        self.sources = set(sources)
        self.web_links = set()
        self.web_pages = set()
        self.allowed = True
        self.checks = 0

    async def ensure_access(self):
        self.checks += 1
        if not self.allowed:
            raise EvoError("Accès retiré")

    def source(self, url):
        self.sources.add(url)


class Response:
    def __init__(self, *, status=200, body=HTML.encode(), content_type="text/html", location=None):
        self.status = status
        self.content_type = content_type
        self.headers = {"Location": location} if location is not None else {}
        self.body = body
        self.content = self
        self.bytes_read = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size):
        for index in range(0, len(self.body), size):
            chunk = self.body[index:index + size]
            self.bytes_read += len(chunk)
            yield chunk


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


class PublicPageTests(unittest.TestCase):
    def test_documented_urls_are_allowed_and_default_port_is_canonical(self):
        for url in (*WEB_START_PAGES, PAGE, OTHER, THIRD,
                    "https://www.dofus-retro.com/fr/mmorpg/actualites/news/123-retro"):
            with self.subTest(url=url):
                self.assertEqual(public_page_url(url), url)
        self.assertEqual(public_page_url("https://wiki.moon-bot.io:443/items/objet-test/"), PAGE)

    def test_scheme_host_credentials_query_and_undocumented_paths_are_rejected(self):
        rejected = (
            "http://wiki.moon-bot.io/", "https://wiki.moon-bot.io.evil.example/",
            "https://evil.wiki.moon-bot.io/", "https://127.0.0.1/", "https://[::1]/",
            "https://user@wiki.moon-bot.io/", "https://@wiki.moon-bot.io/",
            "https://:password@wiki.moon-bot.io/", "https://wiki.moon-bot.io:444/",
            "https://wiki.moon-bot.io/?q=retro", "https://wiki.moon-bot.io/#private",
            "https://wiki.moon-bot.io/items/../account", "https://wiki.moon-bot.io/items/%2e%2e/",
            "https://wiki.moon-bot.io/items/%252e%252e/", "https://wiki.moon-bot.io/items/a%5Cb/",
            "https://wiki.moon-bot.io/account", "https://www.dofus-retro.com/fr/connexion",
            "https://support.ankama.com/hc/fr/articles/123-Dofus-2",
            "https://wiki.moon-bot.io/\n", "https://wiki.moon-bot.io:invalid/",
        )
        for url in rejected:
            with self.subTest(url=url), self.assertRaises(EvoError):
                public_page_url(url)

    def test_html_removes_scripts_templates_interactive_and_hidden_content(self):
        hidden = "INVISIBLE secret documentation assez longue pour créer un extrait parasite."
        sections = [f"<{tag}>{hidden}</{tag}>" for tag in (
            "script", "style", "noscript", "nav", "footer", "form", "svg", "iframe", "template",
        )]
        sections += [f"<div {attr}><span>{hidden}</span></div>" for attr in (
            "hidden", "aria-hidden=' TRUE '", "style='display : none'",
            "style='visibility: hidden'", "style='content-visibility: hidden'",
        )]
        page = extract_page(HTML + "".join(sections), PAGE)
        self.assertNotIn("INVISIBLE", " ".join(page["passages"]))
        self.assertIn("description publique", " ".join(page["passages"]))
        self.assertEqual(page["titre"], "Documentation")

    def test_valueless_attributes_do_not_break_html_extraction(self):
        page = extract_page("<div aria-hidden style>" + HTML + "</div>", PAGE)
        self.assertTrue(page["passages"])

    def test_links_are_only_visible_documentary_urls(self):
        html = HTML + (
            "<a href='/monstres/monstre-test/'>Lien</a>"
            "<a href='https://private.example/'>Autre</a>"
            "<a href='/account'>Compte</a>"
            "<div hidden><a href='/guides/guide-test/'>Caché</a></div>"
        )
        self.assertEqual(extract_page(html, PAGE)["liens"], [OTHER])

    def test_five_short_excerpts_rank_relevant_facts_and_preserve_source_order(self):
        passages = [f"Paragraphe {index} : " + "documentation générale " * 24 for index in range(8)]
        passages[5] = "Crocabulia possède plusieurs grades dans ce document de test. " * 8
        page = extract_page("".join(f"<p>{text}</p>" for text in passages), PAGE)
        result = page_excerpt(page, "Quels sont les grades du Crocabulia ?")
        self.assertEqual(len(result["extraits"]), 5)
        self.assertTrue(all(len(text) <= 320 for text in result["extraits"]))
        self.assertTrue(any("Crocabulia" in text for text in result["extraits"]))
        positions = [page["passages"].index(text) for text in result["extraits"]]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(result["source"], PAGE)
        self.assertNotIn("passages", result)

    def test_empty_or_hidden_only_pages_are_not_invented(self):
        with self.assertRaises(EvoError):
            extract_page("<script>" + "Texte caché " * 10 + "</script>", PAGE)

    def test_text_truncation_is_reported(self):
        page = extract_page("<p>" + "Texte documentaire " * MAX_PAGE_TEXT + "</p>", PAGE)
        self.assertTrue(page["texte_partiel"])
        self.assertTrue(all(len(text) <= 320 for text in page["passages"]))


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_loopback_link_local_and_mixed_dns_are_rejected(self):
        resolver = object.__new__(PublicResolver)
        for addresses in (("127.0.0.1",), ("10.0.0.1",), ("169.254.169.254",),
                          ("::1",), ("fc00::1",), ("8.8.8.8", "192.168.1.1")):
            resolver.resolver = SimpleNamespace(resolve=AsyncMock(
                return_value=[{"host": address} for address in addresses],
            ))
            with self.subTest(addresses=addresses), self.assertRaises(OSError):
                await resolver.resolve("wiki.moon-bot.io", 443)

    async def test_unlisted_host_is_blocked_before_dns(self):
        resolver = object.__new__(PublicResolver)
        resolver.resolver = SimpleNamespace(resolve=AsyncMock())
        with self.assertRaises(OSError):
            await resolver.resolve("localhost", 443)
        resolver.resolver.resolve.assert_not_awaited()

    async def test_public_dns_and_close_use_resolver(self):
        resolver = object.__new__(PublicResolver)
        records = [{"host": "8.8.8.8"}]
        resolver.resolver = SimpleNamespace(resolve=AsyncMock(return_value=records), close=AsyncMock())
        self.assertEqual(await resolver.resolve("wiki.moon-bot.io", 443), records)
        await resolver.close()
        resolver.resolver.close.assert_awaited_once()


class WebFetchTests(unittest.IsolatedAsyncioTestCase):
    async def run_fetch(self, responses, url=PAGE):
        self.session = Session(responses)
        self.resolver = SimpleNamespace(close=AsyncMock())
        with patch("utils.evo_web.PublicResolver", return_value=self.resolver), \
                patch("utils.evo_web.aiohttp.TCPConnector"), \
                patch("utils.evo_web.aiohttp.ClientSession", return_value=self.session) as factory:
            try:
                return await EvoWeb()._fetch(url)
            finally:
                self.resolver.close.assert_awaited_once()
                self.assertFalse(factory.call_args.kwargs["trust_env"])

    async def test_allowed_redirect_is_checked_and_automatic_redirects_are_disabled(self):
        html, url = await self.run_fetch([Response(status=302, location=OTHER), Response()])
        self.assertEqual((html, url), (HTML, OTHER))
        self.assertEqual(self.session.calls, [
            (PAGE, {"allow_redirects": False}), (OTHER, {"allow_redirects": False}),
        ])

    async def test_redirect_to_private_or_account_host_stops_before_next_get(self):
        for location in ("https://127.0.0.1/", "https://evil.example/", "/account", "?token=secret"):
            with self.subTest(location=location), self.assertRaises(EvoError):
                await self.run_fetch([Response(status=302, location=location)])
            self.assertEqual(len(self.session.calls), 1)

    async def test_redirect_chain_is_bounded(self):
        with self.assertRaises(EvoError):
            await self.run_fetch([Response(status=302, location=OTHER) for _ in range(3)])
        self.assertEqual(len(self.session.calls), 3)

    async def test_403_is_reported_without_reading_or_circumvention(self):
        denied = Response(status=403)
        with self.assertRaises(EvoError):
            await self.run_fetch([denied])
        self.assertEqual((len(self.session.calls), denied.bytes_read), (1, 0))

    async def test_non_html_content_is_rejected_before_reading(self):
        response = Response(content_type="application/json")
        with self.assertRaises(EvoError):
            await self.run_fetch([response])
        self.assertEqual(response.bytes_read, 0)

    async def test_oversized_page_is_stopped_during_streaming(self):
        response = Response(body=b"x" * (MAX_PAGE_BYTES * 2))
        with self.assertRaises(EvoError):
            await self.run_fetch([response])
        self.assertLessEqual(response.bytes_read, MAX_PAGE_BYTES + 16384)


class WebReadingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fetch = AsyncMock(side_effect=lambda url: (HTML, url))
        self.now = 1000.0
        self.web = EvoWeb(fetch=self.fetch, clock=lambda: self.now)

    async def test_unknown_invented_url_is_rejected_before_fetch(self):
        with self.assertRaises(EvoError):
            await self.web.read(Context(), PAGE, "documentation")
        self.fetch.assert_not_awaited()

    async def test_user_url_known_tool_source_and_start_page_are_allowed(self):
        for ctx, url in ((Context(request=f"Consulte {PAGE}."), PAGE),
                         (Context(sources={OTHER}), OTHER), (Context(), START)):
            with self.subTest(url=url):
                result = await self.web.read(ctx, url, "documentation")
                self.assertEqual(result["source"], url)
                self.assertIn(url, ctx.sources)
                self.assertEqual(ctx.checks, 2)

    async def test_only_extracted_visible_links_enable_followup_url(self):
        self.fetch.side_effect = lambda url: (HTML + f"<a href='{OTHER}'>Suite</a>", url)
        ctx = Context(sources={PAGE})
        await self.web.read(ctx, PAGE, "documentation")
        self.assertIn(OTHER, ctx.web_links)
        await self.web.read(ctx, OTHER, "documentation")
        with self.assertRaises(EvoError):
            await self.web.read(ctx, THIRD, "documentation")
        self.assertEqual(self.fetch.await_count, 2)

    async def test_two_page_limit_is_enforced_even_for_known_urls(self):
        ctx = Context(sources={PAGE, OTHER, THIRD})
        await self.web.read(ctx, PAGE, "documentation")
        await self.web.read(ctx, OTHER, "documentation")
        with self.assertRaises(EvoError):
            await self.web.read(ctx, THIRD, "documentation")
        self.assertEqual(ctx.web_pages, {PAGE, OTHER})
        self.assertEqual(self.fetch.await_count, 2)

    async def test_concurrent_calls_cannot_pass_two_page_limit(self):
        ctx = Context(sources={PAGE, OTHER, THIRD})
        results = await asyncio.gather(*(
            self.web.read(ctx, url, "documentation") for url in (PAGE, OTHER, THIRD)
        ), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 2)
        self.assertEqual(sum(isinstance(result, EvoError) for result in results), 1)
        self.assertEqual(self.fetch.await_count, 2)

    async def test_cache_expires_at_ten_minutes_and_does_not_bypass_known_sources(self):
        await self.web.read(Context(sources={PAGE}), PAGE, "documentation")
        self.now += 599
        await self.web.read(Context(sources={PAGE}), PAGE, "documentation")
        self.assertEqual(self.fetch.await_count, 1)
        with self.assertRaises(EvoError):
            await self.web.read(Context(), PAGE, "documentation")
        self.now += 1
        await self.web.read(Context(sources={PAGE}), PAGE, "documentation")
        self.assertEqual(self.fetch.await_count, 2)

    async def test_lost_access_after_fetch_never_publishes_source_or_links(self):
        ctx = Context(request=PAGE)

        def revoke(url):
            ctx.allowed = False
            return HTML + f"<a href='{OTHER}'>Suite</a>", url

        self.fetch.side_effect = revoke
        with self.assertRaises(EvoError):
            await self.web.read(ctx, PAGE, "documentation")
        self.assertEqual(ctx.sources, set())
        self.assertEqual(ctx.web_links, set())

    async def test_failed_fetch_does_not_create_cache_or_verified_source(self):
        self.fetch.side_effect = EvoError("Page refusée")
        ctx = Context(request=PAGE)
        with self.assertRaises(EvoError):
            await self.web.read(ctx, PAGE, "documentation")
        self.assertEqual(self.web.cache, {})
        self.assertEqual(ctx.sources, set())
