"""Scrape Indeed / LinkedIn / Glassdoor / Google Jobs via the python-jobspy library."""
from __future__ import annotations

import logging
from typing import List

from app.config import Search
from app.scrapers.base import NormalizedJob, clean_str, to_float

logger = logging.getLogger(__name__)

# JobSpy DataFrame columns we map from. Salary columns: min_amount, max_amount,
# currency, interval. is_remote may be a real bool or NaN.


def scrape(search: Search) -> List[NormalizedJob]:
    """Run one JobSpy search across its configured sites. Returns normalized jobs."""
    try:
        from jobspy import scrape_jobs
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.error("python-jobspy is not installed: %s", exc)
        return []

    try:
        kwargs: dict = dict(
            site_name=search.sites,
            search_term=search.query,
            location=search.location or None,
            country_indeed=search.country,
            results_wanted=search.results_wanted,
            hours_old=search.hours_old,
            verbose=0,
        )
        if search.is_remote:
            kwargs["is_remote"] = True
        df = scrape_jobs(**kwargs)
    except Exception as exc:  # noqa: BLE001 - one bad search must not kill the run
        logger.warning("JobSpy search failed for %r: %s", search.query, exc)
        return []

    if df is None or df.empty:
        return []

    jobs: List[NormalizedJob] = []
    for row in df.to_dict(orient="records"):
        url = clean_str(row.get("job_url") or row.get("job_url_direct"))
        if not url:
            continue
        is_remote = row.get("is_remote")
        jobs.append(
            NormalizedJob(
                source=clean_str(row.get("site")) or "jobspy",
                job_url=url,
                title=clean_str(row.get("title")),
                company=clean_str(row.get("company")),
                location=clean_str(row.get("location")),
                country=search.country,
                is_remote=bool(is_remote) if isinstance(is_remote, bool) else False,
                description=clean_str(row.get("description")),
                job_type=clean_str(row.get("job_type")),
                salary_min=to_float(row.get("min_amount")),
                salary_max=to_float(row.get("max_amount")),
                salary_currency=clean_str(row.get("currency")),
                salary_interval=clean_str(row.get("interval")),
                date_posted=_parse_date(row.get("date_posted")),
            )
        )
    return jobs


def _parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    try:
        import pandas as pd

        ts = pd.to_datetime(value, errors="coerce")
        if ts is None or pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:  # noqa: BLE001
        return None
