"""Session-calibrated daily averages: readings within 5 minutes of the previous
one form a session, sessions are averaged first, then the day."""
import itertools
import os
import tempfile
from datetime import date

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.bp import service  # noqa: E402
from app.modules.bp.schemas import Reading  # noqa: E402

db.init_db()
_n = itertools.count()


def _r(t, s, d, p=None, i=[0]):
    i[0] += 1
    return Reading(id=i[0], taken_at=t, systolic=s, diastolic=d, pulse=p)


def test_sessions_group_by_gap_to_previous_reading():
    rs = [_r("2026-11-19T07:00", 1, 1), _r("2026-11-19T07:02", 2, 1), _r("2026-11-19T07:07", 3, 1),  # 2 and 5 min gaps
          _r("2026-11-19T07:13", 4, 1),                                                          # 6 min -> new
          _r("2026-11-19T23:58", 5, 1), _r("2026-11-20T00:01", 6, 1)]                            # never across midnight
    groups = service.sessions(list(reversed(rs)))  # input order doesn't matter
    assert [[r.systolic for r in g] for g in groups] == [[1, 2, 3], [4], [5], [6]]


def test_day_value_is_mean_of_session_means():
    rs = [
        # morning session: 3 quick readings -> 144/92, pulse (80+70)/2 = 75
        _r("2026-11-19T07:00", 150, 95, 80), _r("2026-11-19T07:02", 144, 92, None), _r("2026-11-19T07:04", 138, 89, 70),
        # evening: one reading 120/80, pulse 65
        _r("2026-11-19T21:00", 120, 80, 65),
    ]
    v = service.day_values(rs)["2026-11-19"]
    assert (v["systolic"], v["diastolic"], v["pulse"]) == ((144 + 120) / 2, (92 + 80) / 2, (75 + 65) / 2)
    assert v["count"] == 4 and v["sessions"] == 2
    # the old per-reading day average would have been 138 / 89 / 71.7


def _user():
    c = TestClient(app)
    c.cookies.set("tz", "UTC")
    c.username = f"ses{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": c.username, "password": "pw"})
    return c


def _add(c, t, s, d, p=None):
    assert c.post("/api/bp/readings", json={"taken_at": t, "systolic": s, "diastolic": d,
                                            "pulse": p}).status_code == 201


def test_trends_and_reports_use_sessions():
    c = _user()
    for t, s, d, p in [("2026-11-02T07:00", 150, 95, 80), ("2026-11-02T07:02", 144, 92, None),
                       ("2026-11-02T07:04", 138, 89, 70), ("2026-11-02T21:00", 120, 80, 65),
                       ("2026-11-04T08:00", 130, 85, 60)]:
        _add(c, t, s, d, p)
    with db.connect() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (c.username,)).fetchone()[0]
        pts = service.daily_averages(conn, uid, date(2026, 11, 2), date(2026, 11, 4))
        summ = service._summary(conn, uid, date(2026, 11, 2), date(2026, 11, 4))
    assert pts[0] == {"x": "2026-11-02", "systolic": 132.0, "diastolic": 86.0, "pulse": 70.0, "count": 4, "sessions": 2}
    assert summ == {"systolic": 131.0, "diastolic": 85.5, "pulse": 65.0, "count": 5}
    w = next(r for r in c.get("/api/bp/reports", params={"period": "week"}).json()["reports"]
             if r["start"] == "2026-11-02")
    assert w["systolic"] == {"avg": 131.0, "min": 120, "max": 150} and w["count"] == 5 and w["sessions"] == 3
