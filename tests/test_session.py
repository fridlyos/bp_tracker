"""Sessions survive only while the account they were issued for exists, and
login errors say which part was wrong."""
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
    c.post("/login", data={"username": "admin", "password": "admin"})
    return c


def _uid(admin, username):
    html = admin.get("/users-ui").text
    block = html.split(f"<b>@{username}</b>")[1]
    return int(block.split('action="/users/')[1].split("/")[0])


def test_stale_cookie_does_not_inherit_reused_id():
    admin = _admin()
    old = TestClient(app)
    old.post("/register", data={"username": "olduser", "password": "pw"})
    assert 'aria-label="Account: olduser"' in old.get("/").text

    admin.post(f"/users/{_uid(admin, 'olduser')}/delete")
    # new account may reuse the freed id (SQLite rowid reuse)
    admin.post("/users/new", data={"username": "newuser", "password": "pw"})

    r = old.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert old.get("/api/readings").status_code == 401


def test_login_error_names_the_problem():
    TestClient(app).post("/register", data={"username": "speller", "password": "pw"})
    r = TestClient(app).post("/login", data={"username": "Spellr", "password": "pw"})
    assert "No account called “spellr”" in r.text
    r = TestClient(app).post("/login", data={"username": "speller", "password": "nope"})
    assert "Wrong password for “speller”" in r.text
