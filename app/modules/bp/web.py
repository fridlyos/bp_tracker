"""Blood pressure pages under /bp. Render Jinja2, POST-redirect-GET.
Anonymous visitors are redirected to /login."""
from datetime import date

from fastapi import APIRouter, Depends, Form, Query, Request
from pydantic import ValidationError

from ... import clock, db, push
from ...deps import current_user
from ...schemas import User
from ...ui import redirect, render, safe_next
from . import service
from .schemas import ReadingIn

router = APIRouter(prefix="/bp")

_FIELD_LABELS = {"systolic": "Systolic", "diastolic": "Diastolic", "pulse": "Pulse",
                 "note": "Note", "taken_at": "Time"}


def _error_text(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        msg = err["msg"].removeprefix("Value error, ")
        field = _FIELD_LABELS.get(err["loc"][0], "") if err["loc"] else ""
        msgs.append(f"{field}: {msg}" if field else msg[:1].upper() + msg[1:])
    return "; ".join(msgs)


def _today_ctx(request, conn, user, form=None, error=None):
    today = clock.local_today(request)
    return {
        "today": today,
        "readings": service.list_readings(conn, user.id, today, today),
        "now": clock.local_now(request).strftime("%Y-%m-%dT%H:%M"),
        "form": form or {}, "error": error,
    }


@router.get("")
def today(request: Request, conn=Depends(db.get_db),
          user: User | None = Depends(current_user)):
    """Quick-add form + today's readings."""
    if user is None:
        return redirect("/login")
    return render(request, "bp/today.html", user, _today_ctx(request, conn, user))


@router.post("/readings")
def reading_add(request: Request, taken_at: str = Form(""), systolic: str = Form(""),
                diastolic: str = Form(""), pulse: str = Form(""), note: str = Form(""),
                conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Add a reading from the Today form. Invalid input re-renders it with the error."""
    if user is None:
        return redirect("/login")
    form = {"taken_at": taken_at, "systolic": systolic, "diastolic": diastolic,
            "pulse": pulse, "note": note}
    try:
        r = ReadingIn(taken_at=taken_at or None, systolic=systolic, diastolic=diastolic,
                      pulse=pulse or None, note=note or None)
    except ValidationError as e:
        resp = render(request, "bp/today.html", user,
                      _today_ctx(request, conn, user, form, _error_text(e)))
        resp.status_code = 422
        return resp
    r.taken_at = clock.to_local_naive(r.taken_at, request)
    service.add_reading(conn, user.id, r)
    return redirect("/bp")


@router.post("/readings/{reading_id}/delete")
def reading_delete(reading_id: int, next_: str | None = Form(None, alias="next"),
                   conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.delete_reading(conn, user.id, reading_id)
    return redirect(safe_next(next_, "/bp"))


@router.get("/history")
def history(request: Request, conn=Depends(db.get_db),
            user: User | None = Depends(current_user)):
    """All readings grouped by day, newest first."""
    if user is None:
        return redirect("/login")
    return render(request, "bp/history.html", user,
                  {"days": service.readings_by_day(conn, user.id)})


@router.get("/trends")
def trends(request: Request, user: User | None = Depends(current_user)):
    """Charts page. Data comes from /api/bp/stats; the page is a static shell."""
    if user is None:
        return redirect("/login")
    return render(request, "bp/trends.html", user, {})


@router.get("/reports")
def reports(request: Request, period: str = Query("week"), conn=Depends(db.get_db),
            user: User | None = Depends(current_user)):
    """Weekly or monthly summaries (?period=week|month), newest first."""
    if user is None:
        return redirect("/login")
    if period not in service.REPORT_PERIODS:
        period = "week"
    return render(request, "bp/reports.html", user,
                  {"period": period, "reports": service.reports(conn, user.id, period)})


def _settings_ctx(request, conn, user, **extra):
    return {"reminders": service.list_reminders(conn, user.id),
            "max_reminders": service.MAX_REMINDERS,
            "tz": clock.user_tz(request).key, "devices": push.device_count(conn, user.id),
            "before": None, "count": None, "purged": None, "reminder_error": None, **extra}


@router.get("/settings")
def settings(request: Request, before: date | None = Query(None),
             purged: int | None = Query(None),
             conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Reminders + purge form. With ?before= shows how many readings would go."""
    if user is None:
        return redirect("/login")
    count = service.purge_before(conn, user.id, before, dry_run=True) if before else None
    return render(request, "bp/settings.html", user,
                  _settings_ctx(request, conn, user, before=before, count=count, purged=purged))


@router.post("/reminders")
def reminder_add(request: Request, at_time: str = Form(""), conn=Depends(db.get_db),
                 user: User | None = Depends(current_user)):
    """Add a daily reminder at HH:MM in the browser's current time zone."""
    if user is None:
        return redirect("/login")
    try:
        service.add_reminder(conn, user.id, at_time, clock.user_tz(request).key)
    except ValueError as e:
        resp = render(request, "bp/settings.html", user,
                      _settings_ctx(request, conn, user, reminder_error=str(e)))
        resp.status_code = 422
        return resp
    return redirect("/bp/settings#reminders")


@router.post("/reminders/{reminder_id}/delete")
def reminder_delete(reminder_id: int, conn=Depends(db.get_db),
                    user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.delete_reminder(conn, user.id, reminder_id)
    return redirect("/bp/settings#reminders")


@router.post("/settings/purge")
def settings_purge(before: date = Form(...), conn=Depends(db.get_db),
                   user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    n = service.purge_before(conn, user.id, before)
    return redirect(f"/bp/settings?purged={n}")
