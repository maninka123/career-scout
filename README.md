# Career Scout

A personal job-finding tool built for robotics and autonomous-systems roles. It scrapes multiple job boards on a schedule, organises listings by relevance to your search profiles, and lets you browse, filter, and track applications from a clean browser-based UI — no repeated manual runs needed.

---

## What it does

- Scrapes **Indeed, LinkedIn, Glassdoor, Google Jobs, Seek (AU)** and company career pages automatically
- Pulls jobs directly from **Greenhouse, Lever, Ashby, and SmartRecruiters** company boards via their public APIs
- Scrapes **any company website** by typing the URL — a headless browser visits the careers page and extracts all listings
- Stores everything in a local **SQLite** database with deduplication by job URL
- Surfaces the most relevant jobs first based on your active **Search Profiles**
- Runs on a schedule so your list stays fresh without you doing anything

---

## Architecture

```
  Sources
  ───────
  Indeed · LinkedIn · Glassdoor · Google Jobs          python-jobspy
  Seek (AU)                                            custom scraper
  Greenhouse · Lever · Ashby · SmartRecruiters         ATS public APIs
  Any company URL                                      Playwright browser

            │
            ▼
  Pipeline  (app/pipeline.py)
  ──────────────────────────────────────────────────────
  collect  →  normalise  →  infer real country from location
           →  match against profile keywords
           →  dedupe by job URL  →  upsert to SQLite (WAL mode)
            │
            ▼
       jobs.db  (SQLite)
            │
    ┌───────┴────────┐
    │                │
    ▼                ▼
  launchd /      FastAPI + Jinja2
  APScheduler    http://localhost:8000
  (unattended)   Browse · Filter · Track
```

---

## Features

**Search Profiles**
Define named profiles with role titles, keyword groups, countries, sources, and schedules. Each profile runs its own scrape and tags matching jobs. Jobs from active profiles appear at the top of the list.

**Keyword matching**
Three keyword groups per profile:

- **Match any** — broad terms used to build the search query
- **Must include at least one** — narrows results after scraping
- **Exclude** — filters out jobs containing these words (e.g. senior, manager)

**Dashboard**
Overview of your job database: counts by status, role category, source, and country. Bar and doughnut charts powered by Chart.js. Salary breakdown converted to approximate AUD with distribution across pay brackets.

**Company scraper**
In Settings, type a company name or website URL. Career Scout visits their careers page with a headless browser, detects the ATS platform if present, scrapes all open roles, and imports them directly. Works with custom career pages as well as the four supported ATS platforms.

**Salary display**
Jobs with salary data show a formatted salary badge. The dashboard salary breakdown converts all currencies to approximate AUD and shows a distribution chart across pay bands.

**Country correction**
Infers the real job country from the location field (e.g. "Hawthorne, CA" → United States) rather than trusting the search-country tag, which is often wrong for global job boards.

**Recycle bin**
Remove listings you do not want to see. They go to a recycle bin and will not reappear even after re-scraping. Restore them at any time, or permanently delete them.

**Scheduling**
Per-profile schedules (daily, twice daily, weekly) controlled from the browser. A master on/off switch in Settings. Can also use macOS launchd for system-level scheduling that runs even when the web UI is closed.

---

## Setup

```bash
git clone https://github.com/maninka123/career-scout
cd career-scout

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

# Download the Chromium browser for company page scraping
.venv/bin/playwright install chromium
```

---

## Configuration

**`config.yaml`** controls the baseline searches that run on every scrape alongside your UI-defined profiles.

```yaml
searches:
  - query: "robotics engineer"
    country: australia
    sites: [indeed, linkedin]
    results_wanted: 40
    hours_old: 168

seek:
  enabled: true
  queries: ["robotics engineer", "autonomous systems"]

ats:
  greenhouse: [bostondynamics, zoox]
  lever: [waymo]
  ashby: [anduril]
  smartrecruiters: []

schedule:
  enabled: false   # use launchd instead, or enable in-app scheduling via Settings
  time: "07:00"
```

| Field | Values |
| --- | --- |
| `country` | australia, usa, uk, canada, germany, sweden, singapore, … |
| `sites` | indeed, linkedin, glassdoor, google, seek |
| ATS tokens | the slug from `boards.greenhouse.io/<token>` |

**Search Profiles** are created and managed in the web UI under Search Profiles. They supplement `config.yaml` rather than replacing it.

---

## Running

**First scrape (test run):**
```bash
.venv/bin/python scripts/run_scrape.py
```

**Start the web server:**
```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000)

---

## Scheduling

### Option A — In-app scheduler (recommended for most users)

1. Go to **Settings** in the web UI
2. Turn on the master scheduling switch
3. Open each Search Profile, enable its schedule, and set a time and frequency
4. The APScheduler runs inside the server process — the server must be running

### Option B — macOS launchd (runs even when the server is off)

1. Edit `launchd/com.jobfinder.scrape.plist` — replace `__PROJECT_DIR__` and `__PYTHON__` with your actual paths
2. Load it:

```bash
cp launchd/com.jobfinder.scrape.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jobfinder.scrape.plist
```

The plist fires `scripts/run_scrape.py` at the configured time regardless of whether the web UI is open. Use one approach or the other, not both.

> **Does it run when the Mac is off?**
> No. If the machine is shut down at the scheduled time, the job will not run. Leave the Mac on (sleep is fine) for consistent daily scrapes.

---

## Pages

| Page | Description |
| --- | --- |
| `/` Dashboard | Stats, charts, top companies, salary breakdown, recent jobs and scrape runs |
| `/jobs` | Full job list with filters for source, country, category, profile, remote, status. Active-profile jobs appear first. |
| `/profiles` | List of Search Profiles with enable/disable toggle and per-profile Run Now |
| `/profiles/new` | Create a profile with role presets, keyword chip builder, country and source selectors, and a schedule |
| `/settings` | Master schedule toggle, timezone, ATS token management, company scraper |
| `/bin` | Removed listings — restore or permanently delete |
| `/runs` | History of every scrape run with per-source job counts |

---

## Project layout

```
career-scout/
├── config.yaml                   baseline searches (edit this)
├── requirements.txt
├── scripts/
│   └── run_scrape.py             standalone scrape entry point (used by launchd)
├── launchd/
│   └── com.jobfinder.scrape.plist
└── app/
    ├── main.py                   FastAPI application entry point
    ├── config.py                 config.yaml loader (Pydantic models)
    ├── db.py                     SQLAlchemy engine, migrations, seeding
    ├── models.py                 Job, ScrapeRun, SearchProfile, AppSetting
    ├── pipeline.py               orchestrates all sources, deduplication, upsert
    ├── scheduler.py              APScheduler — dynamic schedule from DB
    ├── matching.py               keyword match/filter functions
    ├── presets.py                robotics role presets, default profiles, categorisation rules
    ├── routes.py                 all web routes and company-scrape endpoint
    ├── scrapers/
    │   ├── base.py               NormalizedJob dataclass
    │   ├── jobspy_source.py      Indeed / LinkedIn / Glassdoor / Google
    │   ├── ats_source.py         Greenhouse / Lever / Ashby / SmartRecruiters
    │   └── seek_source.py        custom Seek scraper (requests + Playwright fallback)
    └── templates/
        ├── base.html             sidebar layout, floating recycle bin button
        ├── dashboard.html        stats, charts, salary breakdown
        ├── jobs.html             job list with filters and pagination
        ├── job.html              job detail, status, notes
        ├── profiles.html         profile list
        ├── profile_form.html     profile create/edit with chip keyword builder
        ├── settings.html         settings + company scraper
        ├── bin.html              recycle bin
        └── runs.html             scrape history
```

---

## Tech stack

| Component | Library |
| --- | --- |
| Web framework | FastAPI + Jinja2 |
| Database | SQLite (WAL mode) via SQLAlchemy 2 |
| Job board scraping | python-jobspy |
| Browser automation | Playwright (Chromium) |
| Frontend interactivity | Alpine.js (CDN) |
| Charts | Chart.js (CDN) |
| Styling | Tailwind CSS (CDN) |
| Scheduling | APScheduler + macOS launchd |
