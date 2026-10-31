# CLAUDE.md

Guidance for Claude Code in this repo. Roadmap and decisions: `Plan.md`.

## What it is

Personal blood pressure + heart rate log. FastAPI + SQLite, server-rendered
Jinja2 UI, installable PWA, deployed on Fly.io. Setup mirrors `../lose_weight`.

## Architecture

- `app/main.py` — app wiring: lifespan (migrate + seed admin), session
  middleware, static mount, root-scoped `/sw.js`, catch-all error logger.
- `app/web.py` — HTML routes. Acting user comes from the signed session cookie
  (`request.session["uid"]`). Anonymous -> `/login`; admin -> `/users-ui`.
- `app/service.py` — all SQL and business logic. Handlers stay thin.
- `app/db.py` — sqlite3 connection, yoyo migrations from `migrations/`,
  admin seed (`ADMIN_PASSWORD`, default `admin`). Backs up an existing DB to
  `backups/` before applying new migrations.
- `app/auth.py` — stdlib pbkdf2 password hashing.
- `app/templates/`, `app/static/` — UI, PWA manifest, service worker, icons.

## Rules

- Every readings query filters by the acting `user_id`.
- Schema changes = new file in `migrations/` (never edit an applied one).
- Validation: systolic 50–260, diastolic 30–160, systolic > diastolic, pulse 30–220.
- No build step; Chart.js from CDN when charts land.

## Commands

- `uv run pytest` — tests (use throwaway DB via `BP_DB`).
- `./start.sh` or `uv run uvicorn app.main:app --reload` — local server on :8000.
- Env: `BP_DB` (default `bp.db`), `SECRET_KEY`, `COOKIE_SECURE`, `ADMIN_PASSWORD`, `LOG_LEVEL`.
