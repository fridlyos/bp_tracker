"""HTML page routes. Render Jinja2, POST-redirect-GET.

Auth: the logged-in user id lives in a signed session cookie (SessionMiddleware).
Anonymous visitors are redirected to /login. Admin-only pages check is_admin.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db, service
from .schemas import User

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()


def current_user(request: Request, conn=Depends(db.get_db)) -> User | None:
    uid = request.session.get("uid")
    return service.get_user(conn, uid) if uid else None


def _render(request, name, user, ctx):
    return templates.TemplateResponse(request, name, {"user": user, **ctx})


def _to(path):
    return RedirectResponse(path, status_code=303)


@router.get("/")
def home(request: Request, user: User | None = Depends(current_user)):
    """Home. Anonymous -> /login; admins -> /users-ui (they manage, don't log)."""
    if user is None:
        return _to("/login")
    if user.is_admin:
        return _to("/users-ui")
    return _render(request, "home.html", user, {})


# --- auth (register / login / logout)

@router.get("/register")
def register_form(request: Request, user: User | None = Depends(current_user)):
    if user is not None:
        return _to("/")
    return _render(request, "register.html", None, {"error": None})


@router.post("/register")
def register_submit(request: Request, username: str = Form(...), password: str = Form(...),
                    conn=Depends(db.get_db)):
    """Create an account, then log it in. Duplicate username re-renders with an error."""
    try:
        u = service.register_user(conn, username.strip(), password)
    except ValueError:
        return _render(request, "register.html", None, {"error": "That username is taken."})
    request.session["uid"] = u.id
    return _to("/")


@router.get("/login")
def login_form(request: Request, user: User | None = Depends(current_user)):
    if user is not None:
        return _to("/")
    return _render(request, "login.html", None, {"error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...),
                 conn=Depends(db.get_db)):
    u = service.authenticate(conn, username.strip(), password)
    if u is None:
        return _render(request, "login.html", None, {"error": "Wrong username or password."})
    request.session["uid"] = u.id
    return _to("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return _to("/login")


# --- admin console

@router.get("/users-ui")
def users_page(request: Request, conn=Depends(db.get_db),
               user: User | None = Depends(current_user)):
    if user is None:
        return _to("/login")
    if not user.is_admin:
        return _to("/")
    return _render(request, "users.html", user, {"users": service.list_users(conn)})


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
