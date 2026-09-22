"""Mesures simulées Linux ; aucun redémarrage ni nettoyage de données."""
import logging
from unittest.mock import Mock

import pytest

from utils import runtime_memory as memory


def fake_files(monkeypatch, files):
    monkeypatch.setattr(memory, "_read_text", files.get)


def test_current_rss_is_not_confused_with_peak(monkeypatch):
    fake_files(monkeypatch, {
        "/proc/self/status": "VmRSS:\t102400 kB\nVmHWM:\t204800 kB\n",
        "/sys/fs/cgroup/memory.current": str(300 * memory.MIB),
        "/sys/fs/cgroup/memory.max": str(512 * memory.MIB),
    })
    assert memory.memory_snapshot() == dict(
        rss_mib=100.0, peak_rss_mib=200.0, cgroup_mib=300.0, limit_mib=512.0)


def test_cgroup_v1_fallback(monkeypatch):
    fake_files(monkeypatch, {
        "/sys/fs/cgroup/memory/memory.usage_in_bytes": str(120 * memory.MIB),
        "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(512 * memory.MIB),
    })
    data = memory.memory_snapshot()
    assert data["cgroup_mib"] == 120
    assert data["limit_mib"] == 512
    assert data["rss_mib"] is None


@pytest.mark.parametrize("limit", ["max", "invalid", str(1 << 63), "0", "-1"])
def test_unlimited_or_invalid_limit_is_not_reported_as_real(monkeypatch, limit):
    fake_files(monkeypatch, {
        "/sys/fs/cgroup/memory.current": "1048576",
        "/sys/fs/cgroup/memory.max": limit,
    })
    assert memory.memory_snapshot()["limit_mib"] is None


def test_unavailable_linux_files_do_not_break_bot(monkeypatch):
    fake_files(monkeypatch, {})
    assert all(value is None for value in memory.memory_snapshot().values())
    memory.log_memory("test")


def test_malformed_rss_does_not_break_bot(monkeypatch):
    fake_files(monkeypatch, {"/proc/self/status": "VmRSS: nope kB\nVmHWM: 1024 kB\n"})
    assert memory.memory_snapshot()["rss_mib"] is None
    assert memory.memory_snapshot()["peak_rss_mib"] == 1


@pytest.mark.parametrize("raw, expected", [("0", 0), ("-1", 0), ("1", 10), ("60", 60),
                                         ("invalid", 60), ("1000000", 86400)])
def test_interval_validation(monkeypatch, raw, expected):
    monkeypatch.setenv("MEMORY_LOG_INTERVAL", raw)
    assert memory._interval() == expected


def test_warning_uses_container_total_not_only_bot_rss(monkeypatch, caplog):
    monkeypatch.setattr(memory, "memory_snapshot", lambda: dict(
        rss_mib=100., peak_rss_mib=110., cgroup_mib=450., limit_mib=512.))
    with caplog.at_level(logging.INFO):
        memory.log_memory("test")
    assert caplog.records[-1].levelno == logging.WARNING
    assert "cgroup_mib=450.0" in caplog.text


def test_monitor_disabled_and_idempotent(monkeypatch):
    monkeypatch.setattr(memory, "_monitor", None)
    thread = Mock()
    thread.is_alive.return_value = True
    factory = Mock(return_value=thread)
    monkeypatch.setattr(memory, "Thread", factory)
    monkeypatch.setenv("MEMORY_LOG_INTERVAL", "0")
    assert memory.start_memory_monitor() is None
    factory.assert_not_called()
    monkeypatch.setenv("MEMORY_LOG_INTERVAL", "60")
    assert memory.start_memory_monitor() is thread
    assert memory.start_memory_monitor() is thread
    factory.assert_called_once()
    thread.start.assert_called_once()
