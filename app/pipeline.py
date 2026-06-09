"""Orchestrate all scraper sources, dedupe/upsert into DB, log each run."""
from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import List, Optional

from app.config import Config, Search, get_config
from app.db import SessionLocal, init_db
from app.matching import get_category, matches
from app.models import STATUS_NEW, AppSetting, Job, SearchProfile, ScrapeRun
from app.scrapers import jobspy_source
from app.scrapers.base import NormalizedJob

logger = logging.getLogger(__name__)

_run_lock = threading.Lock()

# US state abbreviations — their presence in a location string means the job is in the USA
_US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}

def _infer_country(location: str, fallback: str = "") -> str:
    """Derive the real country from the location string, overriding the search-tagged country."""
    if not location:
        return fallback
    loc = location.upper()
    # Check each comma/dot/bullet segment for a US state code
    import re as _re
    for part in _re.split(r"[,·•\|]", loc):
        if part.strip() in _US_STATES:
            return "united states"
    if any(x in loc for x in ("UNITED STATES", " USA", "U.S.A", " U.S.")):
        return "united states"
    if any(x in loc for x in ("UNITED KINGDOM", "ENGLAND", "SCOTLAND", "WALES", " UK")):
        return "united kingdom"
    if "AUSTRALIA" in loc:
        return "australia"
    if "CANADA" in loc:
        return "canada"
    if "GERMANY" in loc or "DEUTSCHLAND" in loc:
        return "germany"
    if "SINGAPORE" in loc:
        return "singapore"
    if "NEW ZEALAND" in loc:
        return "new zealand"
    if "SWEDEN" in loc:
        return "sweden"
    if "NORWAY" in loc:
        return "norway"
    if "NETHERLANDS" in loc or "HOLLAND" in loc:
        return "netherlands"
    return fallback


def is_running() -> bool:
    return _run_lock.locked()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_setting(session, key: str, default: str = "") -> str:
    row = session.get(AppSetting, key)
    return row.value if row else default


def _get_json_setting(session, key: str, default=None):
    raw = _get_setting(session, key, json.dumps(default or []))
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default or []


def _collect_ats(session) -> List[NormalizedJob]:
    """Fetch ATS jobs from all four providers using DB-stored token lists."""
    from app.scrapers.ats_source import scrape_tokens

    gh = _get_json_setting(session, "ats_greenhouse", [])
    lv = _get_json_setting(session, "ats_lever", [])
    ash = _get_json_setting(session, "ats_ashby", [])
    sr = _get_json_setting(session, "ats_smartrecruiters", [])

    # Also pick up tokens from config.yaml for backward compatibility
    cfg = get_config()
    if cfg.ats.enabled:
        gh = list(set(gh + cfg.ats.greenhouse))
        lv = list(set(lv + cfg.ats.lever))
        ash = list(set(ash + getattr(cfg.ats, "ashby", [])))
        sr = list(set(sr + getattr(cfg.ats, "smartrecruiters", [])))

    if not (gh or lv or ash or sr):
        return []

    return scrape_tokens(gh, lv, ash, sr)


def _collect_config_searches(config: Config) -> tuple[List[NormalizedJob], Counter, List[str]]:
    jobs: List[NormalizedJob] = []
    counts: Counter = Counter()
    errors: List[str] = []
    for search in config.searches:
        try:
            result = jobspy_source.scrape(search)
            for j in result:
                j.source_profile = "config"
            jobs.extend(result)
            for j in result:
                counts[j.source] += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"jobspy[config/{search.query}]: {exc}"
            logger.warning(msg)
            errors.append(msg)
    return jobs, counts, errors


def _collect_seek(config: Config) -> tuple[List[NormalizedJob], Counter, List[str]]:
    jobs: List[NormalizedJob] = []
    counts: Counter = Counter()
    errors: List[str] = []
    if config.seek.enabled and config.seek.searches:
        try:
            from app.scrapers import seek_source

            for s in config.seek.searches:
                result = seek_source.scrape(s)
                for j in result:
                    j.source_profile = "config"
                jobs.extend(result)
                for j in result:
                    counts[j.source] += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"seek: {exc}"
            logger.warning(msg)
            errors.append(msg)
    return jobs, counts, errors


def _collect_profile(profile: SearchProfile, ats_jobs: List[NormalizedJob]) -> tuple[List[NormalizedJob], Counter, List[str]]:
    """Scrape one SearchProfile and apply boolean matching."""
    jobs: List[NormalizedJob] = []
    counts: Counter = Counter()
    errors: List[str] = []

    try:
        countries = json.loads(profile.countries or "[]") or ["australia"]
        sites = json.loads(profile.sites or '["indeed","linkedin"]')
        roles = json.loads(profile.roles or "[]")
        match_any = json.loads(profile.match_any or "[]")
        match_at_least_one = json.loads(profile.match_at_least_one or "[]")
        exclude = json.loads(profile.exclude or "[]")
    except json.JSONDecodeError:
        return jobs, counts, [f"profile {profile.name}: JSON decode error"]

    # Build the search query: roles + match_any keywords combined with OR
    role_terms = [r.lower() for r in roles]
    any_terms = [m.lower() for m in match_any]
    combined = list(dict.fromkeys(role_terms + any_terms))  # dedupe, preserve order
    search_term = " OR ".join(f'"{t}"' if " " in t else t for t in combined[:8]) if combined else profile.name

    for country in countries:
        search = Search(
            query=search_term,
            location="",
            country=country,
            sites=sites,
            results_wanted=profile.results_wanted,
            hours_old=profile.hours_old,
            is_remote=profile.is_remote,
        )
        try:
            raw = jobspy_source.scrape(search)
        except Exception as exc:  # noqa: BLE001
            msg = f"profile {profile.name}/{country}: {exc}"
            logger.warning(msg)
            errors.append(msg)
            continue

        for j in raw:
            if not matches(j.title, j.description, match_any + roles, match_at_least_one, exclude):
                continue
            j.source_profile = profile.name
            jobs.append(j)
            counts[j.source] += 1

    # Optionally include ATS jobs for this profile
    if profile.use_ats and ats_jobs:
        for j in ats_jobs:
            if not matches(j.title, j.description, match_any + roles, match_at_least_one, exclude):
                continue
            # Clone to tag with this profile's name without mutating shared obj
            tagged = NormalizedJob(**{k: getattr(j, k) for k in j.__dataclass_fields__})
            tagged.source_profile = profile.name
            jobs.append(tagged)
            counts[j.source] += 1

    return jobs, counts, errors


def _upsert(session, job: NormalizedJob, now: datetime, category: str) -> bool:
    existing = session.query(Job).filter(Job.job_url == job.job_url).one_or_none()
    if existing is not None and existing.removed:
        return False  # user removed this listing — never re-surface it
    if existing is None:
        real_country = _infer_country(job.location, job.country)
        session.add(Job(
            source=job.source,
            job_url=job.job_url,
            title=job.title,
            company=job.company,
            location=job.location,
            country=real_country,
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
            category=category,
            source_profile=getattr(job, "source_profile", "config"),
        ))
        return True

    existing.last_seen = now
    if not existing.description and job.description:
        existing.description = job.description
    if existing.salary_min is None and job.salary_min is not None:
        existing.salary_min = job.salary_min
        existing.salary_max = job.salary_max
        existing.salary_currency = job.salary_currency
        existing.salary_interval = job.salary_interval
    if existing.category == "Other":
        existing.category = category
    return False


def run_scrape(profile_ids: Optional[List[int]] = None) -> dict:
    """Run the full pipeline. profile_ids=None means run everything."""
    if not _run_lock.acquire(blocking=False):
        logger.info("Scrape already in progress; skipping.")
        return {"skipped": True}

    init_db()
    config = get_config()
    now = _utcnow()
    session = SessionLocal()
    run = ScrapeRun(started_at=now, status="running",
                    profile_name=",".join(str(i) for i in profile_ids) if profile_ids else "all")
    session.add(run)
    session.commit()

    try:
        all_jobs: List[NormalizedJob] = []
        all_counts: Counter = Counter()
        all_errors: List[str] = []

        # 1. config.yaml searches (always run unless specific profile_ids given)
        if not profile_ids:
            j, c, e = _collect_config_searches(config)
            all_jobs.extend(j); all_counts.update(c); all_errors.extend(e)
            j, c, e = _collect_seek(config)
            all_jobs.extend(j); all_counts.update(c); all_errors.extend(e)

        # 2. ATS jobs (fetched once, shared across profiles)
        ats_jobs: List[NormalizedJob] = []
        try:
            ats_jobs = _collect_ats(session)
            for j in ats_jobs:
                j.source_profile = "ats"
            if not profile_ids:
                all_jobs.extend(ats_jobs)
                for j in ats_jobs:
                    all_counts[j.source] += 1
        except Exception as exc:  # noqa: BLE001
            all_errors.append(f"ats: {exc}")

        # 3. Search profiles
        if profile_ids:
            profiles = session.query(SearchProfile).filter(SearchProfile.id.in_(profile_ids)).all()
        else:
            profiles = session.query(SearchProfile).filter(SearchProfile.enabled.is_(True)).all()

        for profile in profiles:
            j, c, e = _collect_profile(profile, ats_jobs)
            all_jobs.extend(j); all_counts.update(c); all_errors.extend(e)

        # 4. Dedupe + upsert
        seen_urls: set[str] = set()
        new_count = 0
        for job in all_jobs:
            if job.job_url in seen_urls:
                continue
            seen_urls.add(job.job_url)
            cat = get_category(job.title, job.description)
            if _upsert(session, job, now, cat):
                new_count += 1

        # 5. Update profile last_run stats
        for profile in profiles:
            profile.last_run_at = now
            profile.last_new_count = sum(
                1 for j in all_jobs
                if getattr(j, "source_profile", "") == profile.name
                and j.job_url not in seen_urls
            )

        run.finished_at = _utcnow()
        run.status = "error" if all_errors else "ok"
        run.source_counts = json.dumps(dict(all_counts))
        run.new_jobs = new_count
        run.total_seen = len(seen_urls)
        run.errors = "\n".join(all_errors)
        session.commit()

        summary = {
            "new_jobs": new_count,
            "total_seen": len(seen_urls),
            "source_counts": dict(all_counts),
            "errors": all_errors,
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
