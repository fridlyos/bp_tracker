"""Calories page under /calories: open the calorie counter app, today's
numbers from My Apps, and the link setting."""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request

from ... import clock, db
from ...deps import current_user
from ...schemas import User
from ...ui import redirect, render
from . import service

router = APIRouter(prefix="/calories")


def _ctx(request, conn, user, **extra):
    url = service.get_url(conn, user.id)
    return {"url": url, "host": urlparse(url).netloc, "is_default": url == service.DEFAULT_URL,
            "default": service.DEFAULT_URL, "t": service.today_numbers(conn, user.id, clock.local_today(request)),
            "error": None, "edit": False, "refused": request.query_params.get("sso") == "refused", **extra}


@router.get("")
def page(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return render(request, "calories/index.html", user, _ctx(request, conn, user))


@router.get("/open")
def open_app(conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """Open the calorie counter signed in as you (a one-time pass to its /sso/callback)."""
    from ... import sso
    if user is None:
        return redirect("/login?next=/calories/open")
    url = service.get_url(conn, user.id)
    callback = url.rstrip("/") + "/sso/callback"
    # a plain link if the app there can't take a pass (older version, other app, plain http)
    if sso.allowed_callback(conn, user.id, callback) is None or not sso.supported(url):
        return redirect(url)
    return sso.redirect_with_token(conn, user, callback, "/")


@router.post("/link")
def link_save(request: Request, url: str = Form(""), reset: str = Form(""), conn=Depends(db.get_db),
              user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    try:
        service.set_url(conn, user.id, None if reset == "1" else url)
    except ValueError as err:
        resp = render(request, "calories/index.html", user, _ctx(request, conn, user, error=str(err), edit=True,
                                                                typed=url))
        resp.status_code = 422
        return resp
    return redirect("/calories")
