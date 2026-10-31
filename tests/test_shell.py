"""App shell: logged-in user is shown on every page; iPhone meta present."""
import itertools
import os
import tempfile

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()
_n = itertools.count()


def test_username_shown_on_every_page():
    c = TestClient(app)
    name = f"shell{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": name, "password": "pw"})
    for path in ("/", "/trends", "/history", "/settings"):
        html = c.get(path).text
        assert f'aria-label="Account: {name}"' in html, path
        assert f"<b>{name}</b>" in html and 'action="/logout"' in html, path
        assert 'href="/users-ui"' not in html  # non-admin: no Users entry


def test_username_is_escaped():
    c = TestClient(app)
    c.post("/register", data={"username": "<i>x</i>", "password": "pw"})
    html = c.get("/").text
    assert "<b><i>x</i></b>" not in html and "&lt;i&gt;x&lt;/i&gt;" in html


def test_admin_menu_has_users_and_role():
    c = TestClient(app)
    c.post("/login", data={"username": "admin", "password": "admin"})
    html = c.get("/").text
    assert 'class="role">admin<' in html and "Users &amp; switch account" in html


def test_signed_out_has_no_tabbar_and_iphone_meta():
    html = TestClient(app).get("/login").text
    assert 'class="tabbar"' not in html and "data-theme-toggle" in html
    assert "viewport-fit=cover" in html and "apple-mobile-web-app-capable" in html
