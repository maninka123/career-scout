"""Shared scraper types and normalization helpers.

Every source produces a list of NormalizedJob; the pipeline handles dedupe and
persistence. job_url is the dedupe key, so each source must supply a stable URL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class NormalizedJob:
    source: str
    job_url: str
    title: str = ""
    company: str = ""
    location: str = ""
    country: str = ""
    is_remote: bool = False
    description: str = ""
    job_type: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = ""
    salary_interval: str = ""
    date_posted: Optional[datetime] = None
    source_profile: str = "config"


def clean_str(value) -> str:
    """Coerce arbitrary/NaN values to a trimmed string."""
    if value is None:
        return ""
    # pandas NaN is a float that != itself.
    if isinstance(value, float) and value != value:
        return ""
    return str(value).strip()


def to_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
