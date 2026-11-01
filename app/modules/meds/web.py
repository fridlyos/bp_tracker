"""Medication pages under /meds. Two screens: Today (daily checklist, with a
link to History) and Medications (the list; add/edit open a separate form).
POST-redirect-GET; anonymous -> /login."""
from fastapi import APIRouter, Depends, Form, Request
from pydantic import ValidationError

from ... import clock, db
from ...deps import current_user
from ...schemas import User
from ...ui import redirect, render, safe_next
from . import service
from .schemas import MAX_TIMES, MedIn

router = APIRouter(prefix="/meds")


def _error_text(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        msg = err["msg"].removeprefix("Value error, ")
        msgs.append(msg[:1].upper() + msg[1:])
    return "; ".join(msgs)


@router.get("")
def today(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    now = clock.local_now(request)
    return render(request, "meds/today.html", user, {
        "now": now, "plan": service.today_plan(conn, user.id, now),
        "has_meds": bool(service.list_meds(conn, user.id))})


@router.post("/doses")
def dose_log(request: Request, med_id: int = Form(...), status: str = Form("taken"),
             slot: str = Form(""), conn=Depends(db.get_db),
             user: User | None = Depends(current_user)):
    """Mark a slot taken/skipped, or (no slot) log an as-needed dose."""
    if user is None:
        return redirect("/login")
    if status in ("taken", "skipped"):
        service.log_dose(conn, user.id, med_id, status, clock.local_now(request), slot or None)
    return redirect("/meds")


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
    return render(request, "meds/history.html", user, {
        "days": service.history(conn, user.id, 30, clock.local_today(request))})


# --- manage

def _form_page(request, user, med=None, form=None, error=None, status=200):
    resp = render(request, "meds/form.html", user, {
        "edit": med, "form": form, "error": error, "max_times": MAX_TIMES})
    resp.status_code = status
    return resp


@router.get("/manage")
def manage(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """The medication list (no form here)."""
    if user is None:
        return redirect("/login")
    return render(request, "meds/manage.html", user, {"meds": service.list_meds(conn, user.id)})


@router.get("/manage/new")
def manage_new(request: Request, user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _form_page(request, user)


@router.get("/manage/{med_id}")
def manage_edit(med_id: int, request: Request, conn=Depends(db.get_db),
                user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    med = service.get_med(conn, user.id, med_id)
    if med is None:
        return redirect("/meds/manage")
    return _form_page(request, user, med=med)


def _save(request, conn, user, med_id, name, dose, notes, times):
    form = {"name": name, "dose": dose, "notes": notes, "times": [t for t in times if t]}
    edit = service.get_med(conn, user.id, med_id) if med_id else None
    if med_id and edit is None:
        return redirect("/meds/manage")
    try:
        m = MedIn(name=name, dose=dose, notes=notes, times=times)
    except ValidationError as e:
        return _form_page(request, user, edit, form, _error_text(e), 422)
    service.save_med(conn, user.id, m, clock.user_tz(request).key, med_id)
    return redirect("/meds/manage")


@router.post("/manage")
def manage_create(request: Request, name: str = Form(""), dose: str = Form(""),
                  notes: str = Form(""), times: list[str] = Form([]),
                  conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _save(request, conn, user, None, name, dose, notes, times)


@router.post("/manage/{med_id}")
def manage_update(med_id: int, request: Request, name: str = Form(""), dose: str = Form(""),
                  notes: str = Form(""), times: list[str] = Form([]),
                  conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return _save(request, conn, user, med_id, name, dose, notes, times)


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
