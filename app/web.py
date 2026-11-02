"""Core HTML routes: the launcher (/), register/login/logout and the admin
console. Module pages live in app/modules/<key>/web.py.

Anonymous visitors are redirected to /login. Admin-only pages check is_admin.
"""
import json
import os
import tempfile
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from . import backup, db, service
from .deps import current_user, start_session
from .modules import MODULES
from .schemas import User
from .ui import redirect as _to
from .ui import render as _render

router = APIRouter()


@router.get("/")
def launcher(request: Request, user: User | None = Depends(current_user)):
    """Home screen: one icon per module. (Admin's Users page is in the account menu.)"""
    if user is None:
        return _to("/login")
    return _render(request, "launcher.html", user, {"modules": MODULES})


# Pre-modules URLs (bookmarks, home-screen shortcuts) -> their new home.
_MOVED = {"/trends": "/bp/trends", "/history": "/bp/history",
          "/reports": "/bp/reports", "/settings": "/bp/settings"}


def _moved(new: str):
    def handler(request: Request):
        q = request.url.query
        return RedirectResponse(new + (f"?{q}" if q else ""), status_code=308)
    return handler


for _old, _new in _MOVED.items():
    router.add_api_route(_old, _moved(_new), methods=["GET"], include_in_schema=False)


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


# --- backup & restore (every user: own data as JSON; admin: whole DB)

MAX_BACKUP_BYTES = 10 * 1024 * 1024


def _backup_page(request, user, result=None, error=None, status=200):
    resp = _render(request, "backup.html", user, {"result": result, "error": error})
    resp.status_code = status
    return resp


@router.get("/backup")
def backup_page(request: Request, user: User | None = Depends(current_user)):
    if user is None:
        return _to("/login")
    return _backup_page(request, user)


@router.get("/backup/download")
def backup_download(conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """All of the user's data as a JSON file."""
    if user is None:
        return _to("/login")
    name = f"my-apps-backup-{user.username}-{date.today().isoformat()}.json"
    return JSONResponse(backup.export_user(conn, user.id, user.username),
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/backup/restore")
async def backup_restore(request: Request, file: UploadFile = File(...),
                         conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Merge a backup file into the user's data (adds missing rows only)."""
    if user is None:
        return _to("/login")
    raw = await file.read(MAX_BACKUP_BYTES + 1)
    try:
        if len(raw) > MAX_BACKUP_BYTES:
            raise ValueError("That file is too big to be a backup (10 MB max).")
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("This file isn't a My Apps backup.") from None
        result = backup.restore_user(conn, user.id, data)
    except ValueError as e:
        return _backup_page(request, user, error=str(e), status=422)
    return _backup_page(request, user, result=result)


@router.get("/backup/database")
def backup_database(background: BackgroundTasks, conn=Depends(db.get_db),
                    user: User | None = Depends(current_user)):
    """Admin-only: a consistent copy of the whole SQLite database."""
    if user is None or not user.is_admin:
        return _to("/")
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    backup.copy_database(conn, path)
    background.add_task(os.remove, path)
    return FileResponse(path, media_type="application/vnd.sqlite3",
                        filename=f"my-apps-{date.today().isoformat()}.db")
