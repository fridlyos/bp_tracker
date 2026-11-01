"""Core JSON API: push notification subscriptions (/api/push). Module APIs
live in app/modules/<key>/api.py."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from . import db, push
from .deps import api_user

router = APIRouter(prefix="/api/push")


class _Keys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(pattern=r"^https://", max_length=1000)
    keys: _Keys


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=1000)


@router.get("/key")
def public_key(uid: int = Depends(api_user), conn=Depends(db.get_db)):
    """VAPID public key for pushManager.subscribe(applicationServerKey)."""
    return {"key": push.vapid(conn)[1]}


@router.post("/subscribe", status_code=201)
def subscribe(s: SubscriptionIn, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    push.subscribe(conn, uid, s.endpoint, s.keys.p256dh, s.keys.auth)
    return {"devices": push.device_count(conn, uid)}


@router.post("/unsubscribe")
def unsubscribe(e: EndpointIn, uid: int = Depends(api_user), conn=Depends(db.get_db)):
    push.unsubscribe(conn, uid, e.endpoint)
    return {"devices": push.device_count(conn, uid)}


@router.post("/test")
def test(uid: int = Depends(api_user), conn=Depends(db.get_db)):
    """Send a test notification to all of my devices."""
    sent = push.send_to_user(conn, uid, "Notifications work", "You'll get reminders here.", "/")
    return {"sent": sent}
