"""Workout pages under /workout: Workout (start/continue + recent), the live
session page, History, and Exercises (the user's exercise types).
POST-redirect-GET; anonymous -> /login. The live session page is driven by
JS against /api/workout (timers, offline queue)."""
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from pydantic import ValidationError

from ... import clock, db
from ...deps import current_user
from ...schemas import User
from ...ui import redirect, render
from . import service
from .schemas import DAY_NAMES, MAX_ROUTINE_ITEMS, CaloriesIn, ExerciseIn, ReminderIn, RoutineIn, SettingsIn

router = APIRouter(prefix="/workout")


def _error_text(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        msg = err["msg"].removeprefix("Value error, ")
        msgs.append(msg[:1].upper() + msg[1:])
    return "; ".join(msgs)


@router.get("")
def home(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    now = clock.local_now(request)
    active = service.active_session(conn, user.id, now)
    return render(request, "workout/home.html", user, {
        "week": service.weekly(conn, user.id, now.date(), 1)[0],
        "prs": service.recent_prs(conn, user.id, (now.date() - timedelta(days=30)).isoformat(), 5),
        "active": service.session_detail(conn, user.id, active["id"]) if active else None,
        "recent": [s for s in service.recent_sessions(conn, user.id, 6) if not s["in_progress"]][:5],
        "has_exercises": bool(service.list_exercises(conn, user.id, active_only=True)),
        "routines": service.list_routines(conn, user.id),
        "settings": service.get_settings(conn, user.id),
        "due": [r for r in (service.reminder_status(conn, x, now.date())
                            for x in service.list_reminders(conn, user.id) if x["active"])
                if r["today"] or r["done_today"]],
        "remind": request.query_params.get("remind", "")})


@router.post("/start")
def start(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    sid = service.start_session(conn, user.id, clock.local_now(request))
    return redirect(f"/workout/session/{sid}")


@router.get("/session/{session_id}")
def session_page(session_id: int, request: Request, conn=Depends(db.get_db),
                 user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    detail = service.session_detail(conn, user.id, session_id)
    if detail is None:
        return redirect("/workout")
    return render(request, "workout/session.html", user, {
        "s": detail, "exercises": [e.model_dump() for e in service.list_exercises(conn, user.id, True)],
        "error": {"name": "You already have a routine with that name.",
                  "kcal": "Calories must be a whole number from 0 to 5000."}.get(
                      request.query_params.get("error", ""))})


@router.post("/session/{session_id}/finish")
def finish(session_id: int, request: Request, notes: str = Form(""), conn=Depends(db.get_db),
           user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.finish_session(conn, user.id, session_id, clock.local_now(request), notes.strip()[:300] or None)
    return redirect(f"/workout/session/{session_id}")


@router.post("/session/{session_id}/delete")
def delete(session_id: int, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.delete_session(conn, user.id, session_id)
    return redirect("/workout/history")


@router.get("/history")
def history(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return render(request, "workout/history.html", user, {
        "sessions": service.recent_sessions(conn, user.id, 60),
        "weeks": service.weekly(conn, user.id, clock.local_today(request))})


@router.get("/progress")
def progress_start(conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Progress opens on the exercise you trained most recently."""
    if user is None:
        return redirect("/login")
    row = conn.execute(
        "SELECT se.exercise_id FROM workout_sets x JOIN workout_session_exercises se ON se.id = x.session_exercise_id"
        " WHERE x.user_id = ? ORDER BY x.started_at DESC LIMIT 1", (user.id,)).fetchone()
    if row is None:
        exs = service.list_exercises(conn, user.id, active_only=True)
        if not exs:
            return redirect("/workout/exercises")
        return redirect(f"/workout/progress/{exs[0].id}")
    return redirect(f"/workout/progress/{row[0]}")


@router.get("/progress/{exercise_id}")
def progress(exercise_id: int, request: Request, conn=Depends(db.get_db),
             user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    data = service.exercise_progress(conn, user.id, exercise_id)
    if data is None:
        return redirect("/workout/progress")
    return render(request, "workout/progress.html", user, {
        "ex": data["exercise"], "records": data["records"], "kinds": data["kinds"],
        "labels": service.PR_LABELS, "has_data": bool(data["points"]),
        "exercises": service.list_exercises(conn, user.id)})


# --- exercise types

@router.get("/exercises")
def exercises(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return render(request, "workout/exercises.html", user, {"exercises": service.list_exercises(conn, user.id)})


def _form_page(request, user, ex=None, form=None, error=None, status=200, history=None, conn=None):
    from ... import push
    reminders = _exercise_reminders(conn, user.id, ex.id if ex else None) if conn else []
    f = form or (ex.model_dump() if ex else ExerciseIn(name="x").model_dump() | {"name": ""})
    if form is None:  # the reminder part of the form, from the saved reminder (if one)
        r = reminders[0] if len(reminders) == 1 else None
        f = f | {"remind": bool(r), "days": r["days"] if r else [0, 1, 2, 3, 4],
                 "at_time": r["at_time"] if r else "18:00", "weekly_goal": r["weekly_goal"] if r else None}
    hold = f.get("target_duration_sec")
    resp = render(request, "workout/exercise_form.html", user, {
        "edit": ex, "f": f, "error": error, "history": history or [], "reminders": reminders,
        "measure": "weight" if f.get("track_weight") else "reps" if f.get("track_reps") else "time",
        "hold_min": hold // 60 if hold else "", "hold_sec": hold % 60 if hold else "",
        "day_names": DAY_NAMES, "devices": push.device_count(conn, user.id) if conn else 0})
    resp.status_code = status
    return resp


@router.get("/exercises/new")
def exercise_new(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _form_page(request, user, conn=conn)


@router.get("/exercises/{exercise_id}")
def exercise_edit(exercise_id: int, request: Request, conn=Depends(db.get_db),
                  user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    ex = service.get_exercise(conn, user.id, exercise_id)
    if ex is None:
        return redirect("/workout/exercises")
    return _form_page(request, user, ex, history=service.exercise_history(conn, user.id, exercise_id, 10),
                      conn=conn)


MEASURES = {"weight": (True, True), "reps": (True, False), "time": (False, False)}  # -> (reps, weight)


def _exercise_reminders(conn, user_id: int, exercise_id: int | None) -> list[dict]:
    return [r for r in service.list_reminders(conn, user_id) if r["exercise_id"] == exercise_id] \
        if exercise_id else []


async def _save(request, conn, user, exercise_id):
    """Save the exercise and, from the same form, its reminder: 'remind'
    ticked = create/update it, unticked = remove it. Only when the exercise
    has at most one reminder (more are managed on the Reminders page)."""
    edit = service.get_exercise(conn, user.id, exercise_id) if exercise_id else None
    if exercise_id and edit is None:
        return redirect("/workout/exercises")
    f = await request.form()
    reps, weight = MEASURES.get(f.get("measure", ""), (True, True))
    mins, secs = _num(f.get("hold_min", "")), _num(f.get("hold_sec", ""))
    hold = None
    if isinstance(mins, int) or isinstance(secs, int):
        hold = (mins if isinstance(mins, int) else 0) * 60 + (secs if isinstance(secs, int) else 0)
    form = {"name": f.get("name", ""), "category": f.get("category", ""), "equipment": f.get("equipment", ""),
            "track_reps": reps, "track_weight": weight, "unit": f.get("unit", "lb"),
            "default_rest_sec": f.get("default_rest_sec") or 90, "notes": f.get("notes", ""),
            "target_duration_sec": hold}
    existing = _exercise_reminders(conn, user.id, exercise_id)
    inline = len(existing) <= 1
    want = inline and f.get("remind") == "1"
    days = f.getlist("days")
    rem_form = {"days": [int(d) for d in days if d.isdigit()], "at_time": f.get("at_time", ""),
                "weekly_goal": _num(f.get("weekly_goal", ""))}

    def again(msg):
        return _form_page(request, user, edit, form | {"remind": want, **rem_form}, msg, 422, conn=conn)

    try:
        e = ExerciseIn(**form)
        rem = ReminderIn(exercise_id=0, days=days, at_time=rem_form["at_time"],
                         weekly_goal=rem_form["weekly_goal"]) if want else None
    except ValidationError as err:
        return again(_error_text(err))
    try:
        saved = service.save_exercise(conn, user.id, e, exercise_id)
    except service.DuplicateName as err:
        return again(str(err))
    if inline and saved:
        if rem is not None:
            rem.exercise_id = saved.id
            service.save_reminder(conn, user.id, rem, clock.user_tz(request).key,
                                  existing[0]["id"] if existing else None)
        elif existing:
            service.delete_reminder(conn, user.id, existing[0]["id"])
    return redirect("/workout/exercises")


@router.post("/exercises")
async def exercise_create(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return await _save(request, conn, user, None)


@router.post("/exercises/{exercise_id}")
async def exercise_update(exercise_id: int, request: Request, conn=Depends(db.get_db),
                          user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return await _save(request, conn, user, exercise_id)


@router.post("/exercises/{exercise_id}/active")
def exercise_active(exercise_id: int, active: int = Form(...), conn=Depends(db.get_db),
                    user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.set_exercise_active(conn, user.id, exercise_id, bool(active))
    return redirect("/workout/exercises")


@router.post("/exercises/{exercise_id}/delete")
def exercise_delete(exercise_id: int, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.delete_exercise(conn, user.id, exercise_id)
    return redirect("/workout/exercises")


# --- phase 3: routines, calories, settings

def _num(v: str, cast=int):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return cast(v)
    except ValueError:
        return v  # let pydantic report it


def _routine_form(conn, user, request, routine=None, form=None, error=None, status=200):
    resp = render(request, "workout/routine_form.html", user, {
        "edit": routine, "f": form or routine or {"name": "", "notes": "", "items": []},
        "exercises": service.list_exercises(conn, user.id, active_only=True),
        "error": error, "rows": MAX_ROUTINE_ITEMS})
    resp.status_code = status
    return resp


@router.get("/routines")
def routines(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return render(request, "workout/routines.html", user, {"routines": service.list_routines(conn, user.id)})


@router.get("/routines/new")
def routine_new(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _routine_form(conn, user, request)


@router.get("/routines/{routine_id}")
def routine_edit(routine_id: int, request: Request, conn=Depends(db.get_db),
                 user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    rt = service.get_routine(conn, user.id, routine_id)
    if rt is None:
        return redirect("/workout/routines")
    return _routine_form(conn, user, request, rt)


async def _routine_save(request: Request, conn, user, routine_id: int | None):
    edit = service.get_routine(conn, user.id, routine_id) if routine_id else None
    if routine_id and edit is None:
        return redirect("/workout/routines")
    f = await request.form()
    ex_ids = f.getlist("item_exercise")
    col = lambda name: f.getlist(name) + [""] * (len(ex_ids) - len(f.getlist(name)))  # noqa: E731
    sets, reps, weight, rest = col("item_sets"), col("item_reps"), col("item_weight"), col("item_rest")
    linked = set(f.getlist("item_superset"))
    items = []
    for i, ex in enumerate(ex_ids):
        if not ex:
            continue
        items.append({"exercise_id": _num(ex), "target_sets": _num(sets[i]), "target_reps": _num(reps[i]),
                      "target_weight": _num(weight[i], float), "rest_sec": _num(rest[i]),
                      "superset_with_previous": str(i) in linked and bool(items)})
    form = {"name": f.get("name", ""), "notes": f.get("notes", ""), "items": items}
    try:
        rt = RoutineIn(**form)
        rid = service.save_routine(conn, user.id, rt, routine_id)
    except ValidationError as err:
        return _routine_form(conn, user, request, edit, form, _error_text(err), 422)
    except (service.DuplicateName, ValueError) as err:
        return _routine_form(conn, user, request, edit, form, str(err), 422)
    return redirect("/workout/routines" if rid else "/workout/routines")


@router.post("/routines")
async def routine_create(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return await _routine_save(request, conn, user, None)


@router.post("/routines/{routine_id}")
async def routine_update(routine_id: int, request: Request, conn=Depends(db.get_db),
                         user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return await _routine_save(request, conn, user, routine_id)


@router.post("/routines/{routine_id}/delete")
def routine_delete(routine_id: int, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.delete_routine(conn, user.id, routine_id)
    return redirect("/workout/routines")


@router.post("/routines/{routine_id}/start")
def routine_start(routine_id: int, request: Request, conn=Depends(db.get_db),
                  user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    sid = service.start_routine(conn, user.id, routine_id, clock.local_now(request))
    return redirect(f"/workout/session/{sid}" if sid else "/workout/routines")


@router.post("/session/{session_id}/save-routine")
def save_as_routine(session_id: int, request: Request, name: str = Form(""), conn=Depends(db.get_db),
                    user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    name = name.strip()[:60] or "Workout " + clock.local_today(request).isoformat()
    try:
        rid = service.routine_from_session(conn, user.id, session_id, name)
    except service.DuplicateName:
        return redirect(f"/workout/session/{session_id}?error=name")
    return redirect(f"/workout/routines/{rid}" if rid else f"/workout/session/{session_id}")


@router.post("/session/{session_id}/calories")
def session_calories(session_id: int, calories: str = Form(""), conn=Depends(db.get_db),
                     user: User | None = Depends(current_user)):
    """Blank = go back to the estimate."""
    if user is None:
        return redirect("/login")
    try:
        kcal = CaloriesIn(calories=_num(calories)).calories
    except ValidationError:
        return redirect(f"/workout/session/{session_id}?error=kcal")
    service.set_calories(conn, user.id, session_id, kcal)
    return redirect(f"/workout/session/{session_id}")


@router.get("/settings")
def settings_page(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return render(request, "workout/settings.html", user, {
        "s": service.get_settings(conn, user.id), "met": service.MET, "error": None})


@router.post("/settings")
def settings_save(request: Request, body_weight: str = Form(""), unit: str = Form("lb"),
                  intensity: str = Form("moderate"), conn=Depends(db.get_db),
                  user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    form = {"body_weight": _num(body_weight, float), "unit": unit, "intensity": intensity}
    try:
        service.save_settings(conn, user.id, SettingsIn(**form))
    except ValidationError as err:
        resp = render(request, "workout/settings.html", user, {
            "s": form, "met": service.MET, "error": _error_text(err)})
        resp.status_code = 422
        return resp
    return redirect("/workout")


# --- reminders

def _target(v: str) -> dict:
    kind, _, ident = (v or "").partition(":")
    ident = _num(ident)
    return {"exercise_id": ident if kind == "e" else None, "routine_id": ident if kind == "r" else None}


def _reminder_form(conn, user, request, rem=None, form=None, error=None, status=200):
    resp = render(request, "workout/reminder_form.html", user, {
        "edit": rem, "f": form or rem or {"target": "", "days": [0, 1, 2, 3, 4], "at_time": "18:00",
                                          "weekly_goal": None},
        "exercises": service.list_exercises(conn, user.id, active_only=True),
        "routines": service.list_routines(conn, user.id), "day_names": DAY_NAMES, "error": error})
    resp.status_code = status
    return resp


@router.get("/reminders")
def reminders(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    from ... import push
    today = clock.local_today(request)
    return render(request, "workout/reminders.html", user, {
        "reminders": [service.reminder_status(conn, r, today) for r in service.list_reminders(conn, user.id)],
        "day_names": DAY_NAMES, "devices": push.device_count(conn, user.id)})


@router.get("/reminders/new")
def reminder_new(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    target = request.query_params.get("target", "")
    return _reminder_form(conn, user, request, form={"target": target, "days": [0, 1, 2, 3, 4],
                                                     "at_time": "18:00", "weekly_goal": None} if target else None)


@router.get("/reminders/{reminder_id}")
def reminder_edit(reminder_id: int, request: Request, conn=Depends(db.get_db),
                  user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    rem = service.get_reminder(conn, user.id, reminder_id)
    if rem is None:
        return redirect("/workout/reminders")
    return _reminder_form(conn, user, request, rem)


def _reminder_save(request, conn, user, reminder_id, target, days, at_time, weekly_goal):
    edit = service.get_reminder(conn, user.id, reminder_id) if reminder_id else None
    if reminder_id and edit is None:
        return redirect("/workout/reminders")
    form = {"target": target, "days": [int(d) for d in days if d.isdigit()], "at_time": at_time,
            "weekly_goal": _num(weekly_goal)}
    try:
        m = ReminderIn(**_target(target), days=days, at_time=at_time, weekly_goal=_num(weekly_goal))
        service.save_reminder(conn, user.id, m, clock.user_tz(request).key, reminder_id)
    except ValidationError as err:
        return _reminder_form(conn, user, request, edit, form, _error_text(err), 422)
    except ValueError as err:
        return _reminder_form(conn, user, request, edit, form, str(err).capitalize(), 422)
    return redirect("/workout/reminders")


@router.post("/reminders")
def reminder_create(request: Request, target: str = Form(""), days: list[str] = Form([]),
                    at_time: str = Form(""), weekly_goal: str = Form(""), conn=Depends(db.get_db),
                    user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _reminder_save(request, conn, user, None, target, days, at_time, weekly_goal)


@router.post("/reminders/{reminder_id}")
def reminder_update(reminder_id: int, request: Request, target: str = Form(""), days: list[str] = Form([]),
                    at_time: str = Form(""), weekly_goal: str = Form(""), conn=Depends(db.get_db),
                    user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _reminder_save(request, conn, user, reminder_id, target, days, at_time, weekly_goal)


@router.post("/reminders/{reminder_id}/active")
def reminder_active(reminder_id: int, active: int = Form(...), conn=Depends(db.get_db),
                    user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.set_reminder_active(conn, user.id, reminder_id, bool(active))
    return redirect("/workout/reminders")


@router.post("/reminders/{reminder_id}/delete")
def reminder_delete(reminder_id: int, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.delete_reminder(conn, user.id, reminder_id)
    return redirect("/workout/reminders")


@router.post("/reminders/{reminder_id}/start")
def reminder_start(reminder_id: int, request: Request, conn=Depends(db.get_db),
                   user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    sid = service.start_reminder(conn, user.id, reminder_id, clock.local_now(request))
    return redirect(f"/workout/session/{sid}" if sid else "/workout")
