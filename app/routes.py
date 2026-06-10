"""Web routes: dashboard, jobs, profiles CRUD, settings, history."""
from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests as _requests
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, or_

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
    return session.query(func.count(Job.id)).filter(Job.status == "new", Job.removed.is_(False)).scalar() or 0

def _bin_count(session) -> int:
    return session.query(func.count(Job.id)).filter(Job.removed.is_(True)).scalar() or 0


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
        "bin_count": _bin_count(session),
        "scraping": is_running(),
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    session = SessionLocal()
    try:
        _active = Job.removed.is_(False)
        total = session.query(func.count(Job.id)).filter(_active).scalar() or 0
        new_count = _new_jobs_count(session)
        saved_count = session.query(func.count(Job.id)).filter(_active, Job.status == "saved").scalar() or 0
        applied_count = session.query(func.count(Job.id)).filter(_active, Job.status == "applied").scalar() or 0

        today = datetime.now(timezone.utc).date()
        new_today = session.query(func.count(Job.id)).filter(
            _active, func.date(Job.first_seen) == today.isoformat()
        ).scalar() or 0

        # By source
        by_source = {r[0]: r[1] for r in session.query(Job.source, func.count(Job.id)).filter(_active).group_by(Job.source).all() if r[0]}

        # By country
        by_country = {r[0]: r[1] for r in session.query(Job.country, func.count(Job.id)).filter(_active).group_by(Job.country).order_by(func.count(Job.id).desc()).limit(10).all() if r[0]}

        # By category
        by_category = {r[0]: r[1] for r in session.query(Job.category, func.count(Job.id)).filter(_active).group_by(Job.category).order_by(func.count(Job.id).desc()).limit(12).all() if r[0]}

        # Top companies
        top_companies = session.query(Job.company, func.count(Job.id).label("cnt")).filter(_active, Job.company != "").group_by(Job.company).order_by(func.count(Job.id).desc()).limit(10).all()

        # Salary coverage
        with_salary = session.query(func.count(Job.id)).filter(_active, Job.salary_min.isnot(None)).scalar() or 0
        salary_pct = round(with_salary * 100 / total) if total else 0

        # Salary breakdown — convert all currencies to AUD (approximate)
        _to_aud = {"AUD": 1.0, "USD": 1.55, "GBP": 1.95, "EUR": 1.70, "CAD": 1.13, "SGD": 1.15, "NZD": 0.91, "CHF": 1.75, "SEK": 0.14, "NOK": 0.14, "DKK": 0.23}
        sal_jobs = session.query(Job).filter(
            _active,
            Job.salary_min.isnot(None),
            Job.salary_interval == "yearly",
        ).all()
        salary_stats = {}
        if sal_jobs:
            def to_aud(val, currency):
                return val * _to_aud.get(currency or "USD", 1.55)

            converted = [
                (to_aud(j.salary_min, j.salary_currency), to_aud(j.salary_max or j.salary_min, j.salary_currency))
                for j in sal_jobs
            ]
            midpoints = [(lo + hi) / 2 for lo, hi in converted]
            all_mins = [lo for lo, _ in converted]
            all_maxs = [hi for _, hi in converted]
            buckets = [
                ("Under $80k",   0,       80000),
                ("$80k–$110k",   80000,   110000),
                ("$110k–$140k",  110000,  140000),
                ("$140k–$180k",  140000,  180000),
                ("$180k–$250k",  180000,  250000),
                ("$250k+",       250000,  None),
            ]
            bucket_counts = []
            for label, lo, hi in buckets:
                count = sum(1 for m in midpoints if m >= lo and (hi is None or m < hi))
                bucket_counts.append((label, count))
            max_bucket = max(c for _, c in bucket_counts) or 1
            salary_stats = {
                "count": len(sal_jobs),
                "avg_min": int(sum(all_mins) / len(all_mins)),
                "avg_max": int(sum(all_maxs) / len(all_maxs)),
                "floor": int(min(all_mins)),
                "ceiling": int(max(all_maxs)),
                "buckets": [(lbl, cnt, round(cnt * 100 / max_bucket)) for lbl, cnt in bucket_counts],
            }

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
        recent_jobs = session.query(Job).filter(_active, Job.status == "new").order_by(Job.first_seen.desc()).limit(8).all()

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
            "salary_stats": salary_stats,
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
    per_page: int = 40,
):
    session = SessionLocal()
    try:
        query = session.query(Job).filter(Job.removed.is_(False))
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

        # Active profile names — used to sort profile-matched jobs to the top
        active_profile_names = [
            r[0] for r in session.query(SearchProfile.name)
            .filter(SearchProfile.enabled.is_(True)).all()
        ]

        # Sort priority:
        # 0 = from an active profile (most relevant to user)
        # 1 = from config.yaml searches
        # 2 = everything else
        # Within each tier: salary present first, then newest first
        if active_profile_names:
            priority = case(
                (Job.source_profile.in_(active_profile_names), 0),
                (Job.source_profile == "config", 1),
                else_=2,
            )
        else:
            priority = case(
                (Job.source_profile == "config", 0),
                else_=1,
            )

        has_salary = case((Job.salary_min.isnot(None), 0), else_=1)

        total = query.count()
        per_page = per_page if per_page in (20, 40, 60, 100) else 40
        page = max(1, page)
        import math
        total_pages = max(1, math.ceil(total / per_page))
        page = min(page, total_pages)

        job_list = (
            query.order_by(priority, has_salary, Job.first_seen.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        last_run = session.query(ScrapeRun).order_by(ScrapeRun.started_at.desc()).first()
        profiles_list = session.query(SearchProfile.name).order_by(SearchProfile.name).all()

        ctx = _base_ctx(session, request)
        ctx.update({
            "jobs": job_list,
            "total": total,
            "page": page,
            "page_size": per_page,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "filters": {"q": q, "source": source, "country": country, "status": status, "remote": remote, "category": category, "profile": profile, "per_page": per_page},
            "sources": _distinct(session, Job.source),
            "countries": _distinct(session, Job.country),
            "categories": _distinct(session, Job.category),
            "profiles_list": [r[0] for r in profiles_list],
            "active_profile_names": active_profile_names,
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
# Manual job add
# ---------------------------------------------------------------------------

@router.post("/jobs/add-manual")
async def add_manual_job(request: Request):
    from app.matching import get_category as _cat
    form = await request.form()
    title = form.get("title", "").strip()
    company = form.get("company", "").strip()
    url = form.get("url", "").strip()
    location = form.get("location", "").strip()
    if not title or not url:
        return JSONResponse({"error": "Title and URL are required."}, status_code=400)
    from app.pipeline import _infer_country
    country = _infer_country(location, "")
    now = datetime.now(timezone.utc)
    session = SessionLocal()
    try:
        existing = session.query(Job).filter(Job.job_url == url).one_or_none()
        if existing and existing.removed:
            return JSONResponse({"error": "This listing is in your recycle bin. Restore it first."})
        if existing:
            return JSONResponse({"already": True, "id": existing.id, "title": existing.title})
        cat = _cat(title, "")
        j = Job(
            source="manual", job_url=url, title=title, company=company,
            location=location, country=country, is_remote=False,
            description="", job_type="", salary_min=None, salary_max=None,
            salary_currency="", salary_interval="", date_posted=None,
            first_seen=now, last_seen=now, status="new",
            category=cat, source_profile="manual", removed=False,
        )
        session.add(j)
        session.commit()
        return JSONResponse({"ok": True, "id": j.id, "title": j.title})
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Recycle bin
# ---------------------------------------------------------------------------

@router.post("/job/{job_id}/remove")
def job_remove(job_id: int, redirect: str = Form("/jobs")):
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        if job:
            job.removed = True
            session.commit()
    finally:
        session.close()
    return RedirectResponse(redirect, status_code=303)


@router.post("/job/{job_id}/restore")
def job_restore(job_id: int):
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        if job:
            job.removed = False
            session.commit()
    finally:
        session.close()
    return RedirectResponse("/bin", status_code=303)


@router.post("/job/{job_id}/delete-forever")
def job_delete_forever(job_id: int):
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        if job:
            session.delete(job)
            session.commit()
    finally:
        session.close()
    return RedirectResponse("/bin", status_code=303)


@router.get("/bin", response_class=HTMLResponse)
def bin_page(request: Request):
    session = SessionLocal()
    try:
        removed_jobs = (
            session.query(Job)
            .filter(Job.removed.is_(True))
            .order_by(Job.last_seen.desc())
            .all()
        )
        ctx = _base_ctx(session, request)
        ctx["removed_jobs"] = removed_jobs
        return templates.TemplateResponse("bin.html", ctx)
    finally:
        session.close()


@router.post("/bin/empty")
def bin_empty():
    session = SessionLocal()
    try:
        session.query(Job).filter(Job.removed.is_(True)).delete()
        session.commit()
    finally:
        session.close()
    return RedirectResponse("/bin", status_code=303)


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
# Company ATS discovery
# ---------------------------------------------------------------------------

_ATS_PATTERNS = {
    "greenhouse":     re.compile(r"boards\.greenhouse\.io/([A-Za-z0-9_\-]+)", re.I),
    "lever":          re.compile(r"jobs\.lever\.co/([A-Za-z0-9_\-]+)", re.I),
    "ashby":          re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_\-]+)", re.I),
    "smartrecruiters": re.compile(r"careers\.smartrecruiters\.com/([A-Za-z0-9_\-]+)", re.I),
}

def _detect_ats(text: str, url: str) -> dict | None:
    for platform, pat in _ATS_PATTERNS.items():
        for haystack in (url, text):
            m = pat.search(haystack)
            if m:
                return {"platform": platform, "slug": m.group(1)}
    return None


def _playwright_crawl(company_input: str) -> tuple[list[dict], str, str]:
    """
    Crawl a company careers page with Playwright.
    Returns (job_dicts, company_name, error).
    job_dicts have keys: title, url, location.
    If an ATS redirect is detected during crawl, returns (__ats__, platform, slug).
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    domain = company_input.strip()
    domain = re.sub(r"^https?://", "", domain, flags=re.I)
    domain = re.sub(r"^www\.", "", domain, flags=re.I).rstrip("/")
    if "." not in domain:
        domain = f"{domain}.com"

    candidates = [
        f"https://{domain}/careers",
        f"https://{domain}/jobs",
        f"https://{domain}/about/careers",
        f"https://{domain}/company/careers",
        f"https://careers.{domain}",
        f"https://jobs.{domain}",
        f"https://{domain}",
    ]

    company_name = domain.split(".")[0].replace("-", " ").title()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ))
            page.set_default_timeout(20000)

            loaded = False
            for url in candidates:
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    if resp and resp.ok:
                        # Check if page redirected to a known ATS
                        ats = _detect_ats(page.content(), page.url)
                        if ats:
                            browser.close()
                            return [{"__ats__": True, **ats}], company_name, ""
                        loaded = True
                        break
                except PWTimeout:
                    continue
                except Exception:
                    continue

            if not loaded:
                browser.close()
                return [], company_name, "Could not load the company's careers page."

            # Wait for JS-rendered content (SuccessFactors / Workday need extra time)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            page.wait_for_timeout(2500)

            # Scroll in steps to trigger lazy-loading
            for _ in range(3):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(600)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)

            # Try to get company name from page title
            try:
                title = page.title()
                if title:
                    cn = title.split("|")[0].split("–")[0].split("-")[0].strip()
                    if 3 < len(cn) < 60:
                        company_name = cn
            except Exception:
                pass

            # ---- helper: extract real job listings from current page ----
            def _extract_jobs_from_page(pg):
                """Run the JS extraction on `pg` and return list of job dicts."""
                return pg.evaluate("""
                    () => {
                        const jobs = [];
                        const seen = new Set();
                        const pageBase = window.location.href.split('#')[0].split('?')[0];

                        function getLocation(el) {
                            const loc = el.querySelector(
                                '[class*="location"],[class*="loc"],[class*="city"],[class*="place"],' +
                                '[data-qa*="location"],[data-testid*="location"],[class*="region"]'
                            );
                            return loc ? loc.innerText.trim() : '';
                        }
                        function addJob(title, url, location) {
                            const t = title.trim();
                            if (!t || t.length < 5 || t.length > 160) return;
                            if (seen.has(url)) return;
                            // Skip obvious non-jobs
                            if (/^(view all|see all|all jobs|load more|show more)$/i.test(t)) return;
                            if (url === pageBase || url.endsWith('#') || !url.startsWith('http')) return;
                            seen.add(url);
                            jobs.push({ title: t, url, location: (location||'').trim() });
                        }

                        // Strategy 1: structured job-card containers
                        const containerSels = [
                            '[data-qa*="job"]','[data-testid*="job"]','[data-automation*="job"]',
                            '[data-testid*="listing"]','[data-automation*="listing"]',
                            '[class*="job-item"]','[class*="job-card"]','[class*="job-listing"]',
                            '[class*="job-row"]','[class*="job-result"]','[class*="jobListItem"]',
                            '[class*="opening-item"]','[class*="position-item"]','[class*="role-item"]',
                            '[class*="vacancy-item"]','[class*="requisition"]','[class*="resultItem"]',
                            '[class*="jobResultItem"]','[class*="job_preview"]',
                            'li[class*="job"]','article[class*="job"]','tr[class*="job"]',
                        ];
                        for (const sel of containerSels) {
                            const els = document.querySelectorAll(sel);
                            if (els.length < 2) continue;
                            const before = jobs.length;
                            for (const el of els) {
                                const link = el.querySelector('a[href]') || (el.tagName==='A' ? el : null);
                                if (!link || !link.href) continue;
                                const titleEl = el.querySelector(
                                    'h1,h2,h3,h4,h5,strong,[class*="title"],[class*="name"],[class*="role"],[class*="position"]'
                                );
                                addJob((titleEl||link).innerText, link.href, getLocation(el));
                            }
                            if (jobs.length - before >= 3) break;
                        }

                        // Strategy 2: densest link-bearing list/grid
                        if (jobs.length < 3) {
                            const pageHref = window.location.href.split('#')[0];
                            const lists = [...document.querySelectorAll('ul,ol,tbody,[class*="list"],[class*="grid"],[class*="results"]')];
                            let best = null, bestScore = 0;
                            for (const list of lists) {
                                const links = [...list.querySelectorAll('a[href]')].filter(a =>
                                    a.href && a.href !== pageHref && !a.href.endsWith('/') &&
                                    !a.href.includes('#') && a.innerText.trim().length > 5
                                );
                                if (links.length > bestScore) { bestScore = links.length; best = list; }
                            }
                            if (best && bestScore >= 3) {
                                for (const a of best.querySelectorAll('a[href]')) {
                                    const parent = a.closest('li,tr,div');
                                    addJob(a.innerText.trim(), a.href, getLocation(parent||a));
                                }
                            }
                        }

                        // Strategy 3: URL-pattern fallback
                        if (jobs.length < 3) {
                            for (const a of document.querySelectorAll('a[href]')) {
                                const href = a.href;
                                const text = a.innerText.trim();
                                const isJobUrl = /\\/(job|position|opening|role|vacancy|requisition)[\\/\\-\\?][\\w\\-]+/i.test(href)
                                    || /workday\\.com|icims\\.com|taleo\\.net|successfactors|jobvite|bamboohr|brassring|smartrecruiters/i.test(href);
                                if (isJobUrl && text) {
                                    const parent = a.closest('li,div,tr,article');
                                    addJob(text, href, getLocation(parent||a));
                                }
                            }
                        }

                        // Deduplicate by normalised title
                        const byTitle = new Map();
                        for (const j of jobs) {
                            const k = j.title.toLowerCase().replace(/\\s+/g,' ');
                            if (!byTitle.has(k)) byTitle.set(k, j);
                        }
                        return [...byTitle.values()].slice(0, 200);
                    }
                """)

            # ---- helper: detect if results are categories not real jobs ----
            def _looks_like_categories(found):
                """Return True when scraped items are clearly category/nav links, not real jobs."""
                if not found or len(found) > 40:
                    return False
                # Category-like title patterns: ends with "Jobs"/"Positions", or is a pure nav phrase
                cat_count = sum(
                    1 for j in found
                    if re.search(r'\bjobs$|\bpositions$|\bvacancies$|\bopportunities$', j["title"], re.I)
                    or re.search(r'^(view all|see all|all jobs|all positions|load more|browse all|show all)$', j["title"], re.I)
                    or re.search(r'^(engineering|technology|corporate|operations|finance|hr|it|sales|marketing)\s+jobs$', j["title"], re.I)
                )
                return cat_count / max(len(found), 1) > 0.5

            # First extraction attempt
            raw = _extract_jobs_from_page(page)

            # If we only got category/nav links, go one level deeper
            if _looks_like_categories(raw):
                # Find "View All Jobs" link first, then try each category
                deeper_url = page.evaluate("""
                    () => {
                        // Prefer "View All Jobs" type links
                        for (const a of document.querySelectorAll('a[href]')) {
                            const t = a.innerText.trim().toLowerCase();
                            if (/view all|see all|all jobs|all positions|all openings/.test(t))
                                return a.href;
                        }
                        return null;
                    }
                """)

                if deeper_url:
                    try:
                        page.goto(deeper_url, wait_until="domcontentloaded", timeout=20000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=12000)
                        except Exception:
                            pass
                        page.wait_for_timeout(3000)
                        for _ in range(4):
                            page.evaluate("window.scrollBy(0, window.innerHeight)")
                            page.wait_for_timeout(700)
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(2000)
                        raw = _extract_jobs_from_page(page)
                    except Exception:
                        pass

                # If still categories (or no "View All"), scrape each category URL
                if _looks_like_categories(raw) or not raw:
                    category_urls = [j["url"] for j in (raw or [])
                                     if j["url"] != page.url and not j["url"].endswith("#")][:6]
                    all_deep = []
                    for cat_url in category_urls:
                        try:
                            page.goto(cat_url, wait_until="domcontentloaded", timeout=15000)
                            try:
                                page.wait_for_load_state("networkidle", timeout=10000)
                            except Exception:
                                pass
                            page.wait_for_timeout(2500)
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            page.wait_for_timeout(1200)
                            found = _extract_jobs_from_page(page)
                            if not _looks_like_categories(found):
                                all_deep.extend(found)
                        except Exception:
                            continue
                    if all_deep:
                        # Deduplicate across categories
                        seen_urls = set()
                        raw = []
                        for j in all_deep:
                            if j["url"] not in seen_urls:
                                seen_urls.add(j["url"])
                                raw.append(j)

            browser.close()
            if raw:
                return raw[:200], company_name, ""
            return [], company_name, "No job listings found. The site may require login or use a platform that blocks automated access."

    except Exception as exc:
        return [], company_name, f"Browser scrape failed: {str(exc)[:200]}"

def _discover_ats(company_input: str) -> dict | None:
    raw = company_input.strip()
    domain = re.sub(r"^https?://", "", raw, flags=re.I)
    domain = re.sub(r"^www\.", "", domain, flags=re.I).rstrip("/")
    if "." not in domain:
        domain = f"{domain}.com"

    candidates = [
        f"https://{domain}/careers",
        f"https://{domain}/jobs",
        f"https://{domain}/about/careers",
        f"https://careers.{domain}",
        f"https://jobs.{domain}",
        f"https://{domain}",
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Career Scout bot)"}
    for url in candidates:
        try:
            resp = _requests.get(url, timeout=8, allow_redirects=True, headers=headers)
            result = _detect_ats(resp.text, resp.url)
            if result:
                return result
        except Exception:
            continue
    return None


@router.get("/settings/discover")
def discover_company(company: str = ""):
    if not company.strip():
        return JSONResponse({"error": "No company provided"}, status_code=400)
    result = _discover_ats(company)
    if result:
        return JSONResponse(result)
    return JSONResponse({"error": f"Could not find a supported ATS for '{company}'. They may use Workday, Taleo, or another platform."})


@router.post("/scrape/company")
async def scrape_company_jobs(request: Request):
    import asyncio
    from app.matching import get_category as _cat

    form = await request.form()
    company_input = form.get("company", "").strip()
    if not company_input:
        return JSONResponse({"error": "Please enter a company name or URL."}, status_code=400)

    # Run blocking Playwright crawl in a thread so we don't block the event loop
    loop = asyncio.get_event_loop()
    raw_list, company_name, crawl_error = await loop.run_in_executor(
        None, _playwright_crawl, company_input
    )

    # Check if Playwright detected an ATS redirect
    platform, slug = None, None
    if raw_list and raw_list[0].get("__ats__"):
        platform = raw_list[0]["platform"]
        slug = raw_list[0]["slug"]
        raw_list = []  # will be filled by ATS API below

    # If no ATS detected yet, try fast URL-pattern discovery
    if not platform:
        ats = _discover_ats(company_input)
        if ats:
            platform, slug = ats["platform"], ats["slug"]

    now = datetime.now(timezone.utc)
    session = SessionLocal()
    new_count = 0
    preview = []
    source_label = "website"

    try:
        if platform and slug:
            # Use fast ATS API (reliable, gets descriptions + salaries)
            from app.scrapers.ats_source import scrape_tokens
            kwargs: dict = {"greenhouse": [], "lever": [], "ashby": [], "smartrecruiters": []}
            kwargs[platform] = [slug]
            try:
                raw_jobs = await loop.run_in_executor(None, lambda: scrape_tokens(**kwargs))
            except Exception as exc:
                return JSONResponse({"error": f"ATS scraping failed: {exc}"})

            source_label = platform.title()
            for nj in raw_jobs:
                existing = session.query(Job).filter(Job.job_url == nj.job_url).one_or_none()
                if existing is not None and existing.removed:
                    continue
                cat = _cat(nj.title or "", nj.description or "")
                if existing is None:
                    session.add(Job(
                        source=nj.source, job_url=nj.job_url, title=nj.title or "",
                        company=nj.company or slug, location=nj.location or "",
                        country=nj.country or "", is_remote=nj.is_remote or False,
                        description=nj.description or "", job_type=nj.job_type or "",
                        salary_min=nj.salary_min, salary_max=nj.salary_max,
                        salary_currency=nj.salary_currency or "", salary_interval=nj.salary_interval or "",
                        date_posted=nj.date_posted, first_seen=now, last_seen=now,
                        status="new", category=cat, source_profile=f"company:{slug}", removed=False,
                    ))
                    new_count += 1
                else:
                    existing.last_seen = now
                preview.append({
                    "title": nj.title or "Untitled",
                    "location": nj.location or "",
                    "url": nj.job_url,
                    "salary": (
                        f"{nj.salary_currency or '$'}{int(nj.salary_min):,}"
                        + (f"–{int(nj.salary_max):,}" if nj.salary_max else "")
                    ) if nj.salary_min else "",
                })

        elif raw_list:
            # Playwright-extracted jobs (no ATS — custom career page)
            source_label = "Website"
            for item in raw_list:
                url = item.get("url", "")
                title = item.get("title", "").strip()
                if not url or not title:
                    continue
                existing = session.query(Job).filter(Job.job_url == url).one_or_none()
                if existing is not None and existing.removed:
                    continue
                cat = _cat(title, "")
                if existing is None:
                    session.add(Job(
                        source="website", job_url=url, title=title,
                        company=company_name, location=item.get("location", ""),
                        country="", is_remote=False, description="",
                        job_type="", salary_min=None, salary_max=None,
                        salary_currency="", salary_interval="",
                        date_posted=None, first_seen=now, last_seen=now,
                        status="new", category=cat,
                        source_profile=f"company:{company_name}", removed=False,
                    ))
                    new_count += 1
                else:
                    existing.last_seen = now
                preview.append({"title": title, "location": item.get("location", ""), "url": url, "salary": ""})

        else:
            err = crawl_error or f"No jobs found for '{company_input}'. The site may require login or use an unsupported platform."
            return JSONResponse({"error": err})

        session.commit()
    except Exception as exc:
        session.rollback()
        return JSONResponse({"error": f"Import failed: {exc}"})
    finally:
        session.close()

    return JSONResponse({
        "platform": source_label,
        "slug": slug or company_name,
        "company": company_name,
        "total": len(preview),
        "new": new_count,
        "jobs": preview,
    })


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
