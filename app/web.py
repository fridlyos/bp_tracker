"""Core HTML routes: the launcher (/), register/login/logout and the admin
console. Module pages live in app/modules/<key>/web.py.

Anonymous visitors are redirected to /login. Admin-only pages check is_admin.
"""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse

from . import clock, db, service
from .deps import current_user, start_session
from .modules import MODULES
from .schemas import User
from .ui import redirect as _to
from .ui import render as _render

router = APIRouter()


@router.get("/")
def launcher(request: Request, conn=Depends(db.get_db),
             user: User | None = Depends(current_user)):
    """Home screen: one tile per module (plus Users for admin)."""
    if user is None:
        return _to("/login")
    today = clock.local_today(request)
    tiles = [{"module": m, "summary": m.summary(conn, user.id, today) if m.summary else None}
             for m in MODULES]
    return _render(request, "launcher.html", user, {"tiles": tiles})


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
