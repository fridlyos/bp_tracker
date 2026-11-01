"""Weekly / monthly reports."""
import itertools
import os
import tempfile
from datetime import date

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.modules.bp import service  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()
_n = itertools.count()


def _user():
    c = TestClient(app)
    c.cookies.set("tz", "UTC")
    c.username = f"rep{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": c.username, "password": "pw"})
    return c


def _add(c, taken_at, sys, dia, pulse=None):
    r = c.post("/api/bp/readings", json={"taken_at": taken_at, "systolic": sys,
                                      "diastolic": dia, "pulse": pulse})
    assert r.status_code == 201, r.text


def test_period_bounds():
    # 2026-11-01 is a Sunday: its week starts Monday Oct 26
    assert service._period_bounds(date(2026, 11, 1), "week") == (date(2026, 10, 26), date(2026, 11, 1))
    assert service._period_bounds(date(2026, 11, 2), "week") == (date(2026, 11, 2), date(2026, 11, 8))
    assert service._period_bounds(date(2026, 2, 15), "month") == (date(2026, 2, 1), date(2026, 2, 28))
    assert service._period_bounds(date(2026, 12, 31), "month") == (date(2026, 12, 1), date(2026, 12, 31))


def test_weekly_report_math_and_zones():
    c = _user()
    _add(c, "2026-10-26T08:00", 118, 78, 60)   # Mon
    _add(c, "2026-10-28T08:00", 125, 85, None)  # pulse missing
    _add(c, "2026-11-01T21:00", 142, 92, 70)   # Sun, same week
    _add(c, "2026-11-02T07:00", 130, 81, 66)   # next Monday -> new week
    rep = c.get("/api/bp/reports", params={"period": "week"}).json()["reports"]
    assert [(r["start"], r["end"], r["count"]) for r in rep] == [
        ("2026-11-02", "2026-11-08", 1), ("2026-10-26", "2026-11-01", 3)]
    w = rep[1]
    assert w["label"] == "Oct 26 – Nov 1, 2026" and w["days"] == 3
    assert w["systolic"] == {"avg": 128.3, "min": 118, "max": 142}
    assert w["diastolic"] == {"avg": 85.0, "min": 78, "max": 92}
    assert w["pulse"] == {"avg": 65.0, "min": 60, "max": 70}
    assert [z["count"] for z in w["systolic_zones"]] == [1, 1, 0, 1]
    assert [z["count"] for z in w["diastolic_zones"]] == [1, 1, 1]
    # zone edges are inclusive at the top: 120/80 good, 129 ok, 90 elevated
    assert [z["count"] for z in service._zone_counts([120, 121, 129, 130, 139, 140], service.SYS_ZONES)] == [1, 2, 2, 1]
    assert [z["count"] for z in service._zone_counts([80, 81, 90, 91], service.DIA_ZONES)] == [1, 2, 1]


def test_monthly_report_and_page():
    c = _user()
    _add(c, "2026-09-30T23:59", 120, 80)
    _add(c, "2026-10-01T00:00", 130, 85)
    _add(c, "2026-10-31T12:00", 140, 90)
    rep = c.get("/api/bp/reports", params={"period": "month"}).json()["reports"]
    assert [(r["label"], r["count"]) for r in rep] == [("October 2026", 2), ("September 2026", 1)]
    assert rep[1]["pulse"] is None
    html = c.get("/bp/reports?period=month").text
    assert "October 2026" in html and "September 2026" in html and "no data" in html
    assert c.get("/bp/reports?period=bogus").status_code == 200  # falls back to weekly


def test_reports_are_per_user_and_need_login():
    a, b = _user(), _user()
    _add(a, "2026-10-01T08:00", 120, 80)
    assert b.get("/api/bp/reports").json()["reports"] == []
    assert "No readings yet" in b.get("/bp/reports").text
    anon = TestClient(app)
    assert anon.get("/api/bp/reports").status_code == 401
    assert anon.get("/bp/reports", follow_redirects=False).headers["location"] == "/login"
