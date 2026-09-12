"""Je dessine les zones Xixou sur leur fond public avec un cache mémoire borné."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
import logging
import math
import time
from urllib.parse import quote

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from utils.dofus_wiki import retry_after_delay


log = logging.getLogger(__name__)
MAP_URL = "https://xixou.io/map-interactive/"
TILES_URL = "https://xixou.io/wp-content/plugins/xixou-map/tiles"
BACKGROUND_SIZE = 5120
MAX_TILE_BYTES = 512 * 1024
MAX_TILE_CACHE_BYTES = 4 * 1024 * 1024
MAX_TILE_CACHE_ENTRIES = 128
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_CACHE_ENTRIES = 32
MAX_COORDINATES = 10000
CACHE_TTL = 3600
STALE_TTL = 86400
HEADER_HEIGHT = 78
FOOTER_HEIGHT = 55


@dataclass(frozen=True)
class MapSpec:
    title: str
    cells: tuple[tuple[float, float], ...] = ()
    polygon: tuple[tuple[float, float], ...] = ()
    points: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class _TileSet:
    box: tuple[int, int, int, int]
    level: int
    tiles: tuple[tuple[int, int, bytes], ...]


def map_pixel(x: float, y: float) -> tuple[float, float]:
    """Je reprends la calibration de la carte Xixou et son axe vertical inversé."""
    return 2 * ((x - 4) * 19.97 + 1613), 5120 - 2 * ((y + 19) * -11.49 + 1174)


def _coordinate(point: object) -> tuple[float, float] | None:
    if not isinstance(point, (tuple, list)) or len(point) != 2:
        return None
    if any(type(value) not in (int, float) for value in point):
        return None
    try:
        x, y = float(point[0]), float(point[1])
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        px, py = map_pixel(x, y)
        if not (0 <= px < BACKGROUND_SIZE and 0 <= py < BACKGROUND_SIZE):
            return None
    except (ValueError, OverflowError):
        return None
    return x, y


def point_url(x: float, y: float) -> str:
    point = _coordinate((x, y))
    if point is None:
        return MAP_URL
    return f"{MAP_URL}#x={point[0]:g}&y={point[1]:g}"


def zone_url(name: str, cells=(), polygon=()) -> str:
    if isinstance(name, str) and name.strip() and "," not in name and len(name) <= 200:
        return f"{MAP_URL}#zone={quote(name, safe='')}"
    for geometry in (cells, polygon):
        if not isinstance(geometry, (tuple, list)):
            continue
        for candidate in geometry[:MAX_COORDINATES]:
            point = _coordinate(candidate)
            if point is not None:
                return point_url(*point)
    return MAP_URL


def _normalise(spec: MapSpec) -> MapSpec | None:
    if not isinstance(spec, MapSpec) or not isinstance(spec.title, str):
        return None
    geometries = []
    count = 0
    for geometry in (spec.cells, () if spec.cells else spec.polygon, spec.points):
        if not isinstance(geometry, (tuple, list)):
            return None
        count += len(geometry)
        if count > MAX_COORDINATES:
            return None
        normalised = []
        for candidate in geometry:
            point = _coordinate(candidate)
            if point is None:
                return None
            normalised.append(point)
        geometries.append(tuple(normalised))
    cells, polygon, points = geometries
    if polygon and len(polygon) < 3:
        return None
    if not (cells or polygon or points):
        return None
    if polygon and not cells and not points:
        area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                   in zip(polygon, polygon[1:] + polygon[:1]))
        if abs(area) < 0.000001:
            return None
    title = " ".join(spec.title.split())[:200] or "Carte Dofus Rétro"
    return MapSpec(title, cells, polygon, points)


def _font(size: int):
    for name in ("DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _ellipsise(draw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _grid_step(spacing: float) -> int:
    for step in (1, 2, 5, 10, 20, 50, 100):
        if spacing * step >= 55:
            return step
    return 100


def _crop_box(spec: MapSpec) -> tuple[int, int, int, int]:
    geometry = (spec.cells or spec.polygon) + spec.points
    pixels = [map_pixel(*point) for point in geometry]
    left, right = min(p[0] for p in pixels), max(p[0] for p in pixels)
    top, bottom = min(p[1] for p in pixels), max(p[1] for p in pixels)
    width, height = max(680, right - left + 200), max(440, bottom - top + 160)
    centre_x, centre_y = (left + right) / 2, (top + bottom) / 2
    return (max(0, math.floor(centre_x - width / 2)),
            max(0, math.floor(centre_y - height / 2)),
            min(BACKGROUND_SIZE, math.ceil(centre_x + width / 2)),
            min(BACKGROUND_SIZE, math.ceil(centre_y + height / 2)))


def _tile_layout(spec: MapSpec):
    """Je choisis les seules tuiles utiles à la résolution finale de l'aperçu."""
    box = _crop_box(spec)
    longest = max(box[2] - box[0], box[3] - box[1])
    level = 5 if longest <= 1200 else 4 if longest <= 2400 else 3
    scale = 2 ** (level - 5)
    columns = range(math.floor(box[0] * scale / 256), math.ceil(box[2] * scale / 256))
    rows = range(math.floor(box[1] * scale / 256), math.ceil(box[3] * scale / 256))
    return box, level, tuple((level, x, y) for x in columns for y in rows)


def _validate_tile(data: bytes):
    with Image.open(BytesIO(data)) as source:
        if source.format != "PNG" or source.size != (256, 256):
            raise ValueError("Tuile de carte incompatible")
        source.load()


def _draw_map(background: _TileSet, spec: MapSpec) -> bytes:
    """Je compose un recadrage de petites tuiles sans décoder le fond de 5 120 pixels."""
    geometry = (spec.cells or spec.polygon) + spec.points
    box = background.box
    tile_scale = 2 ** (background.level - 5)
    cropped = Image.new("RGB", (math.ceil((box[2] - box[0]) * tile_scale),
                                math.ceil((box[3] - box[1]) * tile_scale)), (65, 70, 75))
    try:
        for x, y, data in background.tiles:
            with Image.open(BytesIO(data)) as source:
                if source.format != "PNG" or source.size != (256, 256):
                    raise ValueError("Tuile de carte incompatible")
                with source.convert("RGBA") as tile:
                    cropped.paste(tile, (round(x * 256 - box[0] * tile_scale),
                                         round(y * 256 - box[1] * tile_scale)), tile)
        scale = min(1, 1200 / cropped.width,
                    (1200 - HEADER_HEIGHT - FOOTER_HEIGHT) / cropped.height)
        if scale < 1:
            resized = cropped.resize((max(1, round(cropped.width * scale)),
                                      max(1, round(cropped.height * scale))),
                                     Image.Resampling.LANCZOS)
            cropped.close()
            cropped = resized
        scale_x, scale_y = cropped.width / (box[2] - box[0]), cropped.height / (box[3] - box[1])

        def position(point):
            px, py = map_pixel(*point)
            return (px - box[0]) * scale_x, (py - box[1]) * scale_y

        draw = ImageDraw.Draw(cropped, "RGBA")
        for x, y in spec.cells:
            draw.rectangle((position((x - 0.5, y - 0.5)),
                            position((x + 0.5, y + 0.5))),
                           fill=(255, 190, 40, 105), outline=(255, 226, 135, 230), width=1)
        if not spec.cells and spec.polygon:
            draw.polygon([position(point) for point in spec.polygon],
                         fill=(255, 190, 40, 100), outline=(255, 226, 135, 245), width=3)
        label_font = _font(13)
        min_x = (box[0] / 2 - 1613) / 19.97 + 4
        max_x = (box[2] / 2 - 1613) / 19.97 + 4
        min_y = ((5120 - box[1]) / 2 - 1174) / -11.49 - 19
        max_y = ((5120 - box[3]) / 2 - 1174) / -11.49 - 19
        for axis_min, axis_max, step, vertical in (
            (min_x, max_x, _grid_step(39.94 * scale_x), True),
            (min_y, max_y, _grid_step(22.98 * scale_y), False),
        ):
            for coordinate in range(math.ceil(axis_min / step) * step,
                                    math.floor(axis_max) + 1, step):
                if vertical:
                    px, _ = position((coordinate, min_y))
                    draw.line((px, 0, px, cropped.height), fill=(245, 245, 245, 65))
                    label_position = (px + 3, 5)
                else:
                    _, py = position((min_x, coordinate))
                    draw.line((0, py, cropped.width, py), fill=(245, 245, 245, 65))
                    label_position = (4, py + 2)
                draw.text(label_position, str(coordinate), font=label_font,
                          fill=(255, 255, 255), stroke_width=2, stroke_fill=(20, 30, 35))
        for point in spec.points:
            px, py = position(point)
            draw.ellipse((px - 6, py - 6, px + 6, py + 6),
                         fill=(35, 215, 240, 255), outline=(255, 255, 255, 255), width=2)
        with Image.new("RGB", (cropped.width, cropped.height + HEADER_HEIGHT + FOOTER_HEIGHT),
                       (19, 29, 38)) as canvas:
            canvas.paste(cropped, (0, HEADER_HEIGHT))
            heading = ImageDraw.Draw(canvas)
            title_font, text_font, footer_font = _font(24), _font(16), _font(13)
            heading.text((16, 10), _ellipsise(heading, spec.title, title_font, canvas.width - 32),
                         font=title_font, fill=(255, 255, 255))
            coordinates = (f"Coordonnées : x {min(p[0] for p in geometry):g} à "
                           f"{max(p[0] for p in geometry):g} · y "
                           f"{min(p[1] for p in geometry):g} à {max(p[1] for p in geometry):g}")
            heading.text((16, 45), coordinates, font=text_font, fill=(210, 220, 225))
            heading.text((14, canvas.height - 47), "Source : Xixou.io · Fond Dofus Rétro",
                         font=text_font, fill=(220, 228, 235))
            note = ("Positions de récolte renseignées par Xixou." if spec.points else
                    "Zone indicative : présence du monstre non garantie sur chaque case.")
            heading.text((14, canvas.height - 23),
                         _ellipsise(heading, note, footer_font, canvas.width - 28),
                         font=footer_font, fill=(175, 190, 200))
            output = BytesIO()
            canvas.save(output, format="PNG", compress_level=6)
            result = output.getvalue()
            if len(result) > MAX_IMAGE_BYTES:
                raise ValueError("Image de carte trop volumineuse")
            return result
    finally:
        cropped.close()


class XixouMapRenderer:
    """Je sérialise les rendus, même après l'annulation de leur appelant."""

    def __init__(self, session=None):
        self._session = session
        self._owns_session = session is None
        self._lock = asyncio.Semaphore(1)
        self._downloads = asyncio.Semaphore(2)
        self._tasks: set[asyncio.Task] = set()
        self._closing_task: asyncio.Task | None = None
        self._closed = False
        self._tiles: OrderedDict[tuple[int, int, int], tuple[bytes, float]] = OrderedDict()
        self._tile_bytes = 0
        self._retry_at = 0.0
        self._cache: OrderedDict[MapSpec, tuple[bytes, float]] = OrderedDict()
        self._cache_bytes = 0

    async def render(self, spec: MapSpec) -> bytes | None:
        spec = _normalise(spec)
        if self._closed or spec is None:
            log.debug("Carte Xixou ignorée : service fermé ou géométrie invalide")
            return None
        task = asyncio.create_task(self._render(spec))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await asyncio.shield(task)

    def _fallback_tile(self, key, now):
        cached = self._tiles.get(key)
        if cached is not None and now - cached[1] <= STALE_TTL:
            self._tiles.move_to_end(key)
            return cached
        return None

    async def _load_tile(self, key) -> tuple[bytes, float] | None:
        async with self._downloads:
            now = time.monotonic()
            cached = self._fallback_tile(key, now)
            if cached is not None and now - cached[1] < CACHE_TTL:
                return cached
            if now < self._retry_at:
                return cached
            try:
                if self._session is None:
                    self._session = aiohttp.ClientSession()
                level, x, y = key
                async with self._session.get(
                    f"{TILES_URL}/{level}/{x}/{y}.png", allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    if response.status != 200:
                        delay = (retry_after_delay(response.headers.get("Retry-After", ""))
                                 if response.status in (429, 503) else 60)
                        self._retry_at = max(self._retry_at, now + delay)
                        log.debug("Tuile Xixou indisponible : HTTP %s", response.status)
                        return cached
                    content_type = response.headers.get("Content-Type", "").split(";")[0]
                    if content_type.strip().lower() != "image/png":
                        raise ValueError("Type de tuile incompatible")
                    if response.content_length is not None and response.content_length > MAX_TILE_BYTES:
                        raise ValueError("Tuile trop volumineuse")
                    chunks, length = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        length += len(chunk)
                        if length > MAX_TILE_BYTES:
                            raise ValueError("Tuile trop volumineuse")
                        chunks.append(chunk)
                result = b"".join(chunks)
                await asyncio.to_thread(_validate_tile, result)
                entry = result, time.monotonic()
                previous = self._tiles.pop(key, None)
                if previous is not None:
                    self._tile_bytes -= len(previous[0])
                self._tiles[key] = entry
                self._tile_bytes += len(result)
                while (len(self._tiles) > MAX_TILE_CACHE_ENTRIES or
                       self._tile_bytes > MAX_TILE_CACHE_BYTES):
                    _, removed = self._tiles.popitem(last=False)
                    self._tile_bytes -= len(removed[0])
                return entry
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError,
                    Image.DecompressionBombError) as error:
                self._retry_at = max(self._retry_at, time.monotonic() + 60)
                log.debug("Téléchargement de tuile Xixou impossible : %s", type(error).__name__)
                return cached

    async def _load_background(self, spec):
        box, level, keys = _tile_layout(spec)
        try:
            async with asyncio.timeout(20):
                entries = await asyncio.gather(*(self._load_tile(key) for key in keys))
        except TimeoutError:
            self._retry_at = max(self._retry_at, time.monotonic() + 60)
            log.debug("Délai global dépassé pour les tuiles Xixou")
            return None
        if any(entry is None for entry in entries):
            return None
        expires = min(entry[1] for entry in entries) + CACHE_TTL
        if expires <= time.monotonic():
            expires = min(self._retry_at, min(entry[1] for entry in entries) + STALE_TTL)
        tiles = tuple((key[1], key[2], entry[0]) for key, entry in zip(keys, entries))
        return _TileSet(box, level, tiles), expires

    async def _render(self, spec: MapSpec) -> bytes | None:
        async with self._lock:
            if self._closed:
                return None
            now = time.monotonic()
            cached = self._cache.get(spec)
            if cached is not None and now < cached[1]:
                self._cache.move_to_end(spec)
                return cached[0]
            loaded = await self._load_background(spec)
            if loaded is None:
                return None
            background, expires = loaded
            try:
                result = await asyncio.to_thread(_draw_map, background, spec)
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                log.debug("Rendu de carte Xixou indisponible : %s", type(error).__name__)
                self._retry_at = time.monotonic() + 60
                return None
            previous = self._cache.pop(spec, None)
            if previous is not None:
                self._cache_bytes -= len(previous[0])
            self._cache[spec] = result, expires
            self._cache_bytes += len(result)
            while len(self._cache) > MAX_CACHE_ENTRIES or self._cache_bytes > MAX_CACHE_BYTES:
                _, removed = self._cache.popitem(last=False)
                self._cache_bytes -= len(removed[0])
            log.debug("Carte Xixou dessinée : %s octets, %s entrées en cache",
                      len(result), len(self._cache))
            return result

    async def _finish_close(self):
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if self._session is not None and self._owns_session:
            await self._session.close()
        self._cache.clear()
        self._cache_bytes = 0
        self._tiles.clear()
        self._tile_bytes = 0

    async def close(self):
        self._closed = True
        if self._closing_task is None:
            self._closing_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._closing_task)
