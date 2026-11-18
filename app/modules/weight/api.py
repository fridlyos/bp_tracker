"""Weight JSON API under /api/weight (session cookie; 401 if absent)."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ... import clock, db
from ...deps import api_user
from . import service
from .schemas import Entry, EntryIn, SettingsIn

router = APIRouter(prefix="/api/weight")


@router.get("/entries", response_model=list[Entry])
def list_entries(uid: int = Depends(api_user), conn=Depends(db.get_db)):
    return service.list_entries(conn, uid)


@router.post("/entries", response_model=Entry, status_code=201)
def add_entry(e: EntryIn, request: Request, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    e.taken_at = clock.to_local_naive(e.taken_at, request)
    eid = service.add_entry(conn, uid, e)
    return next(x for x in service.list_entries(conn, uid) if x.id == eid)


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: int, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    service.delete_entry(conn, uid, entry_id)
    return Response(status_code=204)


@router.get("/stats")
def stats(request: Request, range: str = Query("month"), uid: int = Depends(api_user), conn=Depends(db.get_db)):
    """Daily averages + 7-day average for a range (week | month | 3m | year | all)."""
    if range not in service.RANGES:
        raise HTTPException(422, "range must be one of " + ", ".join(service.RANGES))
    return service.stats(conn, uid, clock.local_today(request), range)


@router.get("/summary")
def summary(request: Request, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    return service.summary(conn, uid, clock.local_today(request))


@router.get("/settings")
def get_settings(uid: int = Depends(api_user), conn=Depends(db.get_db)):
    return service.get_settings(conn, uid)


@router.put("/settings")
def put_settings(body: SettingsIn, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    service.save_settings(conn, uid, body)
    return service.get_settings(conn, uid)
