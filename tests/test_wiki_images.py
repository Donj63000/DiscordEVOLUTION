import asyncio
from io import BytesIO
import threading
from unittest.mock import AsyncMock

import aiohttp
from aioresponses import CallbackResult, aioresponses
from PIL import Image, ImageDraw
import pytest

from utils import wiki_images as images


XIXOU_IMAGE = "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png"
RESOURCE_IMAGE = "https://xixou.io/wp-content/uploads/xixou-og/xixou-ressources/images_bois/17.png"
MOON_IMAGE = "https://wiki.moon-bot.io/icons/item_9_47.png"


def png_bytes(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def png():
    with Image.new("RGBA", (60, 100)) as image:
        ImageDraw.Draw(image).rectangle((20, 10, 39, 89), fill=(180, 100, 20, 255))
        return png_bytes(image)


def damaged_png(data, damage):
    """Je conserve une vraie structure PNG pour tester ses contrôles d'intégrité."""
    offset = 8
    while offset < len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        if data[offset + 4:offset + 8] == b"IDAT":
            break
        offset += 12 + size
    else:
        raise AssertionError("Le PNG de test doit contenir un bloc IDAT")
    if damage == "idat-crc":
        crc = offset + 8 + size
        return data[:crc] + bytes([data[crc] ^ 1]) + data[crc + 1:]
    if damage == "idat-truncated":
        return data[:offset + 8 + size // 2]
    if damage == "idat-short-with-iend":
        return data[:offset + 8 + size // 2] + data[-12:]
    if damage == "iend-missing":
        return data[:-12]
    if damage == "iend-truncated":
        return data[:-1]
    if damage == "iend-crc":
        return data[:-1] + bytes([data[-1] ^ 1])
    raise AssertionError("Altération PNG inconnue")


@pytest.mark.parametrize("damage", ["idat-crc", "idat-truncated", "idat-short-with-iend",
                                     "iend-missing", "iend-truncated", "iend-crc"])
def test_real_png_corruption_and_truncation_are_rejected_before_processing(png, damage):
    with pytest.raises((OSError, ValueError, SyntaxError)):
        images._prepare_png(damaged_png(png, damage))


def test_idat_crc_is_checked_even_when_pillow_load_accepts_the_pixels(png):
    corrupted = damaged_png(png, "idat-crc")
    with Image.open(BytesIO(corrupted)) as image:
        image.load()
        assert image.size == (60, 100)
    with pytest.raises(SyntaxError):
        images._prepare_png(corrupted)


def test_transparent_image_is_centered_without_cutting_or_stretching(png):
    result = images._prepare_png(png)
    with Image.open(BytesIO(result)) as image:
        assert image.size == (256, 256)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getchannel("A").getbbox() == (98, 8, 158, 248)
        assert image.getpixel((128, 128)) == (180, 100, 20, 255)


def test_uniform_trimming_preserves_one_level_pale_effects():
    with Image.new("RGBA", (240, 120), (250, 249, 236, 255)) as image:
        ImageDraw.Draw(image).rectangle((100, 50, 140, 70), fill=(80, 60, 20, 255))
        ImageDraw.Draw(image).rectangle((10, 20, 230, 100), outline=(249, 249, 236, 255))
        assert images._uniform_bounds(image) == (10, 20, 231, 101)
        result = images._prepare_png(png_bytes(image))
    with Image.open(BytesIO(result)) as image:
        box = image.getchannel("A").getbbox()
        assert box[2] - box[0] == 240
        assert 85 <= box[3] - box[1] <= 90
        assert any(pixel[:3] == (249, 249, 236) and pixel[3] == 255
                   for pixel in image.getdata())


def test_almost_transparent_pixels_are_preserved():
    with Image.new("RGBA", (100, 100)) as image:
        image.putpixel((2, 2), (255, 255, 255, 1))
        ImageDraw.Draw(image).rectangle((40, 40, 60, 60), fill=(100, 50, 30, 255))
        image.putpixel((97, 97), (255, 255, 255, 1))
        result = images._prepare_png(png_bytes(image))
    with Image.open(BytesIO(result)) as image:
        assert image.getchannel("A").getbbox() == (8, 8, 248, 248)


def test_different_corner_colors_keep_the_entire_drawing():
    with Image.new("RGBA", (100, 80), (240, 235, 210, 255)) as image:
        image.putpixel((0, 0), (10, 20, 30, 255))
        assert images._uniform_bounds(image) == (0, 0, 100, 80)


@pytest.mark.parametrize("mode,color", [("RGB", (240, 235, 210)),
                                        ("RGBA", (10, 20, 30, 0))])
def test_blank_images_are_rejected(mode, color):
    with Image.new(mode, (60, 80), color) as image:
        with pytest.raises(ValueError, match="vide"):
            images._prepare_png(png_bytes(image))


def test_png_palette_transparency_is_preserved():
    with Image.new("P", (40, 40), 0) as image:
        image.putpalette([0, 0, 0, 180, 100, 30] + [0] * (768 - 6))
        ImageDraw.Draw(image).rectangle((10, 10, 29, 29), fill=1)
        image.info["transparency"] = 0
        result = images._prepare_png(png_bytes(image))
    with Image.open(BytesIO(result)) as image:
        assert image.getchannel("A").getbbox() == (8, 8, 248, 248)
        assert image.getpixel((128, 128)) == (180, 100, 30, 255)


def test_animated_png_is_rejected():
    output = BytesIO()
    with Image.new("RGB", (10, 10), "red") as first:
        with Image.new("RGB", (10, 10), "blue") as second:
            first.save(output, format="PNG", save_all=True, append_images=[second], duration=100)
    with pytest.raises(ValueError, match="incompatible"):
        images._prepare_png(output.getvalue())


@pytest.mark.parametrize("size,format", [((2049, 1), "PNG"), ((1, 2049), "PNG"),
                                         ((60, 80), "JPEG"), ((60, 80), "WEBP")])
def test_dimensions_and_actual_format_are_validated(size, format):
    output = BytesIO()
    with Image.new("RGB", size, "red") as image:
        image.save(output, format=format)
    with pytest.raises(ValueError, match="incompatible"):
        images._prepare_png(output.getvalue())


@pytest.mark.asyncio
async def test_download_is_lazy_public_and_returns_prepared_image(png):
    client = images.WikiImageClient()
    assert client._session is None
    with aioresponses() as mocked:
        mocked.get(XIXOU_IMAGE, body=png, content_type="image/png")
        image = await client.resolve((XIXOU_IMAGE,))
        assert image.source_url == XIXOU_IMAGE
        assert image.stale is False
        assert image.data.startswith(b"\x89PNG")
        assert await client.resolve((XIXOU_IMAGE,)) is image
        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["allow_redirects"] is False
        assert request.kwargs["timeout"].total == 5
        assert not request.kwargs.get("headers", {}).get("X-Api-Key")
        assert sum(len(values) for values in mocked.requests.values()) == 1
    await client.close()
    assert client._session.closed
    assert client._cache_bytes == 0
    assert not client._cache
    assert await client.resolve((XIXOU_IMAGE,)) is None


@pytest.mark.parametrize("url", ["http://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png",
                                 "https://other.test/item.png", "file:///image.png", "",
                                 XIXOU_IMAGE + "?key=test", XIXOU_IMAGE + "#test",
                                 XIXOU_IMAGE.replace(".png", ".svg"),
                                 XIXOU_IMAGE.replace("47.png", "../../47.png"), None])
@pytest.mark.asyncio
async def test_disallowed_urls_never_download(url):
    session = AsyncMock()
    client = images.WikiImageClient(session=session)
    assert await client.resolve((url,)) is None
    session.get.assert_not_called()
    await client.close()
    session.close.assert_not_called()


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 429, 500])
@pytest.mark.asyncio
async def test_http_errors_are_negatively_cached(status):
    clock = [1000.0]
    client = images.WikiImageClient(clock=lambda: clock[0])
    with aioresponses() as mocked:
        mocked.get(XIXOU_IMAGE, status=status)
        assert await client.resolve((XIXOU_IMAGE,)) is None
        assert await client.resolve((XIXOU_IMAGE,)) is None
        assert sum(len(values) for values in mocked.requests.values()) == 1
        assert client._negative[XIXOU_IMAGE] == 1300
    await client.close()


@pytest.mark.asyncio
async def test_negative_cache_expires_and_recovers(png):
    clock = [1000.0]
    client = images.WikiImageClient(clock=lambda: clock[0])
    with aioresponses() as mocked:
        mocked.get(XIXOU_IMAGE, status=404)
        assert await client.resolve((XIXOU_IMAGE,)) is None
        clock[0] += 301
        mocked.get(XIXOU_IMAGE, body=png, content_type="image/png")
        assert await client.resolve((XIXOU_IMAGE,))
        assert XIXOU_IMAGE not in client._negative
    await client.close()


@pytest.mark.parametrize("body,content_type,headers", [
    (b"invalid PNG", "image/png", {}), (b"html", "text/html", {}),
    (b"", "image/png", {"Content-Length": str(images.MAX_DOWNLOAD_BYTES + 1)}),
    (b"x" * (images.MAX_DOWNLOAD_BYTES + 1), "image/png", {}),
], ids=["invalid-png", "html", "large-header", "large-stream"])
@pytest.mark.asyncio
async def test_invalid_or_oversized_response_is_not_cached(body, content_type, headers):
    client = images.WikiImageClient()
    with aioresponses() as mocked:
        mocked.get(XIXOU_IMAGE, body=body, content_type=content_type, headers=headers)
        assert await client.resolve((XIXOU_IMAGE,)) is None
        assert not client._cache
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["idat-crc", "idat-truncated", "idat-short-with-iend",
                                     "iend-missing", "iend-truncated", "iend-crc"])
async def test_corrupted_png_response_is_negatively_cached_and_uses_next_candidate(png, damage):
    client = images.WikiImageClient(clock=lambda: 1000.0)
    try:
        with aioresponses() as mocked:
            mocked.get(XIXOU_IMAGE, body=damaged_png(png, damage), content_type="image/png")
            mocked.get(MOON_IMAGE, body=png, content_type="image/png")
            result = await client.resolve((XIXOU_IMAGE, MOON_IMAGE))
            assert result.source_url == MOON_IMAGE
            assert XIXOU_IMAGE not in client._cache
            assert client._negative[XIXOU_IMAGE] == 1300
            assert await client.resolve((XIXOU_IMAGE, MOON_IMAGE)) is result
            assert sum(len(values) for values in mocked.requests.values()) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["idat-crc", "iend-truncated"])
async def test_corrupted_png_refresh_preserves_the_last_verified_image(png, damage):
    clock = [1000.0]
    client = images.WikiImageClient(clock=lambda: clock[0])
    try:
        with aioresponses() as mocked:
            mocked.get(XIXOU_IMAGE, body=png, content_type="image/png")
            original = await client.resolve((XIXOU_IMAGE,))
            clock[0] += images.CACHE_TTL + 1
            mocked.get(XIXOU_IMAGE, body=damaged_png(png, damage), content_type="image/png")
            fallback = await client.resolve((XIXOU_IMAGE,))
            assert fallback.stale is True
            assert fallback.data == original.data
            assert client._cache[XIXOU_IMAGE].refreshed_at == 1000
            assert client._negative[XIXOU_IMAGE] == clock[0] + images.NEGATIVE_TTL
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_network_timeout_keeps_fallback_candidates_available(png):
    client = images.WikiImageClient()
    with aioresponses() as mocked:
        mocked.get(XIXOU_IMAGE, exception=asyncio.TimeoutError())
        mocked.get(MOON_IMAGE, body=png, content_type="image/png")
        result = await client.resolve((XIXOU_IMAGE, XIXOU_IMAGE, MOON_IMAGE))
        assert result.source_url == MOON_IMAGE
        assert sum(len(values) for values in mocked.requests.values()) == 2
    await client.close()


@pytest.mark.asyncio
async def test_invalid_refresh_retains_previous_good_image_until_stale_limit(png):
    clock = [1000.0]
    client = images.WikiImageClient(clock=lambda: clock[0])
    with aioresponses() as mocked:
        mocked.get(XIXOU_IMAGE, body=png, content_type="image/png")
        original = await client.resolve((XIXOU_IMAGE,))
        clock[0] += 3601
        mocked.get(XIXOU_IMAGE, body=b"broken", content_type="image/png")
        stale = await client.resolve((XIXOU_IMAGE,))
        assert stale.data == original.data
        assert stale.stale is True
        assert client._cache[XIXOU_IMAGE].refreshed_at == 1000
        assert (await client.resolve((XIXOU_IMAGE,))).stale is True
        clock[0] += 86400
        mocked.get(XIXOU_IMAGE, status=503)
        assert await client.resolve((XIXOU_IMAGE,)) is None
    await client.close()


@pytest.mark.asyncio
async def test_successful_refresh_clears_stale_status(png):
    clock = [1000.0]
    client = images.WikiImageClient(clock=lambda: clock[0])
    with aioresponses() as mocked:
        mocked.get(XIXOU_IMAGE, body=png, content_type="image/png", repeat=True)
        await client.resolve((XIXOU_IMAGE,))
        clock[0] += 3601
        result = await client.resolve((XIXOU_IMAGE,))
        assert result.stale is False
        assert client._cache[XIXOU_IMAGE].refreshed_at == clock[0]
    await client.close()


@pytest.mark.asyncio
async def test_cache_has_byte_and_count_limits(png, monkeypatch):
    client = images.WikiImageClient()
    monkeypatch.setattr(images, "MAX_CACHE_ENTRIES", 2)
    monkeypatch.setattr(images, "MAX_CACHE_BYTES", 5)
    monkeypatch.setattr(images, "_prepare_png", lambda _: b"abc")
    with aioresponses() as mocked:
        for index in range(6):
            url = f"https://wiki.moon-bot.io/icons/item_{index}.png"
            mocked.get(url, body=png, content_type="image/png")
            assert await client.resolve((url,))
            if index == 2:
                assert len(client._cache) == 1
                assert client._cache_bytes == 3
                monkeypatch.setattr(images, "MAX_CACHE_BYTES", 100)
        assert len(client._cache) == 2
        assert client._cache_bytes == 6
        assert list(client._cache) == ["https://wiki.moon-bot.io/icons/item_4.png",
                                      "https://wiki.moon-bot.io/icons/item_5.png"]
    await client.close()


@pytest.mark.asyncio
async def test_negative_cache_count_is_bounded(monkeypatch):
    client = images.WikiImageClient()
    monkeypatch.setattr(images, "MAX_CACHE_ENTRIES", 2)
    with aioresponses() as mocked:
        for index in range(4):
            url = f"https://wiki.moon-bot.io/icons/item_{index}.png"
            mocked.get(url, status=404)
            assert await client.resolve((url,)) is None
    assert len(client._negative) == 2
    await client.close()


@pytest.mark.asyncio
async def test_same_url_coalesces_download_and_processing(png, monkeypatch):
    calls = []
    monkeypatch.setattr(images, "_prepare_png", lambda data: calls.append(data) or b"png")
    client = images.WikiImageClient()
    with aioresponses() as mocked:
        mocked.get(XIXOU_IMAGE, body=png, content_type="image/png")
        results = await asyncio.gather(*(client.resolve((XIXOU_IMAGE,)) for _ in range(5)))
        assert all(result.data == b"png" for result in results)
        assert sum(len(values) for values in mocked.requests.values()) == 1
        assert calls == [png]
    await client.close()


@pytest.mark.asyncio
async def test_at_most_two_downloads_run_together(png):
    active, maximum = 0, 0

    async def callback(*args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        await asyncio.sleep(0.01)
        active -= 1
        return CallbackResult(body=png, content_type="image/png")

    client = images.WikiImageClient()
    with aioresponses() as mocked:
        urls = [f"https://wiki.moon-bot.io/icons/item_{index}.png" for index in range(5)]
        for url in urls:
            mocked.get(url, callback=callback)
        assert all(await asyncio.gather(*(client.resolve((url,)) for url in urls)))
    assert maximum == 2
    assert active == 0
    await client.close()


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_release_processing_lock_early(png, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def prepare(data):
        calls.append(data)
        entered.set()
        assert release.wait(5)
        return b"png"

    monkeypatch.setattr(images, "_prepare_png", prepare)
    client = images.WikiImageClient()
    with aioresponses() as mocked:
        mocked.get(XIXOU_IMAGE, body=png, content_type="image/png")
        mocked.get(RESOURCE_IMAGE, body=png, content_type="image/png")
        first = asyncio.create_task(client.resolve((XIXOU_IMAGE,)))
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(client.resolve((RESOURCE_IMAGE,)))
        observer = asyncio.create_task(client.resolve((XIXOU_IMAGE,)))
        await asyncio.sleep(0.01)
        assert len(calls) == 1
        closing = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        assert not closing.done()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        release.set()
        await client.close()
        assert await second is None
        assert await observer is None
    assert len(calls) == 1
    assert client._session.closed
    assert client._cache_bytes == 0


@pytest.mark.asyncio
async def test_injected_session_is_not_closed(png):
    async with aiohttp.ClientSession() as session:
        client = images.WikiImageClient(session=session)
        with aioresponses() as mocked:
            mocked.get(XIXOU_IMAGE, body=png, content_type="image/png")
            assert await client.resolve((XIXOU_IMAGE,))
        await client.close()
        await client.close()
        assert not session.closed


@pytest.mark.parametrize("settings", [{"headers": {"X-Api-Key": "test-secret"}},
                                      {"headers": {"Authorization": "Bearer test-secret"}},
                                      {"headers": {"Cookie": "session=test-secret"}},
                                      {"auth": aiohttp.BasicAuth("user", "test-secret")}])
@pytest.mark.asyncio
async def test_authenticated_injected_session_never_sends_credentials(settings, caplog):
    async with aiohttp.ClientSession(**settings) as session:
        client = images.WikiImageClient(session=session)
        with aioresponses() as mocked:
            assert await client.resolve((XIXOU_IMAGE,)) is None
            assert not mocked.requests
        assert "test-secret" not in caplog.text
        await client.close()
