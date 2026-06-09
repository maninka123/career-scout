"""Boolean keyword matching and job categorization."""
from __future__ import annotations

from typing import List

from app.presets import categorize  # re-export for convenience


def _text(job_title: str, description: str = "") -> str:
    return (job_title + " " + description[:1000]).lower()


def matches(
    title: str,
    description: str,
    match_any: List[str],
    match_at_least_one: List[str],
    exclude: List[str],
) -> bool:
    """Return True iff the job passes all three filter groups.

    match_any: job must contain at least one of these (if list is non-empty).
    match_at_least_one: job must contain at least one of these (if list is non-empty).
    exclude: job must NOT contain any of these.
    """
    text = _text(title, description)

    if match_any and not any(kw.lower() in text for kw in match_any):
        return False

    if match_at_least_one and not any(kw.lower() in text for kw in match_at_least_one):
        return False

    if any(kw.lower() in text for kw in exclude):
        return False

    return True


def get_category(title: str, description: str = "") -> str:
    return categorize(title, description)
