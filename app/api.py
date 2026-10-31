"""JSON API under /api. Same signed session cookie as the web UI; 401 if absent.

Times are the user's local wall-clock time. A timezone-aware `taken_at` is
converted to the user's zone (browser `tz` cookie); naive is stored as given.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from . import clock, db, service
from .schemas import PurgeIn, Reading, ReadingIn

router = APIRouter(prefix="/api")


def api_user(request: Request, conn=Depends(db.get_db)) -> int:
    uid = request.session.get("uid")
    if not uid or service.get_user(conn, uid) is None:
        raise HTTPException(401, "not logged in")
    return uid


@router.post("/readings", response_model=Reading, status_code=201)
def create_reading(r: ReadingIn, request: Request, uid: int = Depends(api_user),
                   conn=Depends(db.get_db)):
    r.taken_at = clock.to_local_naive(r.taken_at, request)
    return service.add_reading(conn, uid, r)


@router.get("/readings", response_model=list[Reading])
def list_readings(start: date | None = Query(None, alias="from"),
                  end: date | None = Query(None, alias="to"),
                  uid: int = Depends(api_user), conn=Depends(db.get_db)):
    """Readings between ?from= and ?to= (YYYY-MM-DD, inclusive), newest first."""
    return service.list_readings(conn, uid, start, end)


@router.delete("/readings/{reading_id}", status_code=204)
def delete_reading(reading_id: int, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    if not service.delete_reading(conn, uid, reading_id):
        raise HTTPException(404, "reading not found")
    return Response(status_code=204)


@router.post("/readings/purge")
def purge(p: PurgeIn, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    """Delete readings taken before `before`. dry_run=true only counts them."""
    n = service.purge_before(conn, uid, p.before, dry_run=p.dry_run)
    return {"before": p.before, "count": n, "deleted": not p.dry_run}
