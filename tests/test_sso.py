"""My Apps as the sign-in for the calorie counter: signed one-time passes."""
import base64
import itertools
import json
import os
import tempfile
import time
from urllib.parse import parse_qs, urlparse

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, sso  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()
sso.supported = lambda url: True  # no network in tests (fallback has its own test)
_n = itertools.count()


def _user():
    c = TestClient(app)
    c.username = f"sso{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": c.username, "password": "pw"})
    return c


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify(token, key_b64):
    body, sig = token.split(".")
    Ed25519PublicKey.from_public_bytes(_unb64(key_b64)).verify(_unb64(sig), _unb64(body))  # raises if bad
    return json.loads(_unb64(body))


def test_open_calorie_counter_signed_in():
    c = _user()
    key = c.get("/sso/key").json()
    assert key["alg"] == "Ed25519" and c.get("/sso/key").json() == key  # stable
    r = c.get("/calories/open", follow_redirects=False)
    assert r.status_code == 303 and r.headers["cache-control"] == "no-store"
    u = urlparse(r.headers["location"])
    assert (u.scheme, u.netloc, u.path) == ("https", "lose-weight-fridl.fly.dev", "/sso/callback")
    q = parse_qs(u.query)
    claims = _verify(q["token"][0], key["key"])
    assert claims["u"] == c.username and claims["aud"] == "lose-weight-fridl.fly.dev" and claims["iss"] == "myapps"
    assert 0 < claims["exp"] - time.time() <= 120 and q["next"] == ["/"]
    other = c.get("/calories/open", follow_redirects=False).headers["location"]
    assert parse_qs(urlparse(other).query)["token"] != q["token"]  # fresh jti each time
    # a tampered pass fails verification
    body, sig = q["token"][0].split(".")
    forged = base64.urlsafe_b64encode(json.dumps(claims | {"u": "admin"}).encode()).rstrip(b"=").decode()
    try:
        _verify(forged + "." + sig, key["key"])
        raise AssertionError("forged token verified")
    except Exception as e:
        assert "forged" not in str(e)


def test_authorize_requires_login_and_comes_back():
    anon = _user()
    anon.post("/logout")
    ret = "https://lose-weight-fridl.fly.dev/sso/callback"
    r = anon.get(f"/sso/authorize?return={ret}&next=/log", follow_redirects=False)
    assert r.headers["location"].startswith("/login?next=%2Fsso%2Fauthorize")
    page = anon.get(r.headers["location"]).text
    assert "Sign in to continue to your calorie counter" in page and 'name="next"' in page
    nxt = parse_qs(urlparse(r.headers["location"]).query)["next"][0]
    r = anon.post("/login", data={"username": anon.username, "password": "pw", "next": nxt}, follow_redirects=False)
    assert r.headers["location"] == nxt
    r = anon.get(nxt, follow_redirects=False)
    loc = urlparse(r.headers["location"])
    assert loc.netloc == "lose-weight-fridl.fly.dev" and parse_qs(loc.query)["next"] == ["/log"]


def test_authorize_only_to_your_calorie_counter():
    c = _user()
    for bad in ("https://evil.example.com/sso/callback", "https://lose-weight-fridl.fly.dev/steal",
                "http://lose-weight-fridl.fly.dev/sso/callback", "javascript:alert(1)", ""):
        r = c.get("/sso/authorize", params={"return": bad}, follow_redirects=False)
        assert r.headers["location"] == "/calories?sso=refused", bad
    c.post("/calories/link", data={"url": "http://localhost:8001"})  # local dev over http is fine
    r = c.get("/sso/authorize", params={"return": "http://localhost:8001/sso/callback"}, follow_redirects=False)
    assert r.headers["location"].startswith("http://localhost:8001/sso/callback?token=")
    # login ?next= never leaves the site
    c.post("/logout")
    r = c.post("/login", data={"username": c.username, "password": "pw", "next": "//evil.com"},
               follow_redirects=False)
    assert r.headers["location"] == "/"


def test_open_falls_back_to_plain_link_when_unsupported(monkeypatch):
    c = _user()
    monkeypatch.setattr(sso, "supported", lambda url: False)
    r = c.get("/calories/open", follow_redirects=False)
    assert r.headers["location"] == "https://lose-weight-fridl.fly.dev"
