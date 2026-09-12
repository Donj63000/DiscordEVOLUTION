import asyncio
import calendar
import io
import threading
from datetime import date

import pytest
from PIL import Image, ImageDraw

import calendrier
from test_calendar_data import record
from utils.calendar_data import snapshot_events


@pytest.mark.parametrize("year,month,rows", [(2021, 2, 4), (2026, 9, 5), (2026, 8, 6)])
def test_monthly_png_is_opaque_and_handles_all_grid_heights(year, month, rows):
    data = calendrier.render_month((), year, month, date(year, month, 1))
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (1120, calendrier.GRID_TOP + rows * (calendrier.CELL_HEIGHT + calendrier.GAP) + 66)
    assert len(data) < 1024 * 1024


def test_busy_day_unicode_and_legacy_entry_point_do_not_crash():
    records = {
        str(i): record(titre="😀 Très longue activité sans espace " + "x" * 1000)
        for i in range(100)
    }
    buffer = calendrier.gen_cal(records, object(), 2026, 9, date(2026, 9, 11))
    with Image.open(buffer) as image:
        image.verify()


def test_measured_ellipsis_stays_inside_its_cell():
    image = Image.new("RGB", (300, 100))
    draw = ImageDraw.Draw(image)
    font = calendrier._font(18)
    fitted = calendrier.fit_text(draw, "Événement " * 40, font, 120)
    assert fitted.endswith("…")
    assert draw.textlength(fitted, font=font) <= 120


@pytest.mark.asyncio
async def test_renderer_runs_off_loop_and_reuses_bytes(monkeypatch):
    calls = []
    main_thread = threading.get_ident()

    def render(*args):
        calls.append(threading.get_ident())
        return b"png"

    monkeypatch.setattr(calendrier, "render_month", render)
    renderer = calendrier.MonthlyRenderer()
    first = await renderer.render((), 2026, 9, date(2026, 9, 11))
    second = await renderer.render((), 2026, 9, date(2026, 9, 11))
    assert first == second == b"png"
    assert len(calls) == 1
    assert calls[0] != main_thread


@pytest.mark.asyncio
async def test_cache_invalidation_and_bounded_memory(monkeypatch):
    monkeypatch.setattr(calendrier, "render_month", lambda *args: b"p" * 10)
    renderer = calendrier.MonthlyRenderer(max_entries=2, max_bytes=25)
    events = snapshot_events({"1": record()}).events
    await renderer.render(events, 2026, 9, date(2026, 9, 11))
    await renderer.render(events, 2026, 9, date(2026, 9, 12))
    changed = snapshot_events({"1": record(titre="Nouveau titre")}).events
    await renderer.render(changed, 2026, 9, date(2026, 9, 12))
    assert len(renderer._cache) == 2
    assert renderer._size == 20
    assert all(len(key[-1]) == 32 for key in renderer._cache)
    tiny = calendrier.MonthlyRenderer(max_bytes=5)
    await tiny.render(events, 2026, 9, date(2026, 9, 11))
    assert not tiny._cache


@pytest.mark.asyncio
async def test_concurrency_is_limited_to_two_workers(monkeypatch):
    mutex = threading.Lock()
    active = maximum = 0

    def render(*args):
        import time
        nonlocal active, maximum
        with mutex:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with mutex:
            active -= 1
        return b"png"

    monkeypatch.setattr(calendrier, "render_month", render)
    renderer = calendrier.MonthlyRenderer()
    await asyncio.gather(*[
        renderer.render((), 2026, month, date(2026, 9, 11)) for month in range(1, 10)
    ])
    assert maximum == 2
