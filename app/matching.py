"""Boolean keyword matching and job categorization."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import List

from app.presets import categorize  # re-export for convenience


def _text(job_title: str, description: str = "") -> str:
    return (job_title + " " + description[:1000]).lower()


@lru_cache(maxsize=4096)
def _pattern(kw: str) -> "re.Pattern[str] | None":
    """Compile a word-boundary matcher for a keyword.

    Uses non-alphanumeric boundaries so that short tokens like "ros" match the
    standalone word "ros"/"ROS2" but NOT substrings inside other words such as
    "Rose", "Pros", "across" or "process" — which previously caused a Mental
    Health Therapist at "Rose Family Counseling" to be tagged as robotics.
    """
    kw = kw.lower().strip()
    if not kw:
        return None
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")


def _hit(text: str, kw: str) -> bool:
    pat = _pattern(kw)
    return pat is not None and pat.search(text) is not None


def _is_phrase(kw: str) -> bool:
    """Multi-word / multi-token keywords are specific enough to trust in a
    description (e.g. "autonomous systems", "machine learning engineer")."""
    kw = kw.strip()
    return " " in kw or "-" in kw


def _group_passes(title: str, full_text: str, keywords: List[str]) -> bool:
    """A keyword counts toward the group if it appears (as a whole word) in the
    TITLE, or — for specific multi-word phrases — anywhere in the description.

    Single ambiguous words ("autonomy", "ros", "ai", "digital") only count when
    they are in the title. This keeps precision high: relevant jobs almost always
    carry a relevant term in their title, while a generic word buried in a
    description is usually incidental.
    """
    for kw in keywords:
        if _hit(title, kw):
            return True
        if _is_phrase(kw) and _hit(full_text, kw):
            return True
    return False


def matches(
    title: str,
    description: str,
    match_any: List[str],
    match_at_least_one: List[str],
    exclude: List[str],
) -> bool:
    """Return True iff the job passes all three filter groups.

    match_any: job's title must contain at least one of these (or a multi-word
        phrase appears in the description), if the list is non-empty.
    match_at_least_one: same rule applied as a second required group.
    exclude: job must NOT contain any of these (whole-word, title or description).
    """
    title_l = title.lower()
    full_l = _text(title, description)

    if any(_hit(full_l, kw) for kw in exclude):
        return False

    if match_any and not _group_passes(title_l, full_l, match_any):
        return False

    if match_at_least_one and not _group_passes(title_l, full_l, match_at_least_one):
        return False

    return True


def get_category(title: str, description: str = "") -> str:
    return categorize(title, description)
