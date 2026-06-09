"""Database models: Job, ScrapeRun, SearchProfile, AppSetting."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

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

    # v2 additions (migrated in via ALTER TABLE if not present)
    category: Mapped[str] = mapped_column(String(64), default="Other", index=True)
    source_profile: Mapped[str] = mapped_column(String(128), default="config")

    # v3: soft-delete / recycle bin
    removed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    source_counts: Mapped[str] = mapped_column(Text, default="{}")
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    total_seen: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str] = mapped_column(Text, default="")
    profile_name: Mapped[str] = mapped_column(String(128), default="")


class SearchProfile(Base):
    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # JSON-encoded lists
    countries: Mapped[str] = mapped_column(Text, default="[]")
    sites: Mapped[str] = mapped_column(Text, default='["indeed","linkedin"]')
    roles: Mapped[str] = mapped_column(Text, default="[]")
    match_any: Mapped[str] = mapped_column(Text, default="[]")
    match_at_least_one: Mapped[str] = mapped_column(Text, default="[]")
    exclude: Mapped[str] = mapped_column(Text, default="[]")
    job_levels: Mapped[str] = mapped_column(Text, default="[]")
    career_fields: Mapped[str] = mapped_column(Text, default="[]")

    results_wanted: Mapped[int] = mapped_column(Integer, default=40)
    hours_old: Mapped[int] = mapped_column(Integer, default=168)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    use_ats: Mapped[bool] = mapped_column(Boolean, default=False)

    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # daily / twice_daily / weekly
    schedule_frequency: Mapped[str] = mapped_column(String(32), default="daily")
    schedule_time: Mapped[str] = mapped_column(String(8), default="07:00")
    timezone: Mapped[str] = mapped_column(String(64), default="Australia/Sydney")

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_new_count: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
