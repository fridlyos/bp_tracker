"""Workout JSON API under /api/workout (session cookie; 401 if absent).
The live session page talks to this; sets are upserted by client uid so the
offline queue can resend safely."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ... import clock, db
from ...deps import api_user
from . import service
from .schemas import Exercise, ExerciseIn, SessionExerciseIn, SetIn, SetUpdate

router = APIRouter(prefix="/api/workout")


# --- exercise types

@router.get("/exercises", response_model=list[Exercise])
def list_exercises(uid: int = Depends(api_user), conn=Depends(db.get_db)):
    return service.list_exercises(conn, uid)


@router.post("/exercises", response_model=Exercise, status_code=201)
def create_exercise(e: ExerciseIn, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    try:
        return service.save_exercise(conn, uid, e)
    except service.DuplicateName as err:
        raise HTTPException(409, str(err))


@router.put("/exercises/{exercise_id}", response_model=Exercise)
def update_exercise(exercise_id: int, e: ExerciseIn, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    try:
        out = service.save_exercise(conn, uid, e, exercise_id)
    except service.DuplicateName as err:
        raise HTTPException(409, str(err))
    if out is None:
        raise HTTPException(404, "exercise not found")
    return out


@router.delete("/exercises/{exercise_id}", status_code=204)
def delete_exercise(exercise_id: int, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    service.delete_exercise(conn, uid, exercise_id)
    return Response(status_code=204)


@router.get("/exercises/{exercise_id}/history")
def exercise_history(exercise_id: int, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    if service.get_exercise(conn, uid, exercise_id) is None:
        raise HTTPException(404, "exercise not found")
    return service.exercise_history(conn, uid, exercise_id)


# --- sessions

@router.get("/sessions")
def list_sessions(uid: int = Depends(api_user), conn=Depends(db.get_db)):
    return service.recent_sessions(conn, uid)


@router.post("/sessions", status_code=201)
def start_session(request: Request, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    """Start a workout (or get the one in progress)."""
    sid = service.start_session(conn, uid, clock.local_now(request))
    return service.session_detail(conn, uid, sid)


@router.get("/sessions/{session_id}")
def get_session(session_id: int, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    d = service.session_detail(conn, uid, session_id)
    if d is None:
        raise HTTPException(404, "session not found")
    return d


@router.post("/sessions/{session_id}/finish")
def finish_session(session_id: int, request: Request, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    service.finish_session(conn, uid, session_id, clock.local_now(request))
    return get_session(session_id, uid, conn)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    service.delete_session(conn, uid, session_id)
    return Response(status_code=204)


@router.post("/sessions/{session_id}/exercises", status_code=201)
def add_exercise(session_id: int, body: SessionExerciseIn, uid: int = Depends(api_user),
                 conn=Depends(db.get_db)):
    se_id = service.add_session_exercise(conn, uid, session_id, body.exercise_id, body.target_rest_sec)
    if se_id is None:
        raise HTTPException(404, "session or exercise not found")
    return {"session_exercise_id": se_id, "session": service.session_detail(conn, uid, session_id)}


@router.delete("/session-exercises/{se_id}", status_code=204)
def remove_exercise(se_id: int, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    service.remove_session_exercise(conn, uid, se_id)
    return Response(status_code=204)


# --- sets

@router.put("/sets")
def upsert_sets(sets: list[SetIn], uid: int = Depends(api_user), conn=Depends(db.get_db)):
    """Batch upsert (the offline queue). Returns the uids that were stored;
    rejected ones (not the user's session) are listed so the client drops them."""
    if len(sets) > 200:
        raise HTTPException(413, "too many sets in one batch")
    ok, rejected = [], []
    for s in sets:
        (ok if service.upsert_set(conn, uid, s) else rejected).append(s.uid)
    return {"saved": ok, "rejected": rejected}


@router.patch("/sets/{set_uid}")
def update_set(set_uid: str, u: SetUpdate, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    if not service.update_set(conn, uid, set_uid, u):
        raise HTTPException(404, "set not found")
    return {"ok": True}


@router.delete("/sets/{set_uid}", status_code=204)
def delete_set(set_uid: str, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    service.delete_set(conn, uid, set_uid)
    return Response(status_code=204)
