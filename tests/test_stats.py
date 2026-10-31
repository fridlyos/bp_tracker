"""Phase 3: daily averages + /api/stats ranges."""
import itertools
import os
import tempfile
from datetime import date, datetime, timedelta, timezone

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db, service  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import ReadingIn  # noqa: E402

db.init_db()
_n = itertools.count()


def _user():
    c = TestClient(app)
    c.cookies.set("tz", "UTC")
    c.username = f"s{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": c.username, "password": "pw"})
    return c


def _add(c, taken_at, sys, dia, pulse=None):
    r = c.post("/api/readings", json={"taken_at": taken_at, "systolic": sys,
                                      "diastolic": dia, "pulse": pulse})
    assert r.status_code == 201, r.text


def test_daily_averages_math():
    c = _user()
    _add(c, "2026-10-01T08:00", 120, 80, 60)
    _add(c, "2026-10-01T20:00", 131, 85, None)   # pulse NULL ignored in avg
    _add(c, "2026-10-01T22:00", 125, 81, 71)
    _add(c, "2026-10-03T09:00", 140, 90, 75)
    with db.connect() as conn:
        user_id = conn.execute("SELECT id FROM users WHERE username = ?",
                               (c.username,)).fetchone()[0]
        rows = service.daily_averages(conn, user_id, date(2026, 10, 1), date(2026, 10, 3))
    assert rows == [
        {"x": "2026-10-01", "systolic": 125.3, "diastolic": 82.0, "pulse": 65.5, "count": 3},
        {"x": "2026-10-03", "systolic": 140.0, "diastolic": 90.0, "pulse": 75.0, "count": 1},
    ]


def test_week_range_bounds():
    c = _user()
    today = datetime.now(timezone.utc).date()  # tz cookie = UTC
    _add(c, f"{today - timedelta(days=7)}T23:59", 150, 95)   # just outside 7-day window
    _add(c, f"{today - timedelta(days=6)}T00:00", 120, 80)   # first day inside
    _add(c, f"{today}T07:00", 130, 84, 70)
    s = c.get("/api/stats", params={"range": "week"}).json()
    assert s["start"] == str(today - timedelta(days=6)) and s["end"] == str(today)
    assert [p["x"] for p in s["points"]] == [str(today - timedelta(days=6)), str(today)]
    assert s["summary"] == {"systolic": 125.0, "diastolic": 82.0, "pulse": 70.0, "count": 2}

    m = c.get("/api/stats", params={"range": "month"}).json()
    assert m["start"] == str(today - timedelta(days=29)) and m["summary"]["count"] == 3
    q = c.get("/api/stats", params={"range": "3m"}).json()
    assert q["start"] == str(today - timedelta(days=89))


def test_day_range_returns_individual_readings_oldest_first():
    c = _user()
    _add(c, "2026-09-15T21:00", 130, 85, 66)
    _add(c, "2026-09-15T07:30", 118, 76)
    _add(c, "2026-09-16T07:30", 119, 77)
    s = c.get("/api/stats", params={"range": "day", "date": "2026-09-15"}).json()
    assert [p["x"] for p in s["points"]] == ["2026-09-15T07:30", "2026-09-15T21:00"]
    assert s["points"][0]["pulse"] is None and s["summary"]["count"] == 2


def test_stats_isolation_and_auth():
    a, b = _user(), _user()
    _add(a, "2026-09-15T07:30", 118, 76)
    assert b.get("/api/stats", params={"range": "day", "date": "2026-09-15"}).json()["points"] == []
    assert TestClient(app).get("/api/stats").status_code == 401
    assert a.get("/api/stats", params={"range": "year"}).status_code == 422


def test_trends_page():
    c = _user()
    r = c.get("/trends")
    assert r.status_code == 200 and "bp-chart" in r.text and "chart.umd.min.js" in r.text
    assert TestClient(app).get("/trends", follow_redirects=False).headers["location"] == "/login"
    # zone legend + thresholds the chart colours by; no MAP line any more
    for text in ("≤120", "121–129", "130–139", "140+", "≤80", "81–90", "91+",
                 'SYS_ZONES = [[121, "--z-good"', 'DIA_ZONES = [[81, "--z-good"'):
        assert text in r.text, text
    assert "Mean" not in r.text
