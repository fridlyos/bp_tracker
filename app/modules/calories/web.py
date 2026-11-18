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
            "error": None, "edit": False, **extra}


@router.get("")
def page(request: Request, conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    if user is None:
        return redirect("/login")
    return render(request, "calories/index.html", user, _ctx(request, conn, user))


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
