import asyncio
from io import BytesIO
import re
import threading
from unittest.mock import AsyncMock
from urllib.parse import unquote

import aiohttp
from aioresponses import CallbackResult, aioresponses
from PIL import Image
import pytest

from utils import xixou_maps as maps


@pytest.fixture(scope="module")
def background():
    output = BytesIO()
    with Image.new("RGB", (256, 256), (48, 89, 70)) as image:
        image.save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def zone():
    return maps.MapSpec("La péninsule des gelées", cells=((10, 28), (10, 29), (11, 28)))


TILE_PATTERN = re.compile(re.escape(maps.TILES_URL) + r"/[345]/\d+/\d+\.png$")


def mock_tiles(mocked, body=b"", **kwargs):
    kwargs.setdefault("content_type", "image/png")
    mocked.get(TILE_PATTERN, body=body, repeat=True, **kwargs)


def tile_set(background, spec):
    box, level, keys = maps._tile_layout(spec)
    return maps._TileSet(box, level, tuple((key[1], key[2], background) for key in keys))


def test_calibration_matches_reference_and_inverts_only_source_axis():
    assert maps.map_pixel(4, -19) == (3226, 2772)
    origin = maps.map_pixel(0, 0)
    east = maps.map_pixel(1, 0)
    south = maps.map_pixel(0, 1)
    assert east[0] - origin[0] == pytest.approx(39.94)
    assert south[1] - origin[1] == pytest.approx(22.98)
    assert east[1] == origin[1]
    assert south[0] == origin[0]


@pytest.mark.parametrize("points,level", [(((10, 28),), 5),
                                            (((-20, 0), (20, 0)), 4),
                                            (((-70, -120), (45, 70)), 3)])
def test_tile_resolution_adapts_without_loading_the_full_background(points, level):
    box, actual, keys = maps._tile_layout(maps.MapSpec("Carte", points=points))
    assert actual == level
    assert len(keys) <= 36
    assert all(0 <= key[1] < 5 * 2 ** (level - 3) and
               0 <= key[2] < 5 * 2 ** (level - 3) for key in keys)
    assert (box[2] - box[0]) * 2 ** (level - 5) <= 1280
    assert (box[3] - box[1]) * 2 ** (level - 5) <= 1280


def test_links_encode_exact_names_and_handle_comma_fallback():
    name = "La péninsule des gelées & forêt"
    url = maps.zone_url(name)
    assert unquote(url.partition("#zone=")[2]) == name
    assert " " not in url
    assert "%26" in url
    assert maps.zone_url("Une zone, une autre", cells=((10, 28),)) == maps.point_url(10, 28)
    assert maps.zone_url("Une zone, une autre") == maps.MAP_URL
    assert maps.zone_url(None, polygon=((1, 2), (2, 2), (3, 3))) == maps.point_url(1, 2)
    assert maps.point_url(4, -19) == maps.MAP_URL + "#x=4&y=-19"


@pytest.mark.parametrize("point", [(True, 1), (float("nan"), 1), (1, float("inf")),
                                    (1000, 0), (0, -1000), ("1", 1), (10 ** 400, 0)])
def test_invalid_points_do_not_generate_misleading_links(point):
    assert maps.point_url(*point) == maps.MAP_URL


@pytest.mark.parametrize("spec", [
    maps.MapSpec("Vide"), maps.MapSpec("Hors limites", points=((10000, 0),)),
    maps.MapSpec("Booléen", cells=((True, 1),)), maps.MapSpec("Polygone", polygon=((1, 1),)),
    maps.MapSpec("Polygone plat", polygon=((1, 1), (2, 2), (3, 3))),
    maps.MapSpec("Trop de points", points=((1, 1),) * 10001),
    maps.MapSpec("Dimensions", points=((1, 1, 5),)), maps.MapSpec(None, points=((1, 1),)),
])
@pytest.mark.asyncio
async def test_invalid_geometry_never_downloads(spec):
    session = AsyncMock()
    renderer = maps.XixouMapRenderer(session=session)
    assert await renderer.render(spec) is None
    session.get.assert_not_called()
    await renderer.close()
    session.close.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_download_is_fixed_public_and_renders_png(background, zone, tmp_path):
    renderer = maps.XixouMapRenderer()
    assert renderer._session is None
    with aioresponses() as mocked:
        mock_tiles(mocked, background)
        png = await renderer.render(zone)
        assert await renderer.render(zone) == png
        assert len(mocked.requests) == len(maps._tile_layout(zone)[2])
        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["allow_redirects"] is False
        assert not request.kwargs.get("headers", {}).get("X-Api-Key")
    assert png.startswith(b"\x89PNG")
    with Image.open(BytesIO(png)) as image:
        assert max(image.size) <= 1200
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (19, 29, 38)
        assert image.getpixel((0, maps.HEADER_HEIGHT + 20)) != (19, 29, 38)
    (tmp_path / "xixou-zone-preview.png").write_bytes(png)
    await renderer.close()
    assert renderer._session.closed
    assert not renderer._tiles
    assert renderer._cache_bytes == 0
    assert await renderer.render(zone) is None


def test_crop_highlights_cells_and_ignores_polygon_when_cells_exist(background, zone):
    with_polygon = maps.MapSpec(zone.title, cells=zone.cells,
                               polygon=((-10, -10), (-5, -10), (-10, -5)))
    assert maps._draw_map(tile_set(background, zone), zone) == maps._draw_map(
        tile_set(background, with_polygon), with_polygon)


def test_cells_take_precedence_over_invalid_unused_polygon(zone):
    with_polygon = maps.MapSpec(zone.title, cells=zone.cells, polygon=((10000, 0),))
    assert maps._normalise(with_polygon) == maps._normalise(zone)


def test_wide_geometry_is_bounded_and_points_are_visible(background):
    spec = maps.MapSpec("Frêne", points=((-70, -120), (45, 70), (0, 0)))
    png = maps._draw_map(tile_set(background, spec), spec)
    with Image.open(BytesIO(png)) as image:
        assert max(image.size) <= 1200
        assert any(r < 60 and g > 170 and b > 210 for r, g, b in image.getdata())


def test_polygon_and_long_unicode_title_render(background):
    spec = maps.MapSpec("Forêt d'Astrub étoilée " * 20,
                        polygon=((0, 0), (0, 5), (4, 5), (4, 0)))
    png = maps._draw_map(tile_set(background, spec), maps._normalise(spec))
    assert png.startswith(b"\x89PNG")


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500, 503])
@pytest.mark.asyncio
async def test_http_failures_return_none_and_back_off(status, zone):
    renderer = maps.XixouMapRenderer()
    with aioresponses() as mocked:
        mock_tiles(mocked, status=status, headers={"Retry-After": "3600"})
        before = maps.time.monotonic()
        assert await renderer.render(zone) is None
        assert await renderer.render(zone) is None
        assert sum(len(v) for v in mocked.requests.values()) == 1
        assert renderer._retry_at >= before + (3600 if status in (429, 503) else 60)
    await renderer.close()


@pytest.mark.parametrize("body,content_type,headers", [
    (b"not an image", "image/png", {}),
    (b"not an image", "text/html", {}),
    (b"", "image/png", {"Content-Length": str(maps.MAX_TILE_BYTES + 1)}),
    (b"x" * (maps.MAX_TILE_BYTES + 1), "image/png", {}),
], ids=["invalid-png", "html", "large-content-length", "large-stream"])
@pytest.mark.asyncio
async def test_malformed_and_oversized_assets_are_not_cached(body, content_type, headers, zone):
    renderer = maps.XixouMapRenderer()
    with aioresponses() as mocked:
        mock_tiles(mocked, body, content_type=content_type, headers=headers)
        assert await renderer.render(zone) is None
        assert not renderer._tiles
        assert not renderer._cache
    await renderer.close()


@pytest.mark.parametrize("size,image_format", [((128, 128), "PNG"), ((256, 256), "WEBP")])
def test_wrong_image_dimensions_and_formats_are_rejected(size, image_format, zone):
    output = BytesIO()
    with Image.new("RGB", size) as image:
        image.save(output, image_format)
    with pytest.raises(ValueError):
        maps._draw_map(tile_set(output.getvalue(), zone), zone)


@pytest.mark.asyncio
async def test_network_timeout_returns_none(zone):
    renderer = maps.XixouMapRenderer()
    with aioresponses() as mocked:
        mock_tiles(mocked, exception=asyncio.TimeoutError())
        assert await renderer.render(zone) is None
    await renderer.close()


@pytest.mark.asyncio
async def test_refresh_retains_valid_asset_on_failure_then_expires(background, zone, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr(maps.time, "monotonic", lambda: clock[0])
    renderer = maps.XixouMapRenderer()
    with aioresponses() as mocked:
        mock_tiles(mocked, background)
        expected = await renderer.render(zone)
    clock[0] += 3601
    with aioresponses() as mocked:
        mock_tiles(mocked, b"broken")
        assert await renderer.render(zone) == expected
        assert all(entry == (background, 100000) for entry in renderer._tiles.values())
    clock[0] += 86400
    with aioresponses() as mocked:
        mock_tiles(mocked, status=503)
        assert await renderer.render(zone) is None
    await renderer.close()


@pytest.mark.asyncio
async def test_expired_preview_refreshes_tiles_and_uses_new_image(background, zone, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr(maps.time, "monotonic", lambda: clock[0])
    renderer = maps.XixouMapRenderer()
    with aioresponses() as mocked:
        mock_tiles(mocked, background)
        await renderer.render(zone)
    clock[0] += 3601
    with aioresponses() as mocked:
        mock_tiles(mocked, background)
        assert await renderer.render(zone)
        assert len(renderer._cache) == 1
        assert all(entry[1] == clock[0] for entry in renderer._tiles.values())
    await renderer.close()


@pytest.mark.asyncio
async def test_lru_limits_count_and_bytes(background, monkeypatch):
    renderer = maps.XixouMapRenderer()
    monkeypatch.setattr(maps, "MAX_CACHE_ENTRIES", 2)
    monkeypatch.setattr(maps, "MAX_CACHE_BYTES", 5)
    monkeypatch.setattr(maps, "_draw_map", lambda *_: b"abc")
    with aioresponses() as mocked:
        mock_tiles(mocked, background)
        for index in range(3):
            assert await renderer.render(maps.MapSpec(str(index), points=((1, 1),))) == b"abc"
        assert len(renderer._cache) == 1
        assert next(iter(renderer._cache)).title == "2"
        assert renderer._cache_bytes == 3
        monkeypatch.setattr(maps, "MAX_CACHE_BYTES", 100)
        for index in range(3, 6):
            await renderer.render(maps.MapSpec(str(index), points=((1, 1),)))
        assert [spec.title for spec in renderer._cache] == ["4", "5"]
    await renderer.close()


@pytest.mark.asyncio
async def test_tile_cache_is_bounded_even_during_a_large_render(background, zone, monkeypatch):
    renderer = maps.XixouMapRenderer()
    monkeypatch.setattr(maps, "MAX_TILE_CACHE_ENTRIES", 2)
    monkeypatch.setattr(maps, "MAX_TILE_CACHE_BYTES", 2 * len(background) + 1)
    with aioresponses() as mocked:
        mock_tiles(mocked, background)
        assert await renderer.render(zone)
        assert len(renderer._tiles) == 2
        assert renderer._tile_bytes == 2 * len(background)
    await renderer.close()


@pytest.mark.asyncio
async def test_at_most_two_tiles_download_concurrently(background, zone):
    active, maximum = 0, 0

    async def callback(*args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        await asyncio.sleep(0.01)
        active -= 1
        return CallbackResult(body=background, content_type="image/png")

    renderer = maps.XixouMapRenderer()
    with aioresponses() as mocked:
        mocked.get(TILE_PATTERN, callback=callback, repeat=True)
        assert await renderer.render(zone)
    assert maximum == 2
    assert active == 0
    await renderer.close()


def test_encoded_image_size_is_bounded(background, zone, monkeypatch):
    monkeypatch.setattr(maps, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(ValueError, match="volumineuse"):
        maps._draw_map(tile_set(background, zone), zone)


@pytest.mark.asyncio
async def test_concurrent_requests_share_download_and_same_render(background, zone, monkeypatch):
    calls = []
    monkeypatch.setattr(maps, "_draw_map", lambda *args: calls.append(args) or b"png")
    renderer = maps.XixouMapRenderer()
    with aioresponses() as mocked:
        mock_tiles(mocked, background)
        assert await asyncio.gather(*(renderer.render(zone) for _ in range(5))) == [b"png"] * 5
    assert len(calls) == 1
    await renderer.close()


@pytest.mark.asyncio
async def test_caller_cancellation_keeps_thread_serialized_and_close_waits(background, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def draw(*args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return b"png"

    monkeypatch.setattr(maps, "_draw_map", draw)
    renderer = maps.XixouMapRenderer()
    first = asyncio.create_task(renderer.render(maps.MapSpec("A", points=((1, 1),))))
    with aioresponses() as mocked:
        mock_tiles(mocked, background)
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(renderer.render(maps.MapSpec("B", points=((2, 2),))))
        await asyncio.sleep(0)
        closing = asyncio.create_task(renderer.close())
        await asyncio.sleep(0)
        assert not closing.done()
        assert len(calls) == 1
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        release.set()
        await renderer.close()
        assert await second is None
    assert renderer._session.closed
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_injected_session_is_not_closed(background, zone):
    async with aiohttp.ClientSession() as session:
        renderer = maps.XixouMapRenderer(session=session)
        with aioresponses() as mocked:
            mock_tiles(mocked, background)
            assert await renderer.render(zone)
        await renderer.close()
        await renderer.close()
        assert not session.closed
