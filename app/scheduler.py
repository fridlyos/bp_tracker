"""Once-a-minute background loop that runs every module's jobs.

A job is `job(conn, now_utc)`; it must be idempotent (e.g. remember what it
already sent) because a restart can run the same minute twice. Runs inside
the web process (Fly keeps one machine always on). SCHEDULER=off disables it.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

from . import db

logger = logging.getLogger("bp_tracker.scheduler")


def enabled() -> bool:
    return os.getenv("SCHEDULER", "on").lower() not in ("off", "0", "false")


def run_jobs_once(now: datetime | None = None) -> None:
    from .modules import MODULES  # late: avoid import cycle at app start
    now = now or datetime.now(timezone.utc)
    with db.connect() as conn:
        for m in MODULES:
            for job in m.jobs:
                try:
                    job(conn, now)
                except Exception:
                    logger.exception("job %s.%s failed", m.key, job.__name__)


async def loop() -> None:
    logger.info("scheduler started")
    while True:
        try:
            await asyncio.to_thread(run_jobs_once)
        except Exception:
            logger.exception("scheduler tick failed")
        # wake shortly after the start of each minute
        await asyncio.sleep(60 - datetime.now().second + 1)
