"""Single sign-on for companion apps (the calorie counter): My Apps vouches
for the signed-in user with a short-lived, single-use, Ed25519-signed pass.

Token = base64url(json payload) + "." + base64url(signature). Payload:
  iss "myapps", sub (user id), u (username), aud (the companion app's host),
  iat / exp (unix seconds, 2 minutes), jti (random, the companion keeps used ones).
The private key is generated on first use and kept in app_kv (like the VAPID
key); companions verify with the public key from GET /sso/key.

Passes only go to the host of the user's calorie counter link (or its
default), and only to its /sso/callback path, so a crafted ?return= can't
send a pass anywhere else.
"""
import base64
import json
import logging
import secrets
import time
from urllib.parse import urlencode, urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from . import db
from .deps import current_user
from .schemas import User

log = logging.getLogger("bp_tracker.sso")
router = APIRouter()
TOKEN_TTL = 120
_LOCAL = {"localhost", "127.0.0.1"}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def private_key(conn) -> Ed25519PrivateKey:
    row = conn.execute("SELECT value FROM app_kv WHERE key = 'sso_private_pem'").fetchone()
    if row is None:
        pem = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        # INSERT OR IGNORE + re-read: racing workers end up on the same key
        conn.execute("INSERT OR IGNORE INTO app_kv (key, value) VALUES ('sso_private_pem', ?)", (pem,))
        conn.commit()
        row = conn.execute("SELECT value FROM app_kv WHERE key = 'sso_private_pem'").fetchone()
        log.info("generated SSO signing key")
    return serialization.load_pem_private_key(row[0].encode(), password=None)


def public_key_b64(conn) -> str:
    return _b64(private_key(conn).public_key().public_bytes(serialization.Encoding.Raw,
                                                           serialization.PublicFormat.Raw))


def issue_token(conn, user: User, aud: str) -> str:
    now = int(time.time())
    body = json.dumps({"iss": "myapps", "sub": user.id, "u": user.username, "aud": aud, "iat": now,
                       "exp": now + TOKEN_TTL, "jti": secrets.token_urlsafe(16)}, separators=(",", ":")).encode()
    return _b64(body) + "." + _b64(private_key(conn).sign(body))


def allowed_callback(conn, user_id: int, url: str) -> str | None:
    """`url` if it's the /sso/callback of this user's calorie counter host, else None."""
    from .modules.calories import service as calories
    p, home = urlparse(url or ""), urlparse(calories.get_url(conn, user_id))
    secure = p.scheme == "https" or (p.scheme == "http" and p.hostname in _LOCAL)
    if secure and p.netloc and p.netloc == home.netloc and p.path == "/sso/callback":
        return url.split("?", 1)[0].split("#", 1)[0]
    return None


_PING_TTL = 600
_ping_cache: dict[str, tuple[bool, float]] = {}


def supported(base_url: str) -> bool:
    """Does the calorie counter at `base_url` accept passes (GET /sso/ping)?
    Cached for 10 minutes; if it can't be reached, just link to it plainly."""
    import urllib.request
    hit = _ping_cache.get(base_url)
    if hit and time.time() - hit[1] < _PING_TTL:
        return hit[0]
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/sso/ping", timeout=3) as r:
            ok = r.status == 200 and json.loads(r.read() or b"{}").get("sso") is True
    except Exception:
        ok = False
    _ping_cache[base_url] = (ok, time.time())
    return ok


def redirect_with_token(conn, user: User, callback: str, next_: str = "/") -> RedirectResponse:
    token = issue_token(conn, user, urlparse(callback).netloc)
    log.info("sso pass for user %s -> %s", user.id, urlparse(callback).netloc)
    resp = RedirectResponse(f"{callback}?{urlencode({'token': token, 'next': next_ or '/'})}", status_code=303)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@router.get("/sso/key")
def sso_key(conn=Depends(db.get_db)):
    """Public key companions use to verify passes."""
    return {"alg": "Ed25519", "key": public_key_b64(conn)}


@router.get("/sso/authorize")
def sso_authorize(request: Request, return_: str = Query("", alias="return"), next_: str = Query("/", alias="next"),
                  conn=Depends(db.get_db), user: User | None = Depends(current_user)):
    """'Sign in with My Apps' from a companion: log in here first if needed,
    then go back to its /sso/callback with a pass."""
    if user is None:
        here = request.url.path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse(f"/login?{urlencode({'next': here})}", status_code=303)
    callback = allowed_callback(conn, user.id, return_)
    if callback is None:
        log.warning("sso authorize refused for user %s: return=%r", user.id, return_[:200])
        return RedirectResponse("/calories?sso=refused", status_code=303)
    return redirect_with_token(conn, user, callback, next_)
