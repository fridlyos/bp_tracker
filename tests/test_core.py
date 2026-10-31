"""End-to-end checks for the Phase 1 skeleton: auth flow + home render."""
import os
import tempfile

os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")  # throwaway db before app import

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()


def _client():
    return TestClient(app)


def test_anonymous_redirects_to_login():
    r = _client().get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_register_login_home():
    c = _client()
    r = c.post("/register", data={"username": "alice", "password": "pw"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Add reading" in c.get("/").text

    c.post("/logout")
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"

    bad = c.post("/login", data={"username": "alice", "password": "nope"})
    assert "Wrong username or password" in bad.text

    c.post("/login", data={"username": "alice", "password": "pw"})
    assert "Add reading" in c.get("/").text


def test_duplicate_username_rejected():
    c = _client()
    c.post("/register", data={"username": "bob", "password": "pw"})
    r = _client().post("/register", data={"username": "bob", "password": "x"})
    assert "That username is taken" in r.text


def test_admin_seeded_and_sees_users():
    c = _client()
    c.post("/login", data={"username": "admin", "password": "admin"})
    r = c.get("/")
    # admin logs readings like anyone; Users is an extra nav entry
    assert r.url.path == "/" and "Add reading" in r.text and 'href="/users-ui"' in r.text
    assert "@admin" in c.get("/users-ui").text


def test_non_admin_cannot_open_users_page():
    c = _client()
    c.post("/register", data={"username": "carol", "password": "pw"})
    assert c.get("/users-ui", follow_redirects=False).headers["location"] == "/"


def test_static_and_sw_served():
    c = _client()
    assert c.get("/sw.js").status_code == 200
    assert c.get("/static/manifest.webmanifest").json()["short_name"] == "BP"
