"""Range averages are the average of daily averages (each day weighs the same)."""
import itertools
import os
import tempfile
from datetime import datetime, timedelta, timezone

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()
_n = itertools.count()


def _user():
    c = TestClient(app)
    c.cookies.set("tz", "UTC")
    c.post("/register", data={"username": f"dwa{next(_n)}-{os.getpid()}", "password": "pw"})
    return c


def _add(c, taken_at, sys, dia, pulse=None):
    assert c.post("/api/bp/readings", json={"taken_at": taken_at, "systolic": sys,
                                            "diastolic": dia, "pulse": pulse}).status_code == 201


def test_trends_summary_weights_days_equally():
    c = _user()
    today = datetime.now(timezone.utc).date()
    d1, d2 = today - timedelta(days=2), today - timedelta(days=1)
    # day 1: three readings, avg 140/90 pulse 80; day 2: one reading 120/80 pulse 60
    _add(c, f"{d1}T07:00", 130, 86, 70)
    _add(c, f"{d1}T12:00", 140, 90, 80)
    _add(c, f"{d1}T19:00", 150, 94, 90)
    _add(c, f"{d2}T08:00", 120, 80, 60)
    s = c.get("/api/bp/stats", params={"range": "week"}).json()["summary"]
    # average of daily averages: (140+120)/2 = 130, (90+80)/2 = 85, (80+60)/2 = 70
    # (all readings pooled would give 135 / 87.5 / 75)
    assert s == {"systolic": 130.0, "diastolic": 85.0, "pulse": 70.0, "count": 4}


def test_pulse_average_skips_days_without_pulse():
    c = _user()
    today = datetime.now(timezone.utc).date()
    _add(c, f"{today - timedelta(days=1)}T08:00", 120, 80)          # no pulse that day
    _add(c, f"{today}T08:00", 130, 84, 60)
    _add(c, f"{today}T20:00", 134, 86, 70)
    s = c.get("/api/bp/stats", params={"range": "week"}).json()["summary"]
    assert s["systolic"] == 126.0 and s["pulse"] == 65.0 and s["count"] == 3


def test_empty_range_summary():
    s = _user().get("/api/bp/stats", params={"range": "month"}).json()["summary"]
    assert s == {"systolic": None, "diastolic": None, "pulse": None, "count": 0}


def test_reports_average_daily_averages_but_min_max_are_readings():
    c = _user()
    # week of Mon 2026-11-02: Mon has 3 readings, Wed has 1
    _add(c, "2026-11-02T07:00", 130, 86, 70)
    _add(c, "2026-11-02T12:00", 140, 90, 80)
    _add(c, "2026-11-02T19:00", 150, 94, 90)
    _add(c, "2026-11-04T08:00", 120, 80, None)
    w = next(r for r in c.get("/api/bp/reports", params={"period": "week"}).json()["reports"]
             if r["start"] == "2026-11-02")
    assert w["count"] == 4 and w["days"] == 2
    assert w["systolic"] == {"avg": 130.0, "min": 120, "max": 150}   # pooled would be 135.0
    assert w["diastolic"] == {"avg": 85.0, "min": 80, "max": 94}
    assert w["pulse"] == {"avg": 80.0, "min": 70, "max": 90}         # only Monday has pulse
    m = c.get("/api/bp/reports", params={"period": "month"}).json()["reports"][0]
    assert m["systolic"]["avg"] == 130.0
