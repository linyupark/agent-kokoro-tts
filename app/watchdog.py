"""Periodic RSS watchdog: logs a warning when memory creeps up.

Purely observational — the container MEM_LIMIT is the hard backstop. Helps
catch slow leaks in long-running deployments before they bite.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)

_WARN_EVERY_SEC = 3600  # don't spam: at most one warning per hour


async def start_memory_watchdog(app, interval_sec: float = 1800.0) -> asyncio.Task:
    settings = app.state.settings
    baseline = {"rss_mb": None, "last_warn": 0.0}

    async def loop() -> None:
        import psutil

        proc = psutil.Process()
        limit_mb = _parse_mem_limit()
        while True:
            await asyncio.sleep(interval_sec)
            try:
                rss_mb = proc.memory_info().rss / 1e6
                if baseline["rss_mb"] is None:
                    baseline["rss_mb"] = rss_mb
                growth = rss_mb - baseline["rss_mb"]
                msg = f"内存看门狗: RSS={rss_mb:.0f}MB (较基线 {growth:+.0f}MB)"
                if limit_mb and rss_mb > limit_mb * 0.85:
                    msg += f" — 已达 MEM_LIMIT({limit_mb:.0f}MB)的 85%,建议重启容器"
                if growth > 300 or (limit_mb and rss_mb > limit_mb * 0.85):
                    now = time.time()
                    if now - baseline["last_warn"] > _WARN_EVERY_SEC:
                        baseline["last_warn"] = now
                        log.warning(msg)
                    else:
                        log.debug(msg)
                else:
                    log.info(msg)
            except Exception:  # noqa: BLE001 - watchdog must never crash the app
                continue

    return asyncio.create_task(loop())


def _parse_mem_limit() -> float:
    """Read cgroup memory limit (Docker MEM_LIMIT) in MB; 0 if unknown."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as fp:
                value = int(fp.read().strip())
            if 0 < value < (1 << 50):  # ignore the "unlimited" sentinel
                return value / 1e6
        except (OSError, ValueError):
            continue
    return 0.0
