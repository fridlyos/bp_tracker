# CLAUDE.md

Guidance for Claude Code in this repo. Roadmap and decisions: `Plan.md`.

## What it is

Personal tracker hub ("My Apps") made of modules; the first module is a blood
pressure + heart rate log. FastAPI + SQLite, server-rendered Jinja2 UI,
installable PWA, deployed on Fly.io. Setup mirrors `../lose_weight`.

## Architecture

Core (shared by all modules):
- `app/main.py` — app wiring: lifespan (migrate + seed admin), session
  middleware, static mount, root-scoped `/sw.js`, catch-all error logger.
  Mounts every router of every module in `MODULES`.
- `app/modules/__init__.py` — `Module` / `NavItem` dataclasses and the
  `MODULES` registry. A module = key, name, description, tile icon, nav items
  (first = start page), routers, optional `summary(conn, user_id, today)` for
  its launcher tile.
- `app/web.py` — core HTML routes: launcher `/` (tile per module + Users tile
  for admin), register/login/logout, admin `/users-ui` (create/reset/delete
  users, "Switch to this user" = logout + login prefilled), 308 redirects from
  pre-module URLs (`/trends` → `/bp/trends`, ...).
- `app/deps.py` — session user: `current_user` (HTML, None = anonymous ->
  `/login`), `api_user` (JSON, 401). Signed cookie `uid` + `u` (username).
- `app/ui.py` — `templates`, `render()` (finds the module owning the path, so
  `base.html` shows its tab bar + breadcrumb), `redirect`, `safe_next`,
  `APP_NAME`.
- `app/service.py` / `app/schemas.py` — users only.
- `app/clock.py` — user-local time. Readings are naive local datetimes
  (`YYYY-MM-DDTHH:MM`); "now"/"today" use the browser zone from the `tz`
  cookie set in `base.html` (fallback UTC, server runs UTC on Fly).
- `app/db.py` — sqlite3 connection, yoyo migrations from `migrations/`,
  admin seed (`ADMIN_PASSWORD`, default `admin`). Backs up an existing DB to
  `backups/` before applying new migrations.
- `app/auth.py` — stdlib pbkdf2 password hashing.
- `app/templates/` (`base.html`, `launcher.html`, auth, users), `app/static/`
  — UI, PWA manifest, service worker, icons. Shell in `base.html`: sticky top
  bar ("My Apps / <module>" breadcrumb, account menu: avatar + username, theme,
  Log out, Users for admin). Inside a module the nav (Home + module pages) is a
  left rail on desktop and a fixed bottom tab bar ≤640px; core pages have no
  nav. iPhone: `viewport-fit=cover` + `env(safe-area-inset-*)` padding, ≥44px
  tap targets, inputs ≥16px (no iOS zoom). Text on primary fills uses
  `--on-primary` (dark text in dark mode).

Blood pressure module (`app/modules/bp/`, templates in `app/templates/bp/`):
- `web.py` — pages under `/bp`: Today (quick add + today's list), `/bp/trends`,
  `/bp/reports`, `/bp/history`, `/bp/settings` (purge).
- `api.py` — JSON under `/api/bp` (readings CRUD + purge, `/api/bp/stats`
  chart data, `/api/bp/reports`).
- `service.py` — readings SQL, stats, reports, launcher tile summary.
- `schemas.py` — `ReadingIn`, `Reading`, `PurgeIn`.
- `templates/bp/trends.html` — Chart.js 4.4.1 (cdnjs) page fed by
  `/api/bp/stats`. BP and pulse are separate charts (never a dual axis). BP
  lines are colour-coded by zone (owner's thresholds): systolic ≤120 green,
  121–129 blue, 130–139 yellow, 140+ red; diastolic ≤80 green, 81–90 yellow,
  91+ red. Zones use the rounded (displayed) value; the line colour is a
  vertical gradient with hard stops at x.5. Zone legend + tooltip label carry
  it in words too. Colours are `.viz-root` `--z-*` tokens, each ≥3:1 on the
  light surface `#fff` and the dark chart panel `#1b3149` (light yellow is
  amber `#c98500` for contrast).
- `templates/bp/reports.html` — `/bp/reports?period=week|month`: per week
  (Mon–Sun) or month, counts, avg/min/max and readings per BP zone. Zone
  thresholds live in `service.SYS_ZONES` / `DIA_ZONES` and must match the ones
  in `trends.html`.

## Adding a module

1. `app/modules/<key>/` with `web.py` (`APIRouter(prefix="/<key>")`),
   `api.py` (`prefix="/api/<key>"`), `service.py`, `schemas.py`, and
   `__init__.py` exposing `MODULE = Module(...)`.
2. Templates in `app/templates/<key>/`, extending `base.html`, rendered via
   `ui.render` (nav appears automatically).
3. Append to `MODULES` in `app/modules/__init__.py`; tables in a new migration.

## Rules

- Every module query filters by the acting `user_id`.
- Schema changes = new file in `migrations/` (never edit an applied one).
- BP validation lives in `modules/bp/schemas.ReadingIn`: systolic 50–260, diastolic 30–160,
  systolic > diastolic, pulse 30–220. Web and API both go through it.
- Confirm prompts: `<form data-confirm="...">` (handler in `base.html`); never
  interpolate user text into inline JS.
- No build step; Chart.js from CDN, pinned version.
- Text from user data goes into the DOM via `textContent`, never `innerHTML`.

## Commands

- `uv run pytest` — tests (use throwaway DB via `BP_DB`).
- Deploy: push/merge to `main` -> `.github/workflows/deploy.yml` runs tests, then
  `flyctl deploy` (repo secret `FLY_API_TOKEN`). Manual: `flyctl deploy`.
- `./start.sh` or `uv run uvicorn app.main:app --reload` — local server on :8000.
- Env: `BP_DB` (default `bp.db`), `SECRET_KEY`, `COOKIE_SECURE`, `ADMIN_PASSWORD`, `LOG_LEVEL`.
