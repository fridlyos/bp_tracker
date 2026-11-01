"""Launcher + module shell: module icons, module tab bar, old-URL redirects."""
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


def test_launcher_shows_only_module_icons():
    c = _user()
    c.post("/api/bp/readings", json={"systolic": 130, "diastolic": 85})
    html = c.get("/").text
    for m in MODULES:
        assert f'class="app-icon" href="{m.href}" aria-label="{m.name}"' in html
    assert 'app-icon-name' not in html  # icon only, no visible text
    assert html.count('class="app-icon"') == len(MODULES)
    assert 'class="tabbar"' not in html and "130/85" not in html and m.description not in html


def test_admin_home_has_no_users_icon_but_menu_keeps_it():
    c = TestClient(app)
    c.post("/login", data={"username": "admin", "password": "admin"})
    html = c.get("/").text
    assert html.count('class="app-icon"') == len(MODULES)
    assert 'class="menu-item" role="menuitem" href="/users-ui"' in html


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
