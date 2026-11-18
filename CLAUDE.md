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
  module (132px, name in aria-label/title), nothing else; admin reaches Users via the account menu),
  register/login/logout, admin `/users-ui` (create/reset/delete
  users, "Switch to this user" = logout + login prefilled), 308 redirects from
  pre-module URLs (`/trends` → `/bp/trends`, ...).
- `app/deps.py` — session user: `current_user` (HTML, None = anonymous ->
  `/login`), `api_user` (JSON, 401). Signed cookie `uid` + `u` (username).
  Once per session/zone it calls `clock.repair_zones`: rows in modules'
  `tz_tables` still saved as 'UTC' (pre-`9d49fbe` cookie bug) get the
  browser's real zone; correctly saved zones are never rewritten.
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
- `app/backup.py` + `/backup` (account menu "Backup & restore") — every user
  downloads all their data as JSON (`/backup/download`, `format:
  my-apps-backup`, `version` 1, no ids) and restores it (`/backup/restore`):
  merge only, never deletes/overwrites; existing rows skipped (meds matched by
  name+kind), invalid rows counted; rows re-validated with `ReadingIn` /
  `MedIn`. Admin also gets `/backup/database` (sqlite backup API snapshot).
  New tables/columns a user owns must be added to export + restore.
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
  local day, up to 30 min late (restarts), and is not sent if a reading for
  it is already logged today: since midnight for the first reminder, else since
  half-way back to the previous reminder (`service.already_logged`).
- `api.py` — JSON under `/api/bp` (readings CRUD + purge, `/api/bp/stats`
  chart data, `/api/bp/reports`).
- `service.py` — readings SQL, stats, reports, reminders.
  Range averages (Trends summary for week/month/3m, each report period) are
  the average of daily averages: each day counts once however many readings
  it has; pulse only over days with a pulse; report min/max stay per reading.
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

Medications module (`app/modules/meds/`, templates in `app/templates/meds/`) —
medications and supplements, kept apart by `meds.kind` (`medication` |
`supplement`, migration `0004_med_kind`) but scheduled and reminded the same:
- Two screens (two tabs): Today `/meds` (checklist of today's slots: Taken /
  Skip / Undo, plus "as needed" meds with Log dose; links to `/meds/history`,
  last 30 days). `/meds?day=YYYY-MM-DD` opens a past day's checklist to fill
  in (big ‹ prev / next › buttons labelled with the date, 48px, plus a
  date picker that jumps on change and "Back to today"; future clamps to today); past doses are logged at the
  slot time, as-needed ones at a chosen time. History has Edit per day + a
  "Fill in day" picker and List `/meds/manage` (Medications and Supplements sections: Edit, Pause =
  hidden + no reminders but history kept, Delete = removes history; "+
  Medication" / "+ Supplement", the latter is `/meds/manage/new?kind=supplement`).
  Today is also split into Medications / Supplements sections. Add/edit is its own form page: `/meds/manage/new`,
  `/meds/manage/{id}`. JSON under `/api/meds` (list, today, doses).
- Tables (`0003_meds`): `meds` (name, dose text, notes, tz, active),
  `med_times` (`HH:MM`, `last_reminded`), `med_doses` (slot
  `scheduled_for` 'YYYY-MM-DDTHH:MM' or NULL for as-needed, status
  taken|skipped, `logged_at`; unique per med+slot, re-marking replaces).
- Job `service.send_reminders`: one push per user per minute listing every
  item due now (title counts kinds: "Time for 1 medication and 1 supplement") (in the med's zone, ≤30 min late, once per day per time),
  skipping slots already marked (e.g. taken early). Tapping opens `/meds`.
- Validation: `schemas.MedIn` (name required ≤80, dose ≤80, notes ≤300,
  ≤6 distinct `HH:MM` times; none = as needed).

Workout module (`app/modules/workout/`, templates in `app/templates/workout/`,
live page script `app/static/workout.js`) — strength training. Plan:
the "workout-module-plan" doc in the HEALTH APP Claude project.
- Tabs: Workout `/workout` (Start / Continue workout, recent), Progress, History
  `/workout/history`, Exercises `/workout/exercises` (user-defined types:
  name, category, equipment, track reps / track weight switches, unit lb|kg,
  default rest seconds; Archive = hidden, history kept; Delete = removes sets).
- Tables (`0005_workout`): `workout_exercises`, `workout_sessions`
  (`ended_at` NULL = in progress; unfinished > 6 h is stale),
  `workout_session_exercises` (`target_rest_sec` copied from the exercise,
  `group_id` reserved for supersets), `workout_sets` (client `uid` UNIQUE,
  `started_at`/`ended_at` local 'YYYY-MM-DDTHH:MM:SS', reps, weight, unit,
  rpe, tempo).
- Derived, never stored (`service._set_dict` / `_summary`, mirrored in
  workout.js `recompute`): duration = time under tension (measured), rest
  before a set = its start − previous set's end in the same session-exercise,
  estimated TUT = reps × tempo seconds, volume = reps × weight.
- Live page `/workout/session/{id}`: rendered client-side from a `|tojson`
  blob. Phases ready → set → rest; timing is Date.now() timestamps kept in
  localStorage (`workout:<id>`), so reloads/backgrounding don't lose time.
  Rest target reached → vibrate + beep (audio unlocked by the Start tap).
  Wake Lock keeps the screen on. Sets go through a localStorage queue
  (`workout:queue`) PUT to `/api/workout/sets` (idempotent upsert by uid;
  rejected/invalid uids are dropped), retried on `online` and page load.
  Tap a set row to edit reps/weight or delete it.
- JSON under `/api/workout`: exercises CRUD + `/exercises/{id}/history`,
  sessions (start = resume if one is in progress, finish = end at last set's
  end), `/sessions/{id}/exercises`, `/session-exercises/{id}`, `PUT /sets`,
  `PATCH|DELETE /sets/{uid}`. Validation: `schemas.SetIn` (end ≥ start, ≤ 1 h,
  tempo like 3-1-1-0, uid `[A-Za-z0-9-]{8,64}`).
- Phase 2 (progress): Progress tab `/workout/progress/{exercise_id}`
  (`/workout/progress` opens the last-trained exercise): all-time record
  tiles + Chart.js charts per session from `/api/workout/exercises/{id}/progress?since=`
  (Strength = top set solid `--s-1` + est. 1RM dashed `--s-2`, one axis;
  Volume bars; Most reps or Longest set for bodyweight / timed), table view.
  Series tokens validated with the dataviz checker on #fff and #1b3149.
  PRs (`service._scan`, mirrored in workout.js `recompute`): kinds by what
  the exercise tracks — weight → heaviest + est. 1RM (Epley, 1–12 reps);
  reps only → most reps; neither → longest set. A set is a PR when it beats
  every earlier set and an earlier session exists; weights compared in the
  exercise's unit (kg↔lb converted). Sets carry `prs`, session exercises
  `best_before` + `pr_kinds` so the live page badges PRs before sync.
  Home: this week's workouts/sets/time + PRs from the last 30 days;
  History: by-week table (`service.weekly`, Mon–Sun). `/api/workout/summary`.
- Phase 3 (`0006_workout_routines`): Routines `/workout/routines` (saved
  workouts: ordered exercises with target sets/reps/weight, rest override,
  "Superset" = linked to the row above; ↑/↓ reorder in JS, up to 20 rows;
  start = new session with those exercises/targets/groups, or resume the one
  in progress; "Save as routine" on a finished workout). Supersets: runs of
  consecutive rows sharing `group_id` = id of the run's first row, always
  rewritten by `service._regroup` from "linked to above" flags; live cards
  have "Superset with above" / "Unlink" (`PATCH /api/workout/session-exercises/{id}`).
  Live dock: target line, RPE (6–10) + tempo under "RPE & tempo" (tempo kept
  per exercise in state, invalid tempo sent as null), auto-advance in
  workout.js `advance()`: superset member → next member with no rest target,
  last member → back to the first (group rest) until every target is done,
  single exercise with its target done → next undone exercise.
  Calories: `workout_settings` (body weight, unit, intensity) at
  `/workout/settings`; estimate = MET × kg × hours (`service.MET`: light 3.5,
  moderate 5.0, vigorous 6.0 — 2024 Compendium resistance-training codes);
  `workout_sessions.calories` overrides it (blank = estimate); `kcal` in
  session detail, history cards, home week tile and weekly table. Not synced
  to the calorie tracker (lose_weight).
- Reminders (`0007_workout_reminders`, `/workout/reminders`): for an exercise
  or a routine (exactly one), on chosen weekdays (`days` = digits, Mon=0) at
  `HH:MM` in the zone the browser had (tz_tables), optional `weekly_goal`
  (days/week, ≤ days picked). Job `service.send_reminders`: fires in a 30-min
  window, at most once per local day (`last_sent`), skipped when already done
  today or the week's goal (Mon–Sun) is met. "Done" = a set of that exercise
  that local day, or a workout started from that routine with a set. Push
  opens `/workout?remind=<id>`; home "Today" lists today's reminders with
  Start (`POST /workout/reminders/{id}/start` = routine, or a workout with the
  exercise added) and Skip (`POST /workout/reminders/{id}/skip`, `undo=1`
  takes it back): a row in `workout_reminder_skips` (`0009`) for the local
  day in the reminder's zone → no push that day, shown "Skipped today", not
  counted toward the goal; hatched dot on the Reminders page.
  Reminders page has the push-device block (`push.js`).
- Exercise form (`web._save`, async, reads the whole form): "Measured by"
  radio `measure` = weight (reps+weight) | reps (bodyweight) | time (neither)
  → `track_reps`/`track_weight`; Time adds an optional target hold (min + sec
  → `target_duration_sec`, `0008_workout_target_duration`, cleared by
  `ExerciseIn` for non-timed). Live set phase counts toward the hold and
  beeps once when reached (`holdTarget`, `st.holdAlerted`). Inline reminder
  on the same form when the exercise has ≤ 1 reminder: "Remind me" ticked =
  create/update it (validated before anything is saved), unticked = delete
  it; with 2+ it links to them instead. `/workout/reminders/new?target=e:<id>`
  preselects the target.
- Backup: `workout_exercises` + nested `workout_sessions` (exercises by name,
  sets by uid; a uid owned by another account is restored as `<uid>-u<user_id>`),
  `workout_routines` (by name, items by exercise name), `workout_settings`
  (restored only into an account without settings), session `calories` /
  `routine` name / per-exercise targets, `workout_reminders` (by target name,
  matched on target + time + days, with their `skipped` days).

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
