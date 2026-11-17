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
from .schemas import ExerciseIn

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
        "has_exercises": bool(service.list_exercises(conn, user.id, active_only=True))})


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
        "s": detail, "exercises": [e.model_dump() for e in service.list_exercises(conn, user.id, True)]})


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


def _form_page(request, user, ex=None, form=None, error=None, status=200, history=None):
    resp = render(request, "workout/exercise_form.html", user, {
        "edit": ex, "f": form or (ex.model_dump() if ex else ExerciseIn(name="x").model_dump() | {"name": ""}),
        "error": error, "history": history or []})
    resp.status_code = status
    return resp


@router.get("/exercises/new")
def exercise_new(request: Request, user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _form_page(request, user)


@router.get("/exercises/{exercise_id}")
def exercise_edit(exercise_id: int, request: Request, conn=Depends(db.get_db),
                  user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    ex = service.get_exercise(conn, user.id, exercise_id)
    if ex is None:
        return redirect("/workout/exercises")
    return _form_page(request, user, ex, history=service.exercise_history(conn, user.id, exercise_id, 10))


def _save(request, conn, user, exercise_id, form: dict):
    edit = service.get_exercise(conn, user.id, exercise_id) if exercise_id else None
    if exercise_id and edit is None:
        return redirect("/workout/exercises")
    try:
        e = ExerciseIn(**form)
        service.save_exercise(conn, user.id, e, exercise_id)
    except ValidationError as err:
        return _form_page(request, user, edit, form, _error_text(err), 422)
    except service.DuplicateName as err:
        return _form_page(request, user, edit, form, str(err), 422)
    return redirect("/workout/exercises")


def _form(name, category, equipment, track_reps, track_weight, unit, default_rest_sec, notes) -> dict:
    return {"name": name, "category": category, "equipment": equipment, "track_reps": track_reps == "1",
            "track_weight": track_weight == "1", "unit": unit, "default_rest_sec": default_rest_sec or 90,
            "notes": notes}


@router.post("/exercises")
def exercise_create(request: Request, name: str = Form(""), category: str = Form(""),
                    equipment: str = Form(""), track_reps: str = Form(""), track_weight: str = Form(""),
                    unit: str = Form("lb"), default_rest_sec: str = Form("90"), notes: str = Form(""),
                    conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _save(request, conn, user, None, _form(name, category, equipment, track_reps, track_weight, unit,
                                                  default_rest_sec, notes))


@router.post("/exercises/{exercise_id}")
def exercise_update(exercise_id: int, request: Request, name: str = Form(""), category: str = Form(""),
                    equipment: str = Form(""), track_reps: str = Form(""), track_weight: str = Form(""),
                    unit: str = Form("lb"), default_rest_sec: str = Form("90"), notes: str = Form(""),
                    conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _save(request, conn, user, exercise_id, _form(name, category, equipment, track_reps, track_weight,
                                                         unit, default_rest_sec, notes))


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
