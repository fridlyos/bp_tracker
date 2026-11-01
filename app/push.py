"""Web Push (core, usable by any module).

iPhone: works for the home-screen app on iOS 16.4+, after the user taps
"Enable notifications" (permission prompt). The browser gives us a
subscription (endpoint + keys); we send to it via the push service, signed
with our VAPID key pair.

VAPID keys: VAPID_PRIVATE_KEY (PEM) + VAPID_PUBLIC_KEY env vars if set,
otherwise generated on first use and kept in the DB (app_kv), so there is
nothing to configure. Changing keys invalidates every subscription.
"""
import base64
import json
import logging
import os

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

logger = logging.getLogger("bp_tracker.push")

# Apple requires a real mailto: or https: subject in the VAPID claims.
SUBJECT = os.getenv("VAPID_SUBJECT", "https://bp-tracker-fridl.fly.dev")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _public_b64(v: Vapid) -> str:
    return _b64url(v.public_key.public_bytes(serialization.Encoding.X962,
                                             serialization.PublicFormat.UncompressedPoint))


def _kv_get(conn, key):
    row = conn.execute("SELECT value FROM app_kv WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def vapid(conn) -> tuple[Vapid, str]:
    """(signing key, public key as base64url for pushManager.subscribe)."""
    pem = os.getenv("VAPID_PRIVATE_KEY") or _kv_get(conn, "vapid_private_pem")
    if pem is None:
        v = Vapid()
        v.generate_keys()
        pem = v.private_pem().decode()
        # INSERT OR IGNORE + re-read: two workers racing end up on the same key
        conn.execute("INSERT OR IGNORE INTO app_kv (key, value) VALUES ('vapid_private_pem', ?)", (pem,))
        conn.commit()
        pem = _kv_get(conn, "vapid_private_pem")
        logger.info("generated VAPID key pair")
    v = Vapid.from_pem(pem.encode())
    return v, os.getenv("VAPID_PUBLIC_KEY") or _public_b64(v)


# --- subscriptions

def subscribe(conn, user_id: int, endpoint: str, p256dh: str, auth: str) -> None:
    """Save (or move to this user) one device's subscription."""
    conn.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) VALUES (?,?,?,?)"
        " ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,"
        " p256dh=excluded.p256dh, auth=excluded.auth",
        (user_id, endpoint, p256dh, auth))
    conn.commit()


def unsubscribe(conn, user_id: int, endpoint: str) -> None:
    conn.execute("DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
                 (user_id, endpoint))
    conn.commit()


def device_count(conn, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?",
                        (user_id,)).fetchone()[0]


def send_to_user(conn, user_id: int, title: str, body: str, url: str = "/") -> int:
    """Notify every device of the user. Returns how many accepted it.
    Subscriptions the push service reports gone (404/410) are deleted."""
    signer, _ = vapid(conn)
    payload = json.dumps({"title": title, "body": body, "url": url})
    sent = 0
    for s in conn.execute("SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchall():
        info = {"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}
        try:
            webpush(info, payload, vapid_private_key=signer,
                    vapid_claims={"sub": SUBJECT}, ttl=3600)
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (s["id"],))
                conn.commit()
                logger.info("user %s: dropped expired push subscription %s", user_id, s["id"])
            else:
                logger.warning("user %s: push failed (%s): %s", user_id, status, e)
    return sent
