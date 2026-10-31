# Project Plan — BP Tracker

## Goal

Personal blood pressure + heart rate log, usable from iPhone (PWA) or web.
Record readings, review daily averages, chart trends, prune old data. Cheap to
run. Setup and deployment mirror `../lose_weight`.

## Decisions

- **Stack:** Python 3.12, FastAPI, Jinja2 server-rendered UI, uv — same as lose_weight.
- **DB:** SQLite via stdlib `sqlite3` + yoyo migrations, file on a Fly volume.
  lose_weight is SQLite too (not Postgres). Two Fly apps cannot share one SQLite
  volume, so bp_tracker gets its own `bp.db`. Revisit shared Postgres only if a
  cross-app view is ever needed.
- **Auth:** copy lose_weight's cookie session auth (`auth.py`, register/login,
  seeded admin). Users are separate from the calorie app.
- **Charts:** Chart.js from CDN, data from a JSON endpoint. No build step.
- **Hosting:** Fly.io, new app `bp-tracker-fridl`, region `iad`, 256MB shared
  VM, `data` volume at `/data`, `SECRET_KEY` as Fly secret, `COOKIE_SECURE=true`.

## Data model

```
users(id, username UNIQUE, password_hash, is_admin, created_at)
readings(id, user_id -> users, taken_at TEXT ISO local datetime,
         systolic INT, diastolic INT, pulse INT NULL, note TEXT NULL)
index on readings(user_id, taken_at)
```

Validation: systolic 50–260, diastolic 30–160, systolic > diastolic, pulse 30–220.
Every query filters by acting `user_id`.

## Phases

### Phase 0 — Scaffold (done in this commit)
- Rename `pb_tracker` → `bp_tracker`, `git init`, Plan.md, .gitignore.

### Phase 1 — Skeleton from lose_weight (done)
- `pyproject.toml` (fastapi, uvicorn, jinja2, python-multipart, itsdangerous,
  yoyo-migrations; dev: pytest). Drop openai/tavily/httpx.
- Copy and trim: `app/db.py` (env `BP_DB`, default `bp.db`), `app/auth.py`,
  login/register/users templates, `base.html`, `style.css`, PWA manifest + `sw.js`
  + icons, `start.sh`, `Dockerfile`, `.dockerignore`, `.env.example`.
- `migrations/0001_initial.py` with users + readings.
- CLAUDE.md, README.md.
- Test: register → login → home renders.

### Phase 2 — Readings CRUD (done)
- `service.py`: `add_reading`, `list_readings(from, to)`, `delete_reading`,
  `purge_before(date)`.
- Web: home = quick-add form (sys / dia / pulse / note, `taken_at` defaults to
  now via `<input type="datetime-local">`) + today's readings list.
- History page: readings grouped by day, per-row delete (x), confirm dialog.
- Settings page: "delete everything before [date]" with count preview + confirm.
- JSON API mirrors: `POST /api/readings`, `GET /api/readings`,
  `DELETE /api/readings/{id}`, `POST /api/readings/purge`.
- Tests: CRUD, validation, user isolation, purge boundary.

### Phase 3 — Averages + charts
- `service.daily_averages(user, from, to)` — one SQL `GROUP BY date(taken_at)`
  returning avg sys/dia/pulse + count.
- `GET /api/stats?range=day|week|month|3m`:
  - day → individual readings for the day
  - week / month → daily averages over last 7 / 30 days
  - 3m → daily averages over 90 days (switch to weekly buckets if too noisy)
- Chart page: range tabs, Chart.js line chart (sys, dia, pulse), tap a day
  → list that day's individual readings.
- Optional: shaded normal band (<120/80) on chart.
- Tests: averages math, range bounds.

### Phase 4 — Deploy
- `fly.toml` copied, app `bp-tracker-fridl`, env `BP_DB=/data/bp.db`.
- `flyctl apps create`, `flyctl volumes create data --size 1`,
  `flyctl secrets set SECRET_KEY=...`, `flyctl deploy`.
- Rotate seeded admin password (lose_weight still has admin/admin).
- GitHub repo `fridlyos/bp_tracker`, push.
- Add to iPhone home screen.

### Later (only if wanted)
- CSV export, Apple Health import, morning/evening tagging, alerts on high readings.
