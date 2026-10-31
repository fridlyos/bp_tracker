"""HTML page routes. Render Jinja2, POST-redirect-GET.

Auth: the logged-in user id lives in a signed session cookie (SessionMiddleware).
Anonymous visitors are redirected to /login. Admin-only pages check is_admin.
"""
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from . import clock, db, service
from .schemas import ReadingIn, User

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()


def start_session(request: Request, user: User) -> None:
    # store the username too: SQLite reuses a deleted user's id, and a stale
    # cookie must not log in as whoever gets that id next
    request.session.clear()
    request.session.update({"uid": user.id, "u": user.username})


def session_user(request: Request, conn) -> User | None:
    """The signed-in user, or None. A cookie whose uid now belongs to a
    different (or no) user is cleared."""
    uid = request.session.get("uid")
    if not uid:
        return None
    user = service.get_user(conn, uid)
    if user is None or request.session.get("u") != user.username:
        request.session.clear()
        return None
    return user


def current_user(request: Request, conn=Depends(db.get_db)) -> User | None:
    return session_user(request, conn)


def _render(request, name, user, ctx):
    return templates.TemplateResponse(request, name, {"user": user, **ctx})


def _to(path):
    return RedirectResponse(path, status_code=303)


def _safe_next(path: str | None) -> str:
    # only same-site relative paths, never //host or absolute URLs
    return path if path and path.startswith("/") and not path.startswith("//") else "/"


_FIELD_LABELS = {"systolic": "Systolic", "diastolic": "Diastolic", "pulse": "Pulse",
                 "note": "Note", "taken_at": "Time"}


def _error_text(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        msg = err["msg"].removeprefix("Value error, ")
        field = _FIELD_LABELS.get(err["loc"][0], "") if err["loc"] else ""
        msgs.append(f"{field}: {msg}" if field else msg[:1].upper() + msg[1:])
    return "; ".join(msgs)


def _home_ctx(request, conn, user, form=None, error=None):
    today = clock.local_today(request)
    return {
        "today": today,
        "readings": service.list_readings(conn, user.id, today, today),
        "now": clock.local_now(request).strftime("%Y-%m-%dT%H:%M"),
        "form": form or {}, "error": error,
    }


@router.get("/")
def home(request: Request, conn=Depends(db.get_db),
         user: User | None = Depends(current_user)):
    """Quick-add form + today's readings. Anonymous -> /login. Admins log
    readings like anyone else; they just also get the Users page."""
    if user is None:
        return _to("/login")
    return _render(request, "home.html", user, _home_ctx(request, conn, user))


# --- readings

@router.post("/readings")
def reading_add(request: Request, taken_at: str = Form(""), systolic: str = Form(""),
                diastolic: str = Form(""), pulse: str = Form(""), note: str = Form(""),
                conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Add a reading from the home form. Invalid input re-renders home with the error."""
    if user is None:
        return _to("/login")
    form = {"taken_at": taken_at, "systolic": systolic, "diastolic": diastolic,
            "pulse": pulse, "note": note}
    try:
        r = ReadingIn(taken_at=taken_at or None, systolic=systolic, diastolic=diastolic,
                      pulse=pulse or None, note=note or None)
    except ValidationError as e:
        resp = _render(request, "home.html", user,
                       _home_ctx(request, conn, user, form, _error_text(e)))
        resp.status_code = 422
        return resp
    r.taken_at = clock.to_local_naive(r.taken_at, request)
    service.add_reading(conn, user.id, r)
    return _to("/")


@router.post("/readings/{reading_id}/delete")
def reading_delete(reading_id: int, next_: str | None = Form(None, alias="next"),
                   conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return _to("/login")
    service.delete_reading(conn, user.id, reading_id)
    return _to(_safe_next(next_))


@router.get("/history")
def history(request: Request, conn=Depends(db.get_db),
            user: User | None = Depends(current_user)):
    """All readings grouped by day, newest first."""
    if user is None:
        return _to("/login")
    return _render(request, "history.html", user,
                   {"days": service.readings_by_day(conn, user.id)})


@router.get("/trends")
def trends(request: Request, user: User | None = Depends(current_user)):
    """Charts page. Data comes from /api/stats; the page is a static shell."""
    if user is None:
        return _to("/login")
    return _render(request, "trends.html", user, {})


# --- settings (purge old data)

@router.get("/settings")
def settings(request: Request, before: date | None = Query(None),
             purged: int | None = Query(None),
             conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Purge form. With ?before= shows how many readings would go (preview)."""
    if user is None:
        return _to("/login")
    count = service.purge_before(conn, user.id, before, dry_run=True) if before else None
    return _render(request, "settings.html", user,
                   {"before": before, "count": count, "purged": purged})


@router.post("/settings/purge")
def settings_purge(before: date = Form(...), conn=Depends(db.get_db),
                   user: User | None = Depends(current_user)):
    if user is None:
        return _to("/login")
    n = service.purge_before(conn, user.id, before)
    return _to(f"/settings?purged={n}")


# --- auth (register / login / logout)

@router.get("/register")
def register_form(request: Request, user: User | None = Depends(current_user)):
    """Signup form. A logged-in visitor (e.g. admin) is told so and offered
    Log out, instead of being bounced back to their home page with no hint."""
    return _render(request, "register.html", user, {"error": None})


@router.post("/register")
def register_submit(request: Request, username: str = Form(...), password: str = Form(...),
                    conn=Depends(db.get_db)):
    """Create an account, then log it in. Duplicate username re-renders with an error."""
    try:
        u = service.register_user(conn, username.strip(), password)
    except ValueError:
        return _render(request, "register.html", None, {"error": "That username is taken."})
    start_session(request, u)
    return _to("/")


@router.get("/login")
def login_form(request: Request, username: str = Query(""),
               user: User | None = Depends(current_user)):
    """Login form; ?username= prefills the field (used by admin's "Switch to")."""
    if user is not None:
        return _to("/")
    return _render(request, "login.html", None, {"error": None, "username": username})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...),
                 conn=Depends(db.get_db)):
    u = service.authenticate(conn, username.strip(), password)
    if u is None:
        # Personal app with a handful of accounts: saying which part was wrong
        # is worth more than hiding whether a username exists. Both messages
        # echo the normalized name so a typo ("fridlyos" vs "fridyos") is visible.
        name = service.normalize_username(username)
        error = (f"Wrong password for “{name}”." if service.username_exists(conn, name)
                 else f"No account called “{name}”. Check the spelling, or register.")
        return _render(request, "login.html", None, {"error": error, "username": name})
    start_session(request, u)
    return _to("/")


@router.post("/logout")
def logout(request: Request, switch_to: str = Form("")):
    """Log out. With switch_to, land on login with that username prefilled."""
    request.session.clear()
    if switch_to:
        return _to(f"/login?{urlencode({'username': switch_to})}")
    return _to("/login")


# --- admin console

@router.get("/users-ui")
def users_page(request: Request, created: str | None = Query(None),
               conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Admin console. ?created=<username> shows a "switch to this user" notice."""
    if user is None:
        return _to("/login")
    if not user.is_admin:
        return _to("/")
    return _render(request, "users.html", user,
                   {"users": service.list_users(conn), "created": created})


@router.post("/users/new")
def users_create(request: Request, username: str = Form(...), password: str = Form(...),
                 conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Admin-only: create a regular account. The admin stays logged in."""
    if user is None or not user.is_admin:
        return _to("/login")
    try:
        new = service.register_user(conn, username.strip(), password)
    except ValueError:
        return _render(request, "users.html", user, {
            "users": service.list_users(conn), "create_error": "That username is taken.",
            "create_username": username})
    return _to(f"/users-ui?{urlencode({'created': new.username})}")


@router.post("/users/{user_id}/delete")
def users_delete(user_id: int, conn=Depends(db.get_db),
                 user: User | None = Depends(current_user)):
    if user is None or not user.is_admin:
        return _to("/login")
    service.delete_user(conn, user_id)
    return _to("/users-ui")


@router.post("/users/{user_id}/reset")
def users_reset(user_id: int, password: str = Form(...), conn=Depends(db.get_db),
                user: User | None = Depends(current_user)):
    """Admin-only: set a new password for any user (including the admin itself)."""
    if user is None or not user.is_admin:
        return _to("/login")
    service.set_password(conn, user_id, password)
    return _to("/users-ui")
