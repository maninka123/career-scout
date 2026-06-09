"""Database models: Job and ScrapeRun."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Job lifecycle states the user can set from the UI.
STATUS_NEW = "new"
STATUS_SAVED = "saved"
STATUS_APPLIED = "applied"
STATUS_HIDDEN = "hidden"
STATUSES = (STATUS_NEW, STATUS_SAVED, STATUS_APPLIED, STATUS_HIDDEN)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    # job_url is the natural dedupe key across sources.
    job_url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)

    title: Mapped[str] = mapped_column(String(512), default="")
    company: Mapped[str] = mapped_column(String(512), default="", index=True)
    location: Mapped[str] = mapped_column(String(512), default="")
    country: Mapped[str] = mapped_column(String(64), default="", index=True)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)

    description: Mapped[str] = mapped_column(Text, default="")
    job_type: Mapped[str] = mapped_column(String(128), default="")

    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(16), default="")
    salary_interval: Mapped[str] = mapped_column(String(32), default="")

    date_posted: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    status: Mapped[str] = mapped_column(String(16), default=STATUS_NEW, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/ok/error
    # JSON string: per-source counts, e.g. {"indeed": 40, "linkedin": 12}
    source_counts: Mapped[str] = mapped_column(Text, default="{}")
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    total_seen: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str] = mapped_column(Text, default="")
