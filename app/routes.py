"""Web routes: dashboard, jobs, profiles CRUD, settings, history."""
from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_

from app.db import SessionLocal
from app.models import STATUSES, AppSetting, Job, SearchProfile, ScrapeRun
from app.pipeline import is_running, run_scrape
from app.presets import CAREER_FIELDS, COUNTRIES, JOB_LEVELS, ROBOTICS_ROLES, SOURCES

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["from_json"] = lambda s: json.loads(s or "[]")

PAGE_SIZE = 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_jobs_count(session) -> int:
    return session.query(func.count(Job.id)).filter(Job.status == "new").scalar() or 0


def _distinct(session, column):
    rows = session.query(column).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


def _get_setting(session, key: str, default: str = "") -> str:
    row = session.get(AppSetting, key)
    return row.value if row else default


def _base_ctx(session, request: Request) -> dict:
    return {
        "request": request,
        "new_jobs_badge": _new_jobs_count(session),
        "scraping": is_running(),
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    session = SessionLocal()
    try:
        total = session.query(func.count(Job.id)).scalar() or 0
        new_count = _new_jobs_count(session)
        saved_count = session.query(func.count(Job.id)).filter(Job.status == "saved").scalar() or 0
        applied_count = session.query(func.count(Job.id)).filter(Job.status == "applied").scalar() or 0

        today = datetime.now(timezone.utc).date()
        new_today = session.query(func.count(Job.id)).filter(
            func.date(Job.first_seen) == today.isoformat()
        ).scalar() or 0

        # By source
        by_source = {r[0]: r[1] for r in session.query(Job.source, func.count(Job.id)).group_by(Job.source).all() if r[0]}

        # By country
        by_country = {r[0]: r[1] for r in session.query(Job.country, func.count(Job.id)).group_by(Job.country).order_by(func.count(Job.id).desc()).limit(10).all() if r[0]}

        # By category
        by_category = {r[0]: r[1] for r in session.query(Job.category, func.count(Job.id)).group_by(Job.category).order_by(func.count(Job.id).desc()).limit(12).all() if r[0]}

        # Top companies
        top_companies = session.query(Job.company, func.count(Job.id).label("cnt")).filter(Job.company != "").group_by(Job.company).order_by(func.count(Job.id).desc()).limit(10).all()

        # Salary coverage
        with_salary = session.query(func.count(Job.id)).filter(Job.salary_min.isnot(None)).scalar() or 0
        salary_pct = round(with_salary * 100 / total) if total else 0

        # Recent runs
        recent_runs = session.query(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(5).all()
        parsed_runs = []
        for r in recent_runs:
            try:
                counts = json.loads(r.source_counts or "{}")
            except Exception:
                counts = {}
            parsed_runs.append((r, counts))

        # Recent new jobs
        recent_jobs = session.query(Job).filter(Job.status == "new").order_by(Job.first_seen.desc()).limit(8).all()

        ctx = _base_ctx(session, request)
        ctx.update({
            "total": total,
            "new_count": new_count,
            "saved_count": saved_count,
            "applied_count": applied_count,
            "new_today": new_today,
            "by_source": by_source,
            "by_country": by_country,
            "by_category": by_category,
            "top_companies": top_companies,
            "with_salary": with_salary,
            "salary_pct": salary_pct,
            "recent_runs": parsed_runs,
            "recent_jobs": recent_jobs,
        })
        return templates.TemplateResponse("dashboard.html", ctx)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Jobs list
# ---------------------------------------------------------------------------

@router.get("/jobs", response_class=HTMLResponse)
def jobs(
    request: Request,
    q: str = "",
    source: str = "",
    country: str = "",
    status: str = "new",
    remote: str = "",
    category: str = "",
    profile: str = "",
    page: int = 1,
):
    session = SessionLocal()
    try:
        query = session.query(Job)
        if q:
            like = f"%{q}%"
            query = query.filter(or_(Job.title.ilike(like), Job.company.ilike(like), Job.description.ilike(like)))
        if source:
            query = query.filter(Job.source == source)
        if country:
            query = query.filter(Job.country == country)
        if status:
            query = query.filter(Job.status == status)
        if remote == "1":
            query = query.filter(Job.is_remote.is_(True))
        if category:
            query = query.filter(Job.category == category)
        if profile:
            query = query.filter(Job.source_profile == profile)

        total = query.count()
        page = max(1, page)
        job_list = (
            query.order_by(Job.first_seen.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
            .all()
        )

        last_run = session.query(ScrapeRun).order_by(ScrapeRun.started_at.desc()).first()
        profiles_list = session.query(SearchProfile.name).order_by(SearchProfile.name).all()

        ctx = _base_ctx(session, request)
        ctx.update({
            "jobs": job_list,
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "has_next": page * PAGE_SIZE < total,
            "filters": {"q": q, "source": source, "country": country, "status": status, "remote": remote, "category": category, "profile": profile},
            "sources": _distinct(session, Job.source),
            "countries": _distinct(session, Job.country),
            "categories": _distinct(session, Job.category),
            "profiles_list": [r[0] for r in profiles_list],
            "statuses": STATUSES,
            "last_run": last_run,
        })
        return templates.TemplateResponse("jobs.html", ctx)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Job detail
# ---------------------------------------------------------------------------

@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        ctx = _base_ctx(session, request)
        ctx.update({"job": job, "statuses": STATUSES})
        return templates.TemplateResponse("job.html", ctx)
    finally:
        session.close()


@router.post("/job/{job_id}/status")
def set_status(job_id: int, status: str = Form(...), redirect: str = Form("/jobs")):
    if status in STATUSES:
        session = SessionLocal()
        try:
            job = session.get(Job, job_id)
            if job:
                job.status = status
                session.commit()
        finally:
            session.close()
    return RedirectResponse(redirect, status_code=303)


@router.post("/job/{job_id}/notes")
def set_notes(job_id: int, notes: str = Form(""), redirect: str = Form("/jobs")):
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        if job:
            job.notes = notes
            session.commit()
    finally:
        session.close()
    return RedirectResponse(redirect, status_code=303)


# ---------------------------------------------------------------------------
# Scrape
# ---------------------------------------------------------------------------

@router.post("/scrape")
def scrape_now():
    if not is_running():
        threading.Thread(target=run_scrape, daemon=True).start()
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

@router.get("/profiles", response_class=HTMLResponse)
def profiles_list(request: Request):
    session = SessionLocal()
    try:
        profiles = session.query(SearchProfile).order_by(SearchProfile.name).all()
        ctx = _base_ctx(session, request)
        ctx["profiles"] = profiles
        return templates.TemplateResponse("profiles.html", ctx)
    finally:
        session.close()


@router.get("/profiles/new", response_class=HTMLResponse)
def profile_new(request: Request):
    session = SessionLocal()
    try:
        ctx = _base_ctx(session, request)
        ctx.update({
            "profile": None,
            "robotics_roles": ROBOTICS_ROLES,
            "career_fields": CAREER_FIELDS,
            "job_levels": JOB_LEVELS,
            "countries": COUNTRIES,
            "sources": SOURCES,
        })
        return templates.TemplateResponse("profile_form.html", ctx)
    finally:
        session.close()


@router.get("/profiles/{profile_id}/edit", response_class=HTMLResponse)
def profile_edit(request: Request, profile_id: int):
    session = SessionLocal()
    try:
        profile = session.get(SearchProfile, profile_id)
        ctx = _base_ctx(session, request)
        ctx.update({
            "profile": profile,
            "robotics_roles": ROBOTICS_ROLES,
            "career_fields": CAREER_FIELDS,
            "job_levels": JOB_LEVELS,
            "countries": COUNTRIES,
            "sources": SOURCES,
        })
        return templates.TemplateResponse("profile_form.html", ctx)
    finally:
        session.close()


@router.post("/profiles/save")
async def profile_save(request: Request):
    form = await request.form()
    session = SessionLocal()
    try:
        profile_id = form.get("profile_id", "")
        profile = session.get(SearchProfile, int(profile_id)) if profile_id else None
        if not profile:
            profile = SearchProfile()
            session.add(profile)

        profile.name = form.get("name", "").strip() or "Unnamed Profile"
        profile.countries = json.dumps([v for k, v in form.multi_items() if k == "countries"])
        profile.sites = json.dumps([v for k, v in form.multi_items() if k == "sites"])
        profile.roles = json.dumps([v for k, v in form.multi_items() if k == "roles"])

        # Chip keywords: comma-separated textarea values
        profile.match_any = json.dumps([t.strip() for t in form.get("match_any", "").split(",") if t.strip()])
        profile.match_at_least_one = json.dumps([t.strip() for t in form.get("match_at_least_one", "").split(",") if t.strip()])
        profile.exclude = json.dumps([t.strip() for t in form.get("exclude", "").split(",") if t.strip()])

        profile.job_levels = json.dumps([v for k, v in form.multi_items() if k == "job_levels"])
        profile.career_fields = json.dumps([v for k, v in form.multi_items() if k == "career_fields"])

        try:
            profile.results_wanted = int(form.get("results_wanted", 40))
        except ValueError:
            profile.results_wanted = 40
        try:
            profile.hours_old = int(form.get("hours_old", 168))
        except ValueError:
            profile.hours_old = 168

        profile.is_remote = form.get("is_remote") == "1"
        profile.use_ats = form.get("use_ats") == "1"
        profile.schedule_enabled = form.get("schedule_enabled") == "1"
        profile.schedule_frequency = form.get("schedule_frequency", "daily")
        profile.schedule_time = form.get("schedule_time", "07:00")
        profile.timezone = form.get("timezone", "Australia/Sydney")

        session.commit()

        from app.scheduler import reload_schedules
        reload_schedules()
    finally:
        session.close()
    return RedirectResponse("/profiles", status_code=303)


@router.post("/profiles/{profile_id}/delete")
def profile_delete(profile_id: int):
    session = SessionLocal()
    try:
        profile = session.get(SearchProfile, profile_id)
        if profile:
            session.delete(profile)
            session.commit()
    finally:
        session.close()
    from app.scheduler import reload_schedules
    reload_schedules()
    return RedirectResponse("/profiles", status_code=303)


@router.post("/profiles/{profile_id}/toggle")
def profile_toggle(profile_id: int):
    session = SessionLocal()
    try:
        profile = session.get(SearchProfile, profile_id)
        if profile:
            profile.enabled = not profile.enabled
            session.commit()
    finally:
        session.close()
    from app.scheduler import reload_schedules
    reload_schedules()
    return RedirectResponse("/profiles", status_code=303)


@router.post("/profiles/{profile_id}/run")
def profile_run(profile_id: int):
    if not is_running():
        threading.Thread(target=run_scrape, kwargs={"profile_ids": [profile_id]}, daemon=True).start()
    return RedirectResponse("/profiles", status_code=303)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    session = SessionLocal()
    try:
        def gs(key, default=""):
            return _get_setting(session, key, default)

        ctx = _base_ctx(session, request)
        ctx.update({
            "scheduling_enabled": gs("scheduling_enabled", "false") == "true",
            "timezone": gs("timezone", "Australia/Sydney"),
            "ats_greenhouse": gs("ats_greenhouse", "[]"),
            "ats_lever": gs("ats_lever", "[]"),
            "ats_ashby": gs("ats_ashby", "[]"),
            "ats_smartrecruiters": gs("ats_smartrecruiters", "[]"),
        })
        return templates.TemplateResponse("settings.html", ctx)
    finally:
        session.close()


@router.post("/settings/save")
async def settings_save(request: Request):
    form = await request.form()
    session = SessionLocal()
    try:
        def ss(key, value):
            row = session.get(AppSetting, key)
            if row:
                row.value = value
            else:
                session.add(AppSetting(key=key, value=value))

        ss("scheduling_enabled", "true" if form.get("scheduling_enabled") == "1" else "false")
        ss("timezone", form.get("timezone", "Australia/Sydney"))

        for provider in ("greenhouse", "lever", "ashby", "smartrecruiters"):
            raw = form.get(f"ats_{provider}", "")
            tokens = [t.strip() for t in raw.replace(",", "\n").splitlines() if t.strip()]
            ss(f"ats_{provider}", json.dumps(tokens))

        session.commit()
    finally:
        session.close()

    from app.scheduler import reload_schedules
    reload_schedules()
    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------------------------
# Scrape history
# ---------------------------------------------------------------------------

@router.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    session = SessionLocal()
    try:
        items = session.query(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(50).all()
        parsed = []
        for r in items:
            try:
                counts = json.loads(r.source_counts or "{}")
            except Exception:
                counts = {}
            parsed.append((r, counts))
        ctx = _base_ctx(session, request)
        ctx["runs"] = parsed
        return templates.TemplateResponse("runs.html", ctx)
    finally:
        session.close()
