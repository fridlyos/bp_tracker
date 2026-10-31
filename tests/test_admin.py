"""Admin session must not trap the user: register explains + admin can create users."""
import os
import tempfile

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()


def _admin():
    c = TestClient(app)
    r = c.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_register_while_logged_in_explains_and_offers_logout():
    c = _admin()
    r = c.get("/register", follow_redirects=False)
    assert r.status_code == 200
    assert "logged in as <b>admin</b>" in r.text and 'action="/logout"' in r.text
    assert 'action="/register"' not in r.text
    c.post("/logout")
    r = c.get("/register")
    assert 'action="/register"' in r.text


def test_admin_creates_user_who_can_log_in():
    c = _admin()
    r = c.post("/users/new", data={"username": "fromadmin", "password": "pw"},
               follow_redirects=False)
    assert r.headers["location"] == "/users-ui"
    assert "@fromadmin" in c.get("/users-ui").text
    dup = c.post("/users/new", data={"username": "fromadmin", "password": "x"})
    assert "That username is taken." in dup.text

    u = TestClient(app)
    u.post("/login", data={"username": "fromadmin", "password": "pw"})
    assert "Add reading" in u.get("/").text


def test_non_admin_cannot_create_users():
    u = TestClient(app)
    u.post("/register", data={"username": "plainuser", "password": "pw"})
    r = u.post("/users/new", data={"username": "sneaky", "password": "pw"},
               follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert "@sneaky" not in _admin().get("/users-ui").text
