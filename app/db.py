"""SQLAlchemy engine/session + SQLite WAL + migration + initial seeding."""
from __future__ import annotations

import json
import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_config

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_config = get_config()
DATABASE_URL = f"sqlite:///{_config.database_path}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def _migrate(conn) -> None:
    """Add v2 columns to existing tables without losing data."""
    jobs_cols = _existing_columns(conn, "jobs")
    if "category" not in jobs_cols:
        conn.execute(text("ALTER TABLE jobs ADD COLUMN category TEXT DEFAULT 'Other'"))
        logger.info("Migration: added jobs.category")
    if "source_profile" not in jobs_cols:
        conn.execute(text("ALTER TABLE jobs ADD COLUMN source_profile TEXT DEFAULT 'config'"))
        logger.info("Migration: added jobs.source_profile")
    if "removed" not in jobs_cols:
        conn.execute(text("ALTER TABLE jobs ADD COLUMN removed INTEGER DEFAULT 0"))
        logger.info("Migration: added jobs.removed")

    run_cols = _existing_columns(conn, "scrape_runs")
    if "profile_name" not in run_cols:
        conn.execute(text("ALTER TABLE scrape_runs ADD COLUMN profile_name TEXT DEFAULT ''"))
        logger.info("Migration: added scrape_runs.profile_name")


def _seed_settings(conn) -> None:
    """Insert default AppSettings if not already present."""
    defaults = {
        "scheduling_enabled": "false",
        "timezone": "Australia/Sydney",
        "ats_greenhouse": "[]",
        "ats_lever": "[]",
        "ats_ashby": "[]",
        "ats_smartrecruiters": "[]",
    }
    for key, value in defaults.items():
        conn.execute(
            text("INSERT OR IGNORE INTO app_settings (key, value) VALUES (:k, :v)"),
            {"k": key, "v": value},
        )


def _seed_profiles(conn) -> None:
    """Seed the 10 default search profiles if the table is empty."""
    count = conn.execute(text("SELECT COUNT(*) FROM search_profiles")).scalar()
    if count and count > 0:
        return

    from app.presets import DEFAULT_PROFILES

    for p in DEFAULT_PROFILES:
        conn.execute(
            text("""
                INSERT OR IGNORE INTO search_profiles
                (name, enabled, countries, sites, roles, match_any, match_at_least_one,
                 exclude, job_levels, career_fields, results_wanted, hours_old,
                 is_remote, use_ats, schedule_enabled, schedule_frequency,
                 schedule_time, timezone, last_new_count)
                VALUES
                (:name, 0, :countries, :sites, :roles, :match_any, :match_at_least_one,
                 :exclude, :job_levels, :career_fields, :results_wanted, :hours_old,
                 :is_remote, :use_ats, 0, :schedule_frequency,
                 :schedule_time, :timezone, 0)
            """),
            {
                "name": p["name"],
                "countries": json.dumps(p["countries"]),
                "sites": json.dumps(p["sites"]),
                "roles": json.dumps(p["roles"]),
                "match_any": json.dumps(p["match_any"]),
                "match_at_least_one": json.dumps(p.get("match_at_least_one", [])),
                "exclude": json.dumps(p["exclude"]),
                "job_levels": json.dumps(p.get("job_levels", [])),
                "career_fields": json.dumps(p.get("career_fields", [])),
                "results_wanted": p.get("results_wanted", 40),
                "hours_old": p.get("hours_old", 168),
                "is_remote": 1 if p.get("is_remote") else 0,
                "use_ats": 1 if p.get("use_ats") else 0,
                "schedule_frequency": p.get("schedule_frequency", "daily"),
                "schedule_time": p.get("schedule_time", "07:00"),
                "timezone": p.get("timezone", "Australia/Sydney"),
            },
        )
    logger.info("Seeded %d default search profiles.", len(DEFAULT_PROFILES))


def _backfill_categories(conn) -> None:
    """Backfill category on existing jobs that still have the default 'Other'."""
    from app.presets import categorize

    rows = conn.execute(text("SELECT id, title, description FROM jobs WHERE category = 'Other'")).fetchall()
    for job_id, title, description in rows:
        cat = categorize(title or "", description or "")
        if cat != "Other":
            conn.execute(
                text("UPDATE jobs SET category = :c WHERE id = :id"),
                {"c": cat, "id": job_id},
            )
    if rows:
        logger.info("Backfilled categories for %d jobs.", len(rows))


def _backfill_countries(conn) -> None:
    """Re-infer country from location for jobs where the search country tag is likely wrong."""
    from app.pipeline import _infer_country
    rows = conn.execute(text("SELECT id, location, country FROM jobs WHERE location != ''")).fetchall()
    fixed = 0
    for job_id, location, country in rows:
        inferred = _infer_country(location or "", country or "")
        if inferred and inferred != (country or ""):
            conn.execute(text("UPDATE jobs SET country = :c WHERE id = :id"), {"c": inferred, "id": job_id})
            fixed += 1
    if fixed:
        logger.info("Country backfill: corrected %d jobs.", fixed)


def init_db() -> None:
    """Create tables, run migrations, seed defaults. Safe to call multiple times."""
    from app import models  # noqa: F401 — register on Base.metadata

    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        _migrate(conn)
        _seed_settings(conn)
        _seed_profiles(conn)
        _backfill_categories(conn)
        _backfill_countries(conn)
