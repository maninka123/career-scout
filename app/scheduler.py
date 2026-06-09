"""Dynamic DB-driven scheduler. Call reload_schedules() after any settings/profile change."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler = BackgroundScheduler(timezone="UTC")


def _frequency_to_cron(frequency: str, time_str: str, tz: str) -> list[CronTrigger]:
    """Return one or more CronTriggers for the given frequency."""
    try:
        hour, minute = (int(x) for x in time_str.split(":"))
    except ValueError:
        hour, minute = 7, 0

    if frequency == "twice_daily":
        return [
            CronTrigger(hour=hour, minute=minute, timezone=tz),
            CronTrigger(hour=(hour + 12) % 24, minute=minute, timezone=tz),
        ]
    if frequency == "every_6h":
        return [CronTrigger(hour=f"{hour},{(hour+6)%24},{(hour+12)%24},{(hour+18)%24}", minute=minute, timezone=tz)]
    if frequency == "weekly":
        return [CronTrigger(day_of_week="mon", hour=hour, minute=minute, timezone=tz)]
    # default: daily
    return [CronTrigger(hour=hour, minute=minute, timezone=tz)]


def reload_schedules() -> None:
    """Rebuild all APScheduler jobs from the DB. Call after any change to profiles or settings."""
    from app.config import get_config
    from app.db import SessionLocal
    from app.models import AppSetting, SearchProfile
    from app.pipeline import run_scrape

    _scheduler.remove_all_jobs()

    session = SessionLocal()
    try:
        master_row = session.get(AppSetting, "scheduling_enabled")
        master_on = master_row and master_row.value.lower() == "true"
        if not master_on:
            logger.info("Scheduler: master scheduling disabled — no jobs registered.")
            return

        profiles = session.query(SearchProfile).filter(
            SearchProfile.enabled.is_(True),
            SearchProfile.schedule_enabled.is_(True),
        ).all()

        for profile in profiles:
            tz = profile.timezone or "Australia/Sydney"
            triggers = _frequency_to_cron(profile.schedule_frequency or "daily", profile.schedule_time or "07:00", tz)
            profile_id = profile.id
            for i, trigger in enumerate(triggers):
                job_id = f"profile_{profile_id}_{i}"
                _scheduler.add_job(
                    run_scrape,
                    trigger,
                    kwargs={"profile_ids": [profile_id]},
                    id=job_id,
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
            logger.info("Scheduled profile %r (%s, %s)", profile.name, profile.schedule_frequency, profile.schedule_time)

        # Also honour config.yaml schedule for backward compatibility
        cfg = get_config()
        if cfg.schedule.enabled:
            try:
                hour, minute = (int(x) for x in cfg.schedule.time.split(":"))
                _scheduler.add_job(
                    run_scrape,
                    CronTrigger(hour=hour, minute=minute, timezone=cfg.schedule.timezone),
                    id="config_schedule",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not register config.yaml schedule: %s", exc)

        logger.info("Scheduler: registered %d profile job(s).", len(profiles))
    finally:
        session.close()


def start_scheduler() -> None:
    if not _scheduler.running:
        _scheduler.start()
    reload_schedules()


def shutdown_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
