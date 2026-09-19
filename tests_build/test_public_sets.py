"""Contrats HTML SYNTHÉTIQUES, pas des captures ni une recette HTTP Xixou réelle."""
from unittest.mock import AsyncMock
import pytest
from utils.build.models import BuildError
from utils.build.set_source import (INDEX, MAX_BYTES, PublicSetLoader, allowed_url,
                                    document, parse_index, parse_set)

URL = INDEX + 'panoplie-test/'
REF = 'panoplie test'
HTML = '''<html><body><h1>Panoplie Test</h1>
<p>Cette panoplie est composée de 3 pièces. Elle active des bonus, de 2 à 3 pièces.</p>
<h2>Bonus de panoplie</h2>
<h3>2 pièces |</h3><p>+20 en force</p>
<h3>3 pièces |</h3><p>+40 en force</p><p>+1 PA</p>
<a href="/encyclopedie/panoplies/">Toutes les panoplies</a></body></html>'''
INDEX_HTML = '<a href="/encyclopedie/panoplies/panoplie-test/">Panoplie Test</a>'


def test_complete_table_uses_total_not_increment():
    table = parse_set(HTML, REF, URL)
    assert [t.pieces for t in table.tiers] == [1, 2, 3]
    assert table.tiers[0].effects == ()
    assert {e.stat: e.high for e in table.tiers[-1].effects} == {'fo': 40, 'pa': 1}
    assert table.source == URL


@pytest.mark.parametrize('raw', [
    HTML.replace('Panoplie Test', 'Autre identité'),
    HTML.replace('3 pièces |', '2 pièces |'),
    HTML.replace('+40 en force', '+40 à 50 en force'),
    HTML.replace('+40 en force', 'Effet mystérieux non interprété'),
    HTML.replace('<h3>3 pièces |</h3>', ''),
    HTML.replace('composée de 3', 'composée de 4'),
    HTML.replace('active des bonus, de 2 à 3 pièces', 'bonus possibles'),
    HTML.replace('<h3>3 pièces |</h3><p>+40 en force</p><p>+1 PA</p>', ''),
    HTML.replace('Toutes les panoplies', 'Texte interrompu'),
    HTML.replace('Bonus de panoplie', 'Bonus inconnu'),
    HTML.replace('+20 en force', ''),
    HTML.replace('2 pièces |', '17 pièces |'),
 ])
def test_rejected(raw):
    with pytest.raises(BuildError):
        parse_set(raw, REF, URL)


@pytest.mark.parametrize('url', [
    'http://xixou.io/encyclopedie/panoplies/',
    'https://evil.invalid/encyclopedie/panoplies/',
    'https://xixou.io.evil.invalid/encyclopedie/panoplies/',
    'https://xixou.io:443/encyclopedie/panoplies/',
    'https://user:pass@xixou.io/encyclopedie/panoplies/',
    INDEX + '?token=secret', INDEX + '#test', INDEX + '../api/',
    INDEX + '%2e%2e/', 'https://xixou.io/api/equipements',
])
def test_disallowed_url(url):
    assert allowed_url(url) is False
    with pytest.raises(BuildError):
        parse_set(HTML, REF, url)


def test_index_only_uses_verified_host_and_unambiguous_names():
    raw = INDEX_HTML + '''<a href="https://evil.invalid/x">Pas bon</a>
    <a href="/encyclopedie/panoplies/a/">Collision</a>
    <a href="/encyclopedie/panoplies/b/">Collision</a>
    <a href="/encyclopedie/panoplies/">Retour</a>
    <script><a href="/encyclopedie/panoplies/injection/">Injection</a></script>'''
    assert parse_index(raw) == {REF: URL}
    with pytest.raises(BuildError):
        parse_index('<html>Index vide</html>')


def test_scripts_comments_style_do_not_add_effects():
    raw = HTML.replace('<p>+20 en force</p>', '''<script>+999 PA</script>
    <style>+999 PM</style><!-- +999 Vitalité --><p><span>+20</span> en force</p>''')
    assert parse_set(raw, REF, URL) == parse_set(HTML, REF, URL)


def test_input_size_and_type_are_bounded():
    for raw in (None, 'a' * (MAX_BYTES + 1)):
        with pytest.raises(BuildError):
            document(raw)


@pytest.mark.asyncio
async def test_enrichment_persists_known_and_reuses_cached_index(monkeypatch):
    monkeypatch.setattr('utils.build.set_source.asyncio.sleep', AsyncMock())
    loader = PublicSetLoader()
    loader.fetch = AsyncMock(side_effect=[INDEX_HTML, HTML])
    definitions, notes = await loader.enrich([REF], ())
    assert len(definitions) == 1 and 'ajoutées : 1' in notes[0]
    definitions2, _ = await loader.enrich([REF], definitions)
    assert definitions2 == definitions and loader.fetch.await_count == 2
    # Une seconde table absente de l'index ne recharge pas ce dernier.
    await loader.enrich([REF, 'absente'], definitions)
    assert loader.fetch.await_count == 2


@pytest.mark.asyncio
async def test_failure_keeps_previous_table_and_is_cooled_down(monkeypatch):
    monkeypatch.setattr('utils.build.set_source.asyncio.sleep', AsyncMock())
    table = parse_set(HTML, REF, URL)
    loader = PublicSetLoader()
    loader.fetch = AsyncMock(side_effect=[INDEX_HTML.replace('Test', 'Autre').replace('test/', 'autre/'), BuildError('cassée')])
    definitions, notes = await loader.enrich([REF, 'panoplie autre'], (table,))
    assert definitions == (table,) and 'manquantes : 1' in notes[0]
    await loader.enrich([REF, 'panoplie autre'], definitions)
    assert loader.fetch.await_count == 2


@pytest.mark.asyncio
async def test_failed_first_page_does_not_starve_other_pages(monkeypatch):
    monkeypatch.setattr('utils.build.set_source.asyncio.sleep', AsyncMock())
    index = INDEX_HTML.replace('Panoplie Test', 'A') + INDEX_HTML.replace('Panoplie Test', 'B').replace('test/', 'b/')
    loader = PublicSetLoader(max_pages=1)
    loader.fetch = AsyncMock(side_effect=[index, BuildError('page A cassée'), HTML.replace('Panoplie Test', 'B')])
    tables, _ = await loader.enrich(['a', 'b'], ())
    assert not tables
    tables, _ = await loader.enrich(['a', 'b'], tables)
    assert len(tables) == 1 and tables[0].ref == 'b'


@pytest.mark.asyncio
async def test_enrichment_does_not_swallow_cancellation():
    import asyncio
    loader = PublicSetLoader()
    loader.fetch = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await loader.enrich([REF], ())


class Body:
    def __init__(self, chunks): self.chunks = chunks
    async def iter_chunked(self, size):
        for chunk in self.chunks: yield chunk


class Response:
    def __init__(self, *, status=200, content_type='text/html', length=None, chunks=(b'ok',)):
        self.status, self.content_type, self.content_length = status, content_type, length
        self.content = Body(chunks)
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False


class Session:
    closed = False
    def __init__(self, response): self.response, self.calls = response, []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs)); return self.response
    async def close(self): self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [
    Response(status=302), Response(status=403), Response(content_type='application/json'),
    Response(length=MAX_BYTES + 1), Response(chunks=(b'x' * MAX_BYTES, b'y')),
])
async def test_transport_bounds_without_redirect_or_key(response):
    loader = PublicSetLoader(); loader.session = Session(response)
    with pytest.raises(BuildError): await loader.fetch(URL)
    assert loader.session.calls == [(URL, {'allow_redirects': False})]
    session = loader.session
    await loader.close()
    assert session.closed and loader.session is None


@pytest.mark.asyncio
async def test_transport_utf8_and_url_validation():
    loader = PublicSetLoader(); loader.session = Session(Response(chunks=(b'\xff',)))
    with pytest.raises(UnicodeDecodeError): await loader.fetch(URL)
    with pytest.raises(BuildError): await loader.fetch('https://evil.invalid/')
    assert len(loader.session.calls) == 1


@pytest.mark.asyncio
async def test_closed_loader_cannot_recreate_a_session():
    loader = PublicSetLoader()
    await loader.close()
    with pytest.raises(BuildError, match='arrêté'):
        await loader.fetch(URL)
    assert loader.session is None
