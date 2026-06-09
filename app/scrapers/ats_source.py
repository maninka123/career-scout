"""Scrape company career pages via the public Greenhouse and Lever ATS APIs.

These are stable JSON endpoints (no key, no scraping fragility):
  Greenhouse: https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
  Lever:      https://api.lever.co/v0/postings/<token>?mode=json
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List

import requests

from app.config import AtsConfig
from app.scrapers.base import NormalizedJob, clean_str

logger = logging.getLogger(__name__)

TIMEOUT = 20
HEADERS = {"User-Agent": "job-finder/1.0 (personal use)"}
_TAG_RE = re.compile(r"<[^>]+>")


def scrape(cfg: AtsConfig) -> List[NormalizedJob]:
    jobs: List[NormalizedJob] = []
    for token in cfg.greenhouse:
        jobs.extend(_greenhouse(token))
    for token in cfg.lever:
        jobs.extend(_lever(token))
    if cfg.keywords:
        jobs = _filter_keywords(jobs, cfg.keywords)
    return jobs


def _filter_keywords(jobs: List[NormalizedJob], keywords: List[str]) -> List[NormalizedJob]:
    lowered = [k.lower() for k in keywords]
    return [j for j in jobs if any(k in j.title.lower() for k in lowered)]


def _strip_html(text: str) -> str:
    return _TAG_RE.sub(" ", text or "").strip()


def _greenhouse(token: str) -> List[NormalizedJob]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Greenhouse fetch failed for %r: %s", token, exc)
        return []

    jobs: List[NormalizedJob] = []
    for item in data.get("jobs", []):
        job_url = clean_str(item.get("absolute_url"))
        if not job_url:
            continue
        location = clean_str((item.get("location") or {}).get("name"))
        jobs.append(
            NormalizedJob(
                source="greenhouse",
                job_url=job_url,
                title=clean_str(item.get("title")),
                company=token,
                location=location,
                is_remote="remote" in location.lower(),
                description=_strip_html(item.get("content", ""))[:20000],
                date_posted=_parse_iso(item.get("updated_at")),
            )
        )
    return jobs


def _lever(token: str) -> List[NormalizedJob]:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Lever fetch failed for %r: %s", token, exc)
        return []

    jobs: List[NormalizedJob] = []
    for item in data:
        job_url = clean_str(item.get("hostedUrl"))
        if not job_url:
            continue
        categories = item.get("categories") or {}
        location = clean_str(categories.get("location"))
        jobs.append(
            NormalizedJob(
                source="lever",
                job_url=job_url,
                title=clean_str(item.get("text")),
                company=token,
                location=location,
                is_remote="remote" in location.lower(),
                description=_strip_html(item.get("descriptionPlain") or item.get("description", ""))[:20000],
                job_type=clean_str(categories.get("commitment")),
                date_posted=_parse_epoch_ms(item.get("createdAt")),
            )
        )
    return jobs


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _parse_epoch_ms(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None
