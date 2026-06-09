"""Web routes: job list with filters, detail, status actions, and 'Scrape now'."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_

from app.db import SessionLocal
from app.models import STATUSES, Job, ScrapeRun
from app.pipeline import is_running, run_scrape

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

PAGE_SIZE = 50


def _distinct(session, column):
    rows = session.query(column).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str = "",
    source: str = "",
    country: str = "",
    status: str = "new",
    remote: str = "",
    page: int = 1,
):
    session = SessionLocal()
    try:
        query = session.query(Job)

        if q:
            like = f"%{q}%"
            query = query.filter(or_(Job.title.ilike(like), Job.company.ilike(like)))
        if source:
            query = query.filter(Job.source == source)
        if country:
            query = query.filter(Job.country == country)
        if status:
            query = query.filter(Job.status == status)
        if remote == "1":
            query = query.filter(Job.is_remote.is_(True))

        total = query.count()
        page = max(1, page)
        jobs = (
            query.order_by(Job.first_seen.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
            .all()
        )

        last_run = session.query(ScrapeRun).order_by(ScrapeRun.started_at.desc()).first()

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "jobs": jobs,
                "total": total,
                "page": page,
                "page_size": PAGE_SIZE,
                "has_next": page * PAGE_SIZE < total,
                "filters": {
                    "q": q,
                    "source": source,
                    "country": country,
                    "status": status,
                    "remote": remote,
                },
                "sources": _distinct(session, Job.source),
                "countries": _distinct(session, Job.country),
                "statuses": STATUSES,
                "last_run": last_run,
                "scraping": is_running(),
            },
        )
    finally:
        session.close()


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        return templates.TemplateResponse(
            "job.html", {"request": request, "job": job, "statuses": STATUSES}
        )
    finally:
        session.close()


@router.post("/job/{job_id}/status")
def set_status(job_id: int, status: str = Form(...), redirect: str = Form("/")):
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
def set_notes(job_id: int, notes: str = Form(""), redirect: str = Form("/")):
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        if job:
            job.notes = notes
            session.commit()
    finally:
        session.close()
    return RedirectResponse(redirect, status_code=303)


@router.post("/scrape")
def scrape_now():
    if not is_running():
        threading.Thread(target=run_scrape, daemon=True).start()
    return RedirectResponse("/", status_code=303)


@router.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    session = SessionLocal()
    try:
        items = session.query(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(50).all()
        parsed = []
        for r in items:
            try:
                counts = json.loads(r.source_counts or "{}")
            except json.JSONDecodeError:
                counts = {}
            parsed.append((r, counts))
        return templates.TemplateResponse("runs.html", {"request": request, "runs": parsed})
    finally:
        session.close()
