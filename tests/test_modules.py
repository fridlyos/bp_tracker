"""Launcher + module shell: tiles, module tab bar, old-URL redirects."""
import itertools
import os
import tempfile

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import MODULES  # noqa: E402

db.init_db()
_n = itertools.count()


def _user():
    c = TestClient(app)
    c.cookies.set("tz", "UTC")
    c.post("/register", data={"username": f"mod{next(_n)}-{os.getpid()}", "password": "pw"})
    return c


def test_launcher_has_a_tile_per_module_and_no_tabbar():
    c = _user()
    html = c.get("/").text
    for m in MODULES:
        assert f'class="tile" href="{m.href}"' in html and m.name in html
    assert 'class="tabbar"' not in html
    assert "Log blood pressure" in html  # no readings yet -> description


def test_module_pages_show_module_tabbar_with_home():
    c = _user()
    html = c.get("/bp/reports").text
    assert 'class="tabbar" aria-label="Blood pressure"' in html
    assert 'href="/" class="tab-home"' in html
    assert 'href="/bp/reports" class="active" aria-current="page"' in html
    assert 'href="/bp" class="active"' not in html  # only the longest match is active
    assert 'class="crumb" href="/bp">Blood pressure<' in html


def test_old_urls_redirect_into_module():
    c = _user()
    for old in ("/trends", "/history", "/reports", "/settings"):
        r = c.get(old, follow_redirects=False)
        assert r.status_code == 308 and r.headers["location"] == f"/bp{old}"
    r = c.get("/reports?period=month", follow_redirects=False)
    assert r.headers["location"] == "/bp/reports?period=month"


def test_module_pages_need_login():
    anon = TestClient(app)
    for path in ("/bp", "/bp/trends", "/bp/reports"):
        assert anon.get(path, follow_redirects=False).headers["location"] == "/login"
