"""User-local time.

Readings are stored as naive local datetimes (what the user saw on the cuff).
The server runs in UTC on Fly, so "now" and "today" come from the browser's
IANA timezone, which base.html stores in a `tz` cookie. Unknown/missing -> UTC.
"""
from datetime import date, datetime
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request

_UTC = ZoneInfo("UTC")


def user_tz(request: Request) -> ZoneInfo:
    # base.html percent-encodes it ("America%2FNew_York"); cookies aren't decoded
    name = unquote(request.cookies.get("tz") or "")
    if not name:
        return _UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return _UTC


def browser_zone(request: Request) -> str | None:
    """The browser's IANA zone name if the cookie holds a valid one, else None."""
    name = unquote(request.cookies.get("tz") or "")
    try:
        return ZoneInfo(name).key if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def repair_zones(conn, user_id: int, zone: str) -> int:
    """Until 9d49fbe the server read every 'Area/City' cookie as UTC, so
    reminders and med times saved then carry tz='UTC' and fire hours off.
    Rewrite this user's 'UTC' rows to the zone the browser reports now.
    Rows saved with a real zone are left alone (no surprise moves on travel).
    Returns how many rows changed."""
    if zone == "UTC":
        return 0
    from .modules import MODULES  # late: modules import clock
    changed = 0
    for table in {t for m in MODULES for t in m.tz_tables}:
        changed += conn.execute(f"UPDATE {table} SET tz = ? WHERE user_id = ? AND tz = 'UTC'",
                                (zone, user_id)).rowcount
    if changed:
        conn.commit()
    return changed


def local_now(request: Request) -> datetime:
    return datetime.now(user_tz(request)).replace(tzinfo=None)


def local_today(request: Request) -> date:
    return local_now(request).date()


def to_local_naive(dt: datetime | None, request: Request) -> datetime:
    """None -> now; aware -> converted to the user's zone; naive -> as given."""
    if dt is None:
        return local_now(request)
    if dt.tzinfo is not None:
        return dt.astimezone(user_tz(request)).replace(tzinfo=None)
    return dt
