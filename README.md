# BP Tracker

Personal blood pressure + heart rate log. FastAPI + SQLite + Jinja2, usable
from iPhone as a PWA.

## Setup

```bash
uv sync
cp .env.example .env   # set SECRET_KEY
./start.sh             # http://127.0.0.1:8000/
```

First run creates `bp.db` and an `admin` account (password from
`ADMIN_PASSWORD`, default `admin` — change it). Regular users self-register at
`/register`.

## Tests

```bash
uv run pytest
```

## Deploy (Fly.io)

See Phase 4 in `Plan.md`. App `bp-tracker-fridl`, volume `data` at `/data`,
`BP_DB=/data/bp.db`, `SECRET_KEY` as a Fly secret.
