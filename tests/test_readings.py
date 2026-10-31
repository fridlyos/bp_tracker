"""Phase 2: readings CRUD, validation, user isolation, purge boundary."""
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


def _user():
    """Fresh registered + logged-in client."""
    c = TestClient(app)
    c.cookies.set("tz", "America/New_York")
    r = c.post("/register", data={"username": f"u{next(_n)}-{os.getpid()}", "password": "pw"},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _add(c, taken_at="2026-10-31T08:15", sys=120, dia=80, **kw):
    return c.post("/api/readings",
                  json={"taken_at": taken_at, "systolic": sys, "diastolic": dia, **kw})


# --- API CRUD

def test_api_requires_login():
    c = TestClient(app)
    assert c.get("/api/readings").status_code == 401
    assert c.post("/api/readings", json={"systolic": 120, "diastolic": 80}).status_code == 401


def test_add_list_delete():
    c = _user()
    r = _add(c, pulse=64, note="  left arm  ")
    assert r.status_code == 201
    body = r.json()
    assert body["taken_at"] == "2026-10-31T08:15"
    assert (body["systolic"], body["diastolic"], body["pulse"], body["note"]) == (120, 80, 64, "left arm")

    _add(c, taken_at="2026-10-30T21:00", sys=130, dia=85)
    assert [x["systolic"] for x in c.get("/api/readings").json()] == [120, 130]  # newest first
    day = c.get("/api/readings", params={"from": "2026-10-30", "to": "2026-10-30"}).json()
    assert [x["systolic"] for x in day] == [130]

    assert c.delete(f"/api/readings/{body['id']}").status_code == 204
    assert c.delete(f"/api/readings/{body['id']}").status_code == 404
    assert len(c.get("/api/readings").json()) == 1


def test_seconds_dropped_and_tz_aware_converted():
    c = _user()
    assert _add(c, taken_at="2026-10-31T08:15:42").json()["taken_at"] == "2026-10-31T08:15"
    # 12:00Z == 08:00 in New York (EDT, UTC-4) on Oct 31 2026
    assert _add(c, taken_at="2026-10-31T12:00:00Z").json()["taken_at"] == "2026-10-31T08:00"


def test_missing_taken_at_defaults_to_now():
    c = _user()
    r = _add(c, taken_at=None)
    assert r.status_code == 201 and len(r.json()["taken_at"]) == 16


# --- validation

def test_validation_bounds():
    c = _user()
    ok = [dict(sys=50, dia=30), dict(sys=260, dia=160), dict(sys=120, dia=80, pulse=30),
          dict(sys=120, dia=80, pulse=220)]
    bad = [dict(sys=49, dia=30), dict(sys=261, dia=80), dict(sys=120, dia=29),
           dict(sys=200, dia=161), dict(sys=120, dia=80, pulse=29),
           dict(sys=120, dia=80, pulse=221), dict(sys=80, dia=80), dict(sys=80, dia=90)]
    for kw in ok:
        assert _add(c, **kw).status_code == 201, kw
    for kw in bad:
        assert _add(c, **kw).status_code == 422, kw


# --- isolation

def test_user_isolation():
    a, b = _user(), _user()
    rid = _add(a).json()["id"]
    assert b.get("/api/readings").json() == []
    assert b.delete(f"/api/readings/{rid}").status_code == 404
    b.post(f"/readings/{rid}/delete", data={"next": "/"})  # web path too
    assert b.post("/api/readings/purge", json={"before": "2100-01-01"}).json()["count"] == 0
    assert len(a.get("/api/readings").json()) == 1


# --- purge

def test_purge_boundary_and_dry_run():
    c = _user()
    _add(c, taken_at="2026-10-29T23:59", sys=101)
    _add(c, taken_at="2026-10-30T00:00", sys=102)
    _add(c, taken_at="2026-10-30T12:00", sys=103)

    dry = c.post("/api/readings/purge", json={"before": "2026-10-30", "dry_run": True}).json()
    assert dry["count"] == 1 and dry["deleted"] is False
    assert len(c.get("/api/readings").json()) == 3

    r = c.post("/api/readings/purge", json={"before": "2026-10-30"}).json()
    assert r["count"] == 1 and r["deleted"] is True
    assert sorted(x["systolic"] for x in c.get("/api/readings").json()) == [102, 103]


# --- web UI

def test_web_add_shows_on_today_and_history():
    c = _user()
    home = c.get("/")
    assert home.status_code == 200 and "Add reading" in home.text
    # use the server's own 'now' so the reading lands on today
    now = home.text.split('name="taken_at" value="')[1][:16]
    r = c.post("/readings", data={"taken_at": now, "systolic": "141", "diastolic": "91",
                                  "pulse": "", "note": "<b>x</b>"}, follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/").text
    assert "141/91" in page and "&lt;b&gt;x&lt;/b&gt;" in page  # escaped note
    assert "141/91" in c.get("/history").text


def test_web_validation_error_rerenders_with_values():
    c = _user()
    r = c.post("/readings", data={"taken_at": "", "systolic": "70", "diastolic": "90"})
    assert r.status_code == 422
    assert "Systolic must be higher than diastolic" in r.text
    assert 'value="70"' in r.text
    r = c.post("/readings", data={"systolic": "abc", "diastolic": "80"})
    assert r.status_code == 422 and "Systolic:" in r.text
    assert c.get("/api/readings").json() == []


def test_web_delete_and_safe_redirect():
    c = _user()
    rid = _add(c).json()["id"]
    r = c.post(f"/readings/{rid}/delete", data={"next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    assert c.get("/api/readings").json() == []


def test_web_purge_preview_and_confirm():
    c = _user()
    _add(c, taken_at="2026-01-01T09:00")
    _add(c, taken_at="2026-06-01T09:00")
    preview = c.get("/settings", params={"before": "2026-03-01"})
    assert "<b>1</b> reading taken before 2026-03-01" in preview.text
    assert len(c.get("/api/readings").json()) == 2  # preview deletes nothing
    r = c.post("/settings/purge", data={"before": "2026-03-01"}, follow_redirects=False)
    assert r.headers["location"] == "/settings?purged=1"
    assert "Deleted 1 reading." in c.get(r.headers["location"]).text
    assert len(c.get("/api/readings").json()) == 1


def test_pages_require_login():
    c = TestClient(app)
    for path in ("/history", "/settings"):
        assert c.get(path, follow_redirects=False).headers["location"] == "/login"
