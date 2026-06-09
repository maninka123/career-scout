# Career Scout

A personal job aggregator that scrapes multiple job boards on a schedule and lets you browse, filter, and track applications from a clean web UI — no manual re-runs needed.

---

## How it works

```
┌─────────────────────────────────────────────────────┐
│                    Data Sources                      │
│                                                      │
│  Indeed (AU/US/UK/CA…)   LinkedIn   Glassdoor        │
│  Google Jobs             Seek (AU)  Greenhouse/Lever │
└──────────────┬──────────────────────────────────────┘
               │  python-jobspy + custom scrapers
               ▼
┌─────────────────────────────────────────────────────┐
│                  Pipeline  (pipeline.py)             │
│                                                      │
│   collect → normalise → dedupe (by job URL) → upsert│
│                        ↓                            │
│               SQLite  (jobs.db, WAL mode)            │
└──────────────┬──────────────────────────────────────┘
               │
       ┌───────┴───────┐
       │               │
       ▼               ▼
 launchd / cron   FastAPI web server
 (unattended      http://localhost:8000
  daily runs)     ↓
                Browse · Filter · Save · Apply
```

## Scheduling flow

```
 Machine starts / 07:00 daily
        │
        ▼
 launchd fires  ──►  scripts/run_scrape.py  ──►  pipeline.run_scrape()
        │                                              │
        │                                    writes ScrapeRun to DB
        │
 (web server running?)
        │  yes
        ▼
 "Scrape now" button  ──►  POST /scrape  ──►  background thread
        │                                    (guarded by threading.Lock —
        │                                     skips if already running)
        ▼
 / refreshes with latest jobs
```

---

## Setup

```bash
git clone https://github.com/maninka123/career-scout
cd career-scout

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium   # only if you enable Seek in config.yaml
```

---

## Configuration

Everything is in **`config.yaml`** — no code changes needed.

| Section | What to change |
|---|---|
| `searches` | Role + location blocks. Use `country: australia / usa / uk / canada / germany / singapore` etc. |
| `seek` | Set `enabled: true`, add queries. |
| `ats.greenhouse` / `ats.lever` | Add company tokens — find them in `boards.greenhouse.io/<token>`. |
| `ats.keywords` | Only keep ATS jobs whose title contains these words. |
| `schedule.enabled` | `false` if you use launchd; `true` + a time if you prefer in-app scheduling. |

---

## Run

**First scrape (test):**
```bash
python scripts/run_scrape.py
```

**Web UI:**
```bash
uvicorn app.main:app --reload
# open http://localhost:8000
```

**Schedule with launchd (macOS, runs even when the app is closed):**

1. Edit `launchd/com.jobfinder.scrape.plist` — replace `__PROJECT_DIR__` and `__PYTHON__`.
2. Load it:
```bash
cp launchd/com.jobfinder.scrape.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jobfinder.scrape.plist
```

---

## Web UI overview

```
http://localhost:8000/

 ┌──────────────────────────────────────────────────────────┐
 │ Career Scout          [Scrape history]  [Scrape now ▶]   │
 ├──────────────────────────────────────────────────────────┤
 │ Keyword ___  Source ▾  Country ▾  Status ▾  □ Remote     │
 │                                               [Filter]   │
 ├──────────────────────────────────────────────────────────┤
 │ ● Data Scientist @ Atlassian · Sydney · indeed           │
 │   AU$120k–150k/year · posted 2026-06-08    [Apply ↗]     │
 │                               [Save] [Applied] [Hide]    │
 │                                                          │
 │ ● ML Engineer @ Canva · Remote · linkedin                │
 │   ...                                      [Apply ↗]     │
 └──────────────────────────────────────────────────────────┘
```

Click a job title for the full description + a notes field.

---

## Project layout

```
career-scout/
├── config.yaml          ← edit this to add searches
├── requirements.txt
├── scripts/
│   └── run_scrape.py    ← standalone scrape (used by launchd)
├── launchd/
│   └── com.jobfinder.scrape.plist
└── app/
    ├── main.py           FastAPI app entry point
    ├── config.py         config.yaml loader (Pydantic)
    ├── db.py             SQLAlchemy + SQLite WAL
    ├── models.py         Job, ScrapeRun
    ├── pipeline.py       orchestrates sources → DB
    ├── scheduler.py      optional APScheduler
    ├── routes.py         web routes
    ├── scrapers/
    │   ├── base.py       NormalizedJob dataclass
    │   ├── jobspy_source.py  Indeed/LinkedIn/Glassdoor/Google
    │   ├── ats_source.py     Greenhouse + Lever APIs
    │   └── seek_source.py    custom Seek scraper
    └── templates/
        ├── base.html
        ├── index.html    job list + filters
        ├── job.html      detail + notes
        └── runs.html     scrape history
```
