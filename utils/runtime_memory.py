"""Mesures mémoire légères ; aucune purge, aucun redémarrage automatique."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock, Thread
import time

log = logging.getLogger("runtime.memory")
MIB = 1024 * 1024
_monitor = None
_monitor_lock = Lock()


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None


def _integer_file(path: str) -> int | None:
    raw = _read_text(path)
    try:
        value = int(raw.strip()) if raw is not None else None
        return value if value is not None and value >= 0 else None
    except ValueError:
        return None


def memory_snapshot() -> dict[str, float | None]:
    """RSS du processus courant et compteurs cgroup Linux si exposés.

    Les compteurs du cgroup peuvent inclure d'autres processus et des caches
    système. Sur une plateforme sans /proc ni cgroup, les valeurs valent None.
    Le graphe Render reste la référence pour la limite effective du service.
    """
    result = dict(rss_mib=None, peak_rss_mib=None,
                  cgroup_mib=None, limit_mib=None)
    for line in (_read_text("/proc/self/status") or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in {"VmRSS:", "VmHWM:"} and parts[2] == "kB":
            try:
                value = int(parts[1]) / 1024
            except ValueError:
                continue
            result["rss_mib" if parts[0] == "VmRSS:" else "peak_rss_mib"] = value

    # Emplacements usuels dans les conteneurs : cgroup v2, puis v1.
    for used_path, limit_path in (
        ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
        ("/sys/fs/cgroup/memory/memory.usage_in_bytes",
         "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        used = _integer_file(used_path)
        if used is None:
            continue
        result["cgroup_mib"] = used / MIB
        limit = _integer_file(limit_path)
        # v2 utilise "max" ; v1 peut utiliser une énorme sentinelle.
        if limit is not None and 0 < limit < (1 << 60):
            result["limit_mib"] = limit / MIB
        break
    return result


def log_memory(phase: str) -> dict[str, float | None]:
    stats = memory_snapshot()
    fields = " ".join(
        f"{name}={value:.1f}" if value is not None else f"{name}=n/a"
        for name, value in stats.items()
    )
    used, limit = stats["cgroup_mib"], stats["limit_mib"]
    level = logging.WARNING if (
        used is not None and limit is not None and used >= limit * 0.85
    ) else logging.INFO
    log.log(level, "MEMORY phase=%s %s", phase, fields)
    return stats


def _interval() -> int:
    try:
        value = int(os.getenv("MEMORY_LOG_INTERVAL", "60"))
    except ValueError:
        value = 60
    return 0 if value <= 0 else min(86400, max(10, value))


def start_memory_monitor() -> Thread | None:
    """Un seul thread démon, sans historique en RAM. Intervalle 0 = désactivé."""
    global _monitor
    interval = _interval()
    if interval == 0:
        return None
    with _monitor_lock:
        if _monitor is not None and _monitor.is_alive():
            return _monitor

        def monitor():
            while True:
                log_memory("periodic")
                time.sleep(interval)

        _monitor = Thread(target=monitor, name="memory-monitor", daemon=True)
        _monitor.start()
        return _monitor
