"""Orchestrate all scraper sources, dedupe/upsert into the DB, log each run."""
from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import List

from app.config import Config, get_config
from app.db import SessionLocal, init_db
from app.models import STATUS_NEW, Job, ScrapeRun
from app.scrapers import ats_source, jobspy_source
from app.scrapers.base import NormalizedJob

logger = logging.getLogger(__name__)

# A single in-process lock prevents the manual "Scrape now" button from running
# concurrently with the in-app scheduler. (Cross-process safety relies on the
# README recommendation to use either launchd OR the in-app scheduler.)
_run_lock = threading.Lock()


def is_running() -> bool:
    return _run_lock.locked()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def collect_jobs(config: Config) -> tuple[List[NormalizedJob], Counter, List[str]]:
    """Run every enabled source. Returns (jobs, per-source counts, error messages)."""
    jobs: List[NormalizedJob] = []
    counts: Counter = Counter()
    errors: List[str] = []

    for search in config.searches:
        try:
            result = jobspy_source.scrape(search)
            jobs.extend(result)
            for j in result:
                counts[j.source] += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"jobspy[{search.query}]: {exc}"
            logger.warning(msg)
            errors.append(msg)

    if config.ats.enabled and (config.ats.greenhouse or config.ats.lever):
        try:
            result = ats_source.scrape(config.ats)
            jobs.extend(result)
            for j in result:
                counts[j.source] += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"ats: {exc}"
            logger.warning(msg)
            errors.append(msg)

    if config.seek.enabled and config.seek.searches:
        try:
            from app.scrapers import seek_source  # imported lazily (optional Playwright)

            for s in config.seek.searches:
                result = seek_source.scrape(s)
                jobs.extend(result)
                for j in result:
                    counts[j.source] += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"seek: {exc}"
            logger.warning(msg)
            errors.append(msg)

    return jobs, counts, errors


def _upsert(session, job: NormalizedJob, now: datetime) -> bool:
    """Insert a new job or update last_seen on an existing one.

    Returns True if a brand-new row was created. User-set status/notes are
    always preserved; salary/description are backfilled only when newly present.
    """
    existing = session.query(Job).filter(Job.job_url == job.job_url).one_or_none()
    if existing is None:
        session.add(
            Job(
                source=job.source,
                job_url=job.job_url,
                title=job.title,
                company=job.company,
                location=job.location,
                country=job.country,
                is_remote=job.is_remote,
                description=job.description,
                job_type=job.job_type,
                salary_min=job.salary_min,
                salary_max=job.salary_max,
                salary_currency=job.salary_currency,
                salary_interval=job.salary_interval,
                date_posted=job.date_posted,
                first_seen=now,
                last_seen=now,
                status=STATUS_NEW,
            )
        )
        return True

    existing.last_seen = now
    if not existing.description and job.description:
        existing.description = job.description
    if existing.salary_min is None and job.salary_min is not None:
        existing.salary_min = job.salary_min
        existing.salary_max = job.salary_max
        existing.salary_currency = job.salary_currency
        existing.salary_interval = job.salary_interval
    return False


def run_scrape() -> dict:
    """Run the full pipeline once. Safe to call from a background thread."""
    if not _run_lock.acquire(blocking=False):
        logger.info("Scrape already in progress; skipping.")
        return {"skipped": True}

    init_db()
    config = get_config()
    now = _utcnow()
    session = SessionLocal()
    run = ScrapeRun(started_at=now, status="running")
    session.add(run)
    session.commit()

    try:
        jobs, counts, errors = collect_jobs(config)

        # Dedupe within this run by job_url before touching the DB.
        seen_urls = set()
        new_count = 0
        for job in jobs:
            if job.job_url in seen_urls:
                continue
            seen_urls.add(job.job_url)
            if _upsert(session, job, now):
                new_count += 1

        run.finished_at = _utcnow()
        run.status = "error" if errors else "ok"
        run.source_counts = json.dumps(dict(counts))
        run.new_jobs = new_count
        run.total_seen = len(seen_urls)
        run.errors = "\n".join(errors)
        session.commit()

        summary = {
            "new_jobs": new_count,
            "total_seen": len(seen_urls),
            "source_counts": dict(counts),
            "errors": errors,
        }
        logger.info("Scrape complete: %s", summary)
        return summary
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        run.finished_at = _utcnow()
        run.status = "error"
        run.errors = str(exc)
        session.commit()
        logger.exception("Scrape failed")
        return {"error": str(exc)}
    finally:
        session.close()
        _run_lock.release()
