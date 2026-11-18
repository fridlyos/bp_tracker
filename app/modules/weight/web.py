"""Weight pages under /weight: Today (add + summary), Trends, History,
Settings (unit, goal, reminders). POST-redirect-GET; anonymous -> /login."""
from fastapi import APIRouter, Depends, Form, Request
from pydantic import ValidationError

from ... import clock, db, push
from ...deps import current_user
from ...schemas import User
from ...ui import redirect, render, safe_next
from . import service
from .schemas import EntryIn, SettingsIn

router = APIRouter(prefix="/weight")


def _error_text(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        msg = err["msg"].removeprefix("Value error, ")
        if err.get("loc") and err["loc"][0] == "weight" and "number" in msg:
            msg = "enter your weight"
        msgs.append(msg[:1].upper() + msg[1:])
    return "; ".join(msgs)


def _today_ctx(request, conn, user, form=None, error=None):
    today = clock.local_today(request)
    s = service.get_settings(conn, user.id)
    return {"today": today, "entries": service.list_entries(conn, user.id, today, today),
            "sum": service.summary(conn, user.id, today), "unit": s["unit"],
            "now": clock.local_now(request).strftime("%Y-%m-%dT%H:%M"),
            "form": form or {"unit": s["unit"]}, "error": error}


@router.get("")
def today(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return render(request, "weight/today.html", user, _today_ctx(request, conn, user))


@router.post("/entries")
def entry_add(request: Request, weight: str = Form(""), unit: str = Form("lb"), taken_at: str = Form(""),
              note: str = Form(""), conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    form = {"weight": weight, "unit": unit, "taken_at": taken_at, "note": note}
    try:
        e = EntryIn(weight=weight.replace(",", ".").strip() or None, unit=unit, taken_at=taken_at or None,
                    note=note or None)
    except ValidationError as err:
        resp = render(request, "weight/today.html", user, _today_ctx(request, conn, user, form, _error_text(err)))
        resp.status_code = 422
        return resp
    e.taken_at = clock.to_local_naive(e.taken_at, request)
    service.add_entry(conn, user.id, e)
    return redirect("/weight")


@router.post("/entries/{entry_id}/delete")
def entry_delete(entry_id: int, next_: str | None = Form(None, alias="next"), conn=Depends(db.get_db),
                 user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.delete_entry(conn, user.id, entry_id)
    return redirect(safe_next(next_, "/weight"))


@router.get("/trends")
def trends(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Chart page; data from /api/weight/stats."""
    if user is None:
        return redirect("/login")
    return render(request, "weight/trends.html", user, {"unit": service.get_settings(conn, user.id)["unit"]})


@router.get("/history")
def history(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return render(request, "weight/history.html", user, {
        "days": service.entries_by_day(conn, user.id), "unit": service.get_settings(conn, user.id)["unit"]})


def _settings_ctx(request, conn, user, **extra):
    return {"s": service.get_settings(conn, user.id), "reminders": service.list_reminders(conn, user.id),
            "max_reminders": service.MAX_REMINDERS, "tz": clock.user_tz(request).key,
            "devices": push.device_count(conn, user.id), "error": None, "reminder_error": None,
            "saved": request.query_params.get("saved") == "1", **extra}


@router.get("/settings")
def settings(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return render(request, "weight/settings.html", user, _settings_ctx(request, conn, user))


@router.post("/settings")
def settings_save(request: Request, unit: str = Form("lb"), goal: str = Form(""), conn=Depends(db.get_db),
                  user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    try:
        service.save_settings(conn, user.id, SettingsIn(unit=unit, goal=goal.replace(",", ".").strip() or None))
    except ValidationError as err:
        resp = render(request, "weight/settings.html", user, _settings_ctx(
            request, conn, user, error=_error_text(err), s={"unit": unit, "goal": goal}))
        resp.status_code = 422
        return resp
    return redirect("/weight/settings?saved=1")


@router.post("/reminders")
def reminder_add(request: Request, at_time: str = Form(""), conn=Depends(db.get_db),
                 user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    try:
        service.add_reminder(conn, user.id, at_time, clock.user_tz(request).key)
    except ValueError as err:
        resp = render(request, "weight/settings.html", user, _settings_ctx(request, conn, user,
                                                                          reminder_error=str(err)))
        resp.status_code = 422
        return resp
    return redirect("/weight/settings#reminders")


@router.post("/reminders/{reminder_id}/delete")
def reminder_delete(reminder_id: int, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.delete_reminder(conn, user.id, reminder_id)
    return redirect("/weight/settings#reminders")
