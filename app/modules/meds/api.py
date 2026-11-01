"""Medications JSON API under /api/meds (session cookie; 401 if absent)."""
from fastapi import APIRouter, Depends, HTTPException, Request

from ... import clock, db
from ...deps import api_user
from . import service
from .schemas import DoseIn, Med

router = APIRouter(prefix="/api/meds")


@router.get("", response_model=list[Med])
def list_meds(uid: int = Depends(api_user), conn=Depends(db.get_db)):
    return service.list_meds(conn, uid)


@router.get("/today")
def today(request: Request, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    plan = service.today_plan(conn, uid, clock.local_now(request))
    return {"scheduled": [{**{k: v for k, v in s.items() if k != "med"}, "med_id": s["med"].id,
                           "name": s["med"].name, "dose_text": s["med"].dose} for s in plan["scheduled"]],
            "as_needed": [{"med_id": a["med"].id, "name": a["med"].name, "doses": a["doses"]}
                          for a in plan["as_needed"]]}


@router.post("/doses", status_code=201)
def log_dose(d: DoseIn, request: Request, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    if not service.log_dose(conn, uid, d.med_id, d.status, clock.local_now(request), d.slot):
        raise HTTPException(404, "medication not found")
    return {"ok": True}
