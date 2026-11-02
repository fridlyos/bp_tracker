"""Medication pages under /meds. Two screens: Today (daily checklist, with a
link to History) and Medications (the list; add/edit open a separate form).
POST-redirect-GET; anonymous -> /login."""
import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from pydantic import ValidationError

from ... import clock, db
from ...deps import current_user
from ...schemas import User
from ...ui import redirect, render, safe_next
from . import service
from .schemas import KINDS, MAX_TIMES, MedIn

router = APIRouter(prefix="/meds")


def _error_text(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        msg = err["msg"].removeprefix("Value error, ")
        msgs.append(msg[:1].upper() + msg[1:])
    return "; ".join(msgs)


_SLOT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _day(value: str | None, today: date) -> date:
    """?day=YYYY-MM-DD -> that day; missing, invalid or future -> today."""
    try:
        d = date.fromisoformat(value or "")
    except ValueError:
        return today
    return d if d <= today else today


def _day_url(d: date, today: date) -> str:
    return "/meds" if d == today else f"/meds?day={d.isoformat()}"


@router.get("")
def today(request: Request, day: str | None = None, conn=Depends(db.get_db),
          user: User | None = Depends(current_user)):
    """Today's checklist; ?day= opens an earlier day to fill it in."""
    if user is None:
        return redirect("/login")
    now = clock.local_now(request)
    d = _day(day, now.date())
    return render(request, "meds/today.html", user, {
        "now": now, "day": d, "is_today": d == now.date(),
        "prev": (d - timedelta(days=1)).isoformat(),
        "next": (d + timedelta(days=1)).isoformat() if d < now.date() else None,
        "here": _day_url(d, now.date()),
        "plan": service.day_plan(conn, user.id, d, now),
        "has_meds": bool(service.list_meds(conn, user.id))})


@router.post("/doses")
def dose_log(request: Request, med_id: int = Form(...), status: str = Form("taken"),
             slot: str = Form(""), day: str = Form(""), at: str = Form(""),
             conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Mark a slot taken/skipped, or (no slot) log an as-needed dose. On a
    past `day` the dose is logged at the slot time (or `at` HH:MM)."""
    if user is None:
        return redirect("/login")
    now = clock.local_now(request)
    d = _day(day, now.date())
    slot = slot if _SLOT.match(slot) else ""
    if d == now.date():
        logged = now
    elif slot:
        logged = datetime.fromisoformat(slot)
    else:
        logged = datetime.fromisoformat(f"{d.isoformat()}T{at if _HHMM.match(at) else '12:00'}")
    if status in ("taken", "skipped"):
        service.log_dose(conn, user.id, med_id, status, logged, slot or None)
    return redirect(_day_url(d, now.date()))


@router.post("/doses/{dose_id}/undo")
def dose_undo(dose_id: int, next_: str | None = Form(None, alias="next"),
              conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.undo_dose(conn, user.id, dose_id)
    return redirect(safe_next(next_, "/meds"))


@router.get("/history")
def history(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    today = clock.local_today(request)
    return render(request, "meds/history.html", user, {
        "today": today.isoformat(), "days": service.history(conn, user.id, 30, today)})


# --- manage

def _form_page(request, user, med=None, form=None, error=None, status=200, kind="medication"):
    resp = render(request, "meds/form.html", user, {
        "edit": med, "form": form, "error": error, "max_times": MAX_TIMES,
        "kind": (form or {}).get("kind") or (med.kind if med else kind)})
    resp.status_code = status
    return resp


@router.get("/manage")
def manage(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """The medication list (no form here)."""
    if user is None:
        return redirect("/login")
    return render(request, "meds/manage.html", user, {"meds": service.list_meds(conn, user.id)})


@router.get("/manage/new")
def manage_new(request: Request, kind: str = "medication",
               user: User | None = Depends(current_user)):
    """Add form; ?kind=supplement preselects the type."""
    if user is None:
        return redirect("/login")
    return _form_page(request, user, kind=kind if kind in KINDS else "medication")


@router.get("/manage/{med_id}")
def manage_edit(med_id: int, request: Request, conn=Depends(db.get_db),
                user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    med = service.get_med(conn, user.id, med_id)
    if med is None:
        return redirect("/meds/manage")
    return _form_page(request, user, med=med)


def _save(request, conn, user, med_id, name, kind, dose, notes, times):
    form = {"name": name, "kind": kind, "dose": dose, "notes": notes, "times": [t for t in times if t]}
    edit = service.get_med(conn, user.id, med_id) if med_id else None
    if med_id and edit is None:
        return redirect("/meds/manage")
    try:
        m = MedIn(name=name, kind=kind, dose=dose, notes=notes, times=times)
    except ValidationError as e:
        return _form_page(request, user, edit, form, _error_text(e), 422)
    service.save_med(conn, user.id, m, clock.user_tz(request).key, med_id)
    return redirect("/meds/manage")


@router.post("/manage")
def manage_create(request: Request, name: str = Form(""), kind: str = Form("medication"), dose: str = Form(""),
                  notes: str = Form(""), times: list[str] = Form([]),
                  conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _save(request, conn, user, None, name, kind, dose, notes, times)


@router.post("/manage/{med_id}")
def manage_update(med_id: int, request: Request, name: str = Form(""), kind: str = Form("medication"),
                  dose: str = Form(""),
                  notes: str = Form(""), times: list[str] = Form([]),
                  conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _save(request, conn, user, med_id, name, kind, dose, notes, times)


@router.post("/manage/{med_id}/active")
def manage_active(med_id: int, active: int = Form(...), conn=Depends(db.get_db),
                  user: User | None = Depends(current_user)):
    """Pause (no reminders, hidden from Today, history kept) or resume."""
    if user is None:
        return redirect("/login")
    service.set_active(conn, user.id, med_id, bool(active))
    return redirect("/meds/manage")


@router.post("/manage/{med_id}/delete")
def manage_delete(med_id: int, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    service.delete_med(conn, user.id, med_id)
    return redirect("/meds/manage")
