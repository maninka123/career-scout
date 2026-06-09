"""Optional in-app scheduler (APScheduler) that runs the scrape on a daily timer."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_config
from app.pipeline import run_scrape

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    """Start the daily job if enabled in config. No-op otherwise."""
    global _scheduler
    cfg = get_config().schedule
    if not cfg.enabled:
        logger.info("In-app scheduler disabled (schedule.enabled=false).")
        return

    try:
        hour, minute = (int(x) for x in cfg.time.split(":"))
    except ValueError:
        logger.error("Invalid schedule.time %r; expected HH:MM. Scheduler not started.", cfg.time)
        return

    _scheduler = BackgroundScheduler(timezone=cfg.timezone)
    _scheduler.add_job(
        run_scrape,
        CronTrigger(hour=hour, minute=minute, timezone=cfg.timezone),
        id="daily_scrape",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("In-app scheduler started: daily at %s %s", cfg.time, cfg.timezone)


def shutdown_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
