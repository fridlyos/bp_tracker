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
  (first = start page), routers, optional background `jobs`.
- `app/web.py` — core HTML routes: launcher `/` (small icon-only button per
  module (44px, name in aria-label/title), nothing else; admin reaches Users via the account menu),
  register/login/logout, admin `/users-ui` (create/reset/delete
  users, "Switch to this user" = logout + login prefilled), 308 redirects from
  pre-module URLs (`/trends` → `/bp/trends`, ...).
- `app/deps.py` — session user: `current_user` (HTML, None = anonymous ->
  `/login`), `api_user` (JSON, 401). Signed cookie `uid` + `u` (username).
- `app/ui.py` — `templates`, `render()` (finds the module owning the path, so
  `base.html` shows its tab bar + breadcrumb), `redirect`, `safe_next`,
  `APP_NAME`.
- `app/service.py` / `app/schemas.py` — users only.
- `app/push.py` + `app/api.py` (`/api/push/key|subscribe|unsubscribe|test`) —
  Web Push for any module: `push.send_to_user(conn, uid, title, body, url)`.
  VAPID keys auto-generated into `app_kv` on first use (or `VAPID_PRIVATE_KEY`
  PEM + `VAPID_PUBLIC_KEY` env); 404/410 subscriptions are dropped. iPhone
  needs the home-screen app (iOS 16.4+) and a tap on "Enable notifications"
  (`static/push.js`); `sw.js` shows the push and opens its `url` on tap.
- `app/scheduler.py` — in-process loop started in lifespan, runs every
  module's `jobs` once a minute as `job(conn, now_utc)`. Jobs must be
  idempotent. `SCHEDULER=off` disables it (tests call `run_jobs_once(now)`).
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
  `/bp/reports`, `/bp/history`, `/bp/settings` (reminders + purge).
- Reminders (`bp_reminders`): daily `HH:MM` in the IANA zone the browser had
  when it was added. Job `service.send_reminders` fires each at most once per
  local day, up to 30 min late (restarts), and skips if a reading was logged
  in the 2 hours before.
- `api.py` — JSON under `/api/bp` (readings CRUD + purge, `/api/bp/stats`
  chart data, `/api/bp/reports`).
- `service.py` — readings SQL, stats, reports, reminders.
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

Medications module (`app/modules/meds/`, templates in `app/templates/meds/`):
- Pages under `/meds`: Today (checklist of today's slots: Taken / Skip / Undo,
  plus "as needed" meds with Log dose), `/meds/history` (last 30 days),
  `/meds/manage` (add/edit, pause = hidden + no reminders but history kept,
  delete = removes history). JSON under `/api/meds` (list, today, doses).
- Tables (`0003_meds`): `meds` (name, dose text, notes, tz, active),
  `med_times` (`HH:MM`, `last_reminded`), `med_doses` (slot
  `scheduled_for` 'YYYY-MM-DDTHH:MM' or NULL for as-needed, status
  taken|skipped, `logged_at`; unique per med+slot, re-marking replaces).
- Job `service.send_reminders`: one push per user per minute listing every
  med due now (in the med's zone, ≤30 min late, once per day per time),
  skipping slots already marked (e.g. taken early). Tapping opens `/meds`.
- Validation: `schemas.MedIn` (name required ≤80, dose ≤80, notes ≤300,
  ≤6 distinct `HH:MM` times; none = as needed).

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
- Env: `BP_DB` (default `bp.db`), `SECRET_KEY`, `COOKIE_SECURE`, `ADMIN_PASSWORD`, `LOG_LEVEL`,
  `SCHEDULER` (on/off), `VAPID_SUBJECT`, optional `VAPID_PRIVATE_KEY` / `VAPID_PUBLIC_KEY`.
