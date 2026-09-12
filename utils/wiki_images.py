"""Je prépare les images officielles des objets sans perdre leurs détails pâles."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
from io import BytesIO
import logging
import time

import aiohttp
from PIL import Image, ImageOps

from utils.xixou_image_paths import is_allowed_image_url


log = logging.getLogger(__name__)
MAX_DOWNLOAD_BYTES = 1024 * 1024
MAX_DIMENSION = 2048
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_CACHE_ENTRIES = 128
MAX_CANDIDATES = 8
CACHE_TTL = 3600
STALE_TTL = 86400
NEGATIVE_TTL = 300


@dataclass(frozen=True)
class ItemImage:
    data: bytes
    source_url: str
    stale: bool = False


@dataclass(frozen=True)
class _CacheEntry:
    image: ItemImage
    refreshed_at: float


def _uniform_bounds(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Je retire seulement les lignes strictement identiques à la couleur des coins."""
    width, height = image.size
    color = image.getpixel((0, 0))
    if any(image.getpixel(point) != color for point in (
        (width - 1, 0), (0, height - 1), (width - 1, height - 1),
    )):
        return 0, 0, width, height
    expected = tuple((value, value) for value in color)

    def uniform(box):
        with image.crop(box) as edge:
            return edge.getextrema() == expected

    left, top, right, bottom = 0, 0, width, height
    while top < bottom and uniform((left, top, right, top + 1)):
        top += 1
    if top == bottom:
        return None
    while bottom > top and uniform((left, bottom - 1, right, bottom)):
        bottom -= 1
    while left < right and uniform((left, top, left + 1, bottom)):
        left += 1
    while right > left and uniform((right - 1, top, right, bottom)):
        right -= 1
    return left, top, right, bottom


def _prepare_png(data: bytes) -> bytes:
    """Je centre le dessin entier sur un carré transparent, avec huit pixels de marge."""
    with BytesIO(data) as stream, Image.open(stream) as source:
        if (source.format != "PNG" or getattr(source, "is_animated", False) or
                not all(0 < size <= MAX_DIMENSION for size in source.size)):
            raise ValueError("Image incompatible")
        if not data.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82"):
            raise ValueError("Image PNG incomplète")
        source.verify()
    with BytesIO(data) as stream, Image.open(stream) as source:
        source.load()
        image = source.convert("RGBA")
    try:
        with image.getchannel("A") as alpha:
            bounds = (alpha.getbbox() if alpha.getextrema() != (255, 255)
                      else _uniform_bounds(image))
        if bounds is None:
            raise ValueError("Image vide")
        with image.crop(bounds) as cropped:
            with ImageOps.contain(cropped, (240, 240), Image.Resampling.LANCZOS) as resized:
                with Image.new("RGBA", (256, 256), (0, 0, 0, 0)) as canvas:
                    canvas.paste(resized, ((256 - resized.width) // 2,
                                          (256 - resized.height) // 2))
                    output = BytesIO()
                    canvas.save(output, format="PNG", compress_level=6)
                    result = output.getvalue()
                    if len(result) > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Image préparée trop volumineuse")
                    return result
    finally:
        image.close()


class WikiImageClient:
    """Je mutualise les téléchargements autorisés et je garde un secours limité à un jour."""

    def __init__(self, session=None, clock=time.monotonic):
        self._session = session
        self._owns_session = session is None
        self._clock = clock
        self._downloads = asyncio.Semaphore(2)
        self._processing = asyncio.Semaphore(1)
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_bytes = 0
        self._negative: OrderedDict[str, float] = OrderedDict()
        self._inflight: dict[str, asyncio.Task] = {}
        self._closed = False
        self._closing_task: asyncio.Task | None = None

    def _fallback(self, url: str) -> ItemImage | None:
        cached = self._cache.get(url)
        if cached is not None and self._clock() - cached.refreshed_at <= STALE_TTL:
            self._cache.move_to_end(url)
            return replace(cached.image, stale=True)
        return None

    def _remember_failure(self, url: str):
        self._negative.pop(url, None)
        self._negative[url] = self._clock() + NEGATIVE_TTL
        while len(self._negative) > MAX_CACHE_ENTRIES:
            self._negative.popitem(last=False)

    def _remember_image(self, url: str, image: ItemImage):
        previous = self._cache.pop(url, None)
        if previous is not None:
            self._cache_bytes -= len(previous.image.data)
        self._cache[url] = _CacheEntry(image, self._clock())
        self._cache_bytes += len(image.data)
        self._negative.pop(url, None)
        while len(self._cache) > MAX_CACHE_ENTRIES or self._cache_bytes > MAX_CACHE_BYTES:
            _, removed = self._cache.popitem(last=False)
            self._cache_bytes -= len(removed.image.data)

    async def resolve(self, candidates: tuple[str, ...]) -> ItemImage | None:
        if self._closed or not isinstance(candidates, (tuple, list)):
            return None
        seen = set()
        for url in candidates[:MAX_CANDIDATES]:
            if self._closed:
                return None
            if not isinstance(url, str) or not is_allowed_image_url(url):
                log.debug("Image objet ignorée : URL non autorisée")
                continue
            if url in seen:
                continue
            seen.add(url)
            result = await self._resolve_url(url)
            if self._closed:
                return None
            if result is not None:
                return result
        return None

    async def _resolve_url(self, url: str) -> ItemImage | None:
        cached = self._cache.get(url)
        if cached is not None and self._clock() - cached.refreshed_at < CACHE_TTL:
            self._cache.move_to_end(url)
            return cached.image
        if self._negative.get(url, 0) > self._clock():
            self._negative.move_to_end(url)
            return self._fallback(url)
        task = self._inflight.get(url)
        if task is None:
            task = asyncio.create_task(self._fetch(url))
            self._inflight[url] = task

            def finished(completed):
                if self._inflight.get(url) is completed:
                    self._inflight.pop(url, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _fetch(self, url: str) -> ItemImage | None:
        try:
            async with self._downloads:
                if self._closed:
                    return None
                if self._session is None:
                    self._session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
                if (self._session.auth is not None or len(self._session.cookie_jar) or
                        any(self._session.headers.get(name)
                            for name in ("Authorization", "X-Api-Key", "Cookie"))):
                    raise ValueError("Session authentifiée incompatible")
                async with self._session.get(
                    url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    if response.status != 200:
                        raise ValueError("Réponse image indisponible")
                    content_type = response.headers.get("Content-Type", "").split(";")[0]
                    if content_type.strip().lower() != "image/png":
                        raise ValueError("Type image incompatible")
                    if response.content_length is not None and response.content_length > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Image trop volumineuse")
                    chunks, length = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        length += len(chunk)
                        if length > MAX_DOWNLOAD_BYTES:
                            raise ValueError("Image trop volumineuse")
                        chunks.append(chunk)
                body = b"".join(chunks)
                async with self._processing:
                    if self._closed:
                        return None
                    prepared = await asyncio.to_thread(_prepare_png, body)
            if self._closed:
                return None
            image = ItemImage(prepared, url)
            self._remember_image(url, image)
            log.debug("Image objet préparée : %s octets", len(prepared))
            return image
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, SyntaxError,
                Image.DecompressionBombError) as error:
            self._remember_failure(url)
            log.debug("Image objet indisponible : %s", type(error).__name__)
            return self._fallback(url)

    async def _finish_close(self):
        if self._inflight:
            await asyncio.gather(*tuple(self._inflight.values()), return_exceptions=True)
        if self._session is not None and self._owns_session:
            await self._session.close()
        self._cache.clear()
        self._cache_bytes = 0
        self._negative.clear()

    async def close(self):
        self._closed = True
        if self._closing_task is None:
            self._closing_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._closing_task)
