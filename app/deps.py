"""Request dependencies shared by core and module routes: who is signed in.

The logged-in user lives in a signed session cookie (SessionMiddleware).
"""
import logging

from fastapi import Depends, HTTPException, Request

from . import clock, db, service
from .schemas import User

logger = logging.getLogger("bp_tracker.deps")


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
    # once per session and zone: fix schedules saved as UTC by the old cookie bug
    zone = clock.browser_zone(request)
    if zone and request.session.get("tz") != zone:
        n = clock.repair_zones(conn, user.id, zone)
        if n:
            logger.info("user %s: moved %d schedule(s) from UTC to %s", user.id, n, zone)
        request.session["tz"] = zone
    return user


def current_user(request: Request, conn=Depends(db.get_db)) -> User | None:
    """HTML routes: None when anonymous (the route redirects to /login)."""
    return session_user(request, conn)


def api_user(request: Request, conn=Depends(db.get_db)) -> int:
    """JSON routes: the acting user's id, or 401."""
    user = session_user(request, conn)
    if user is None:
        raise HTTPException(401, "not logged in")
    return user.id
