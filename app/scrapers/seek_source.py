"""Custom Seek.com.au scraper.

Seek is not covered by JobSpy. This is a best-effort, more-fragile scraper:
it requests the search results page and extracts the embedded
`window.SEEK_REDUX_DATA` JSON blob. If the page is challenged/JS-gated and
Playwright is installed, it falls back to a rendered fetch.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote

import requests

from app.config import SeekSearch
from app.scrapers.base import NormalizedJob, clean_str

logger = logging.getLogger(__name__)

BASE = "https://www.seek.com.au"
TIMEOUT = 25
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
}
_REDUX_RE = re.compile(r"window\.SEEK_REDUX_DATA\s*=\s*(\{.*?\});", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _build_url(search: SeekSearch, page: int) -> str:
    parts = [BASE]
    query_slug = _slug(search.query)
    path = f"/{query_slug}-jobs" if query_slug else "/jobs"
    if search.location:
        path += f"/in-{quote(search.location)}"
    return f"{BASE}{path}?page={page}"


def scrape(search: SeekSearch) -> List[NormalizedJob]:
    jobs: List[NormalizedJob] = []
    for page in range(1, max(1, search.pages) + 1):
        url = _build_url(search, page)
        html = _fetch(url)
        if not html:
            continue
        page_jobs = _parse(html)
        if not page_jobs:
            logger.info("Seek: no jobs parsed on %s (may be JS-gated).", url)
            break
        jobs.extend(page_jobs)
    return jobs


def _fetch(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 200 and "SEEK_REDUX_DATA" in resp.text:
            return resp.text
        logger.info("Seek plain fetch incomplete (%s); trying Playwright.", resp.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Seek requests fetch failed for %s: %s", url, exc)

    return _fetch_rendered(url)


def _fetch_rendered(url: str) -> Optional[str]:
    """Optional Playwright fallback for JS-gated pages."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("Playwright not installed; skipping Seek render fallback.")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto(url, wait_until="networkidle", timeout=30000)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:  # noqa: BLE001
        logger.warning("Seek Playwright fetch failed for %s: %s", url, exc)
        return None


def _parse(html: str) -> List[NormalizedJob]:
    match = _REDUX_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    # Navigate defensively: results.results.jobs is the typical shape.
    results = (
        data.get("results", {})
        .get("results", {})
        .get("jobs", [])
    )
    jobs: List[NormalizedJob] = []
    for item in results:
        job_id = clean_str(item.get("id"))
        if not job_id:
            continue
        location = _location(item)
        jobs.append(
            NormalizedJob(
                source="seek",
                job_url=f"{BASE}/job/{job_id}",
                title=clean_str(item.get("title")),
                company=clean_str((item.get("advertiser") or {}).get("description")),
                location=location,
                country="australia",
                is_remote="remote" in location.lower(),
                description=_TAG_RE.sub(" ", clean_str(item.get("teaser"))),
                job_type=clean_str(item.get("workType")),
                date_posted=_parse_date(item.get("listingDate")),
            )
        )
    return jobs


def _location(item: dict) -> str:
    locs = item.get("locations") or []
    if locs and isinstance(locs, list):
        return clean_str(locs[0].get("label"))
    return clean_str(item.get("location"))


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
