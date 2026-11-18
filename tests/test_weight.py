"""Weight module: entries in lb/kg, summary, trends, settings, reminders, backup,
and the workout calorie estimate using the latest weigh-in."""
import io
import itertools
import json
import os
import tempfile
from datetime import date, datetime, timezone

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db, push  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.weight import service  # noqa: E402

db.init_db()
_n = itertools.count()


def _user(tz="UTC"):
    c = TestClient(app)
    c.cookies.set("tz", tz)
    c.username = f"wt{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": c.username, "password": "pw"})
    return c


def _uid(c):
    with db.connect() as conn:
        return conn.execute("SELECT id FROM users WHERE username = ?", (c.username,)).fetchone()[0]


def _add(c, taken_at, weight, unit="lb"):
    r = c.post("/api/weight/entries", json={"taken_at": taken_at, "weight": weight, "unit": unit})
    assert r.status_code == 201, r.text
    return r.json()


def test_add_in_lb_and_kg_and_validation():
    c = _user()
    e = _add(c, "2026-11-18T07:00", 180, "lb")
    assert abs(e["kg"] - 81.647) < 0.01 and e["unit"] == "lb"
    e2 = _add(c, "2026-11-18T19:00", 82, "kg")
    assert e2["kg"] == 82 and e2["unit"] == "kg"
    for bad in ({"weight": 10, "unit": "lb"}, {"weight": 500, "unit": "kg"}, {"weight": 180, "unit": "st"}):
        assert c.post("/api/weight/entries", json={"taken_at": "2026-11-18T07:00", **bad}).status_code == 422
    # web form: comma decimal, error text keeps the unit's range
    r = c.post("/weight/entries", data={"weight": "181,4", "unit": "lb", "taken_at": "2026-11-17T07:00"},
               follow_redirects=False)
    assert r.status_code == 303
    r = c.post("/weight/entries", data={"weight": "15", "unit": "kg", "taken_at": "2026-11-17T07:00"})
    assert r.status_code == 422 and "Weight must be 20-400 kg" in r.text
    r = c.post("/weight/entries", data={"weight": "", "unit": "lb"})
    assert r.status_code == 422
    days = c.get("/weight/history").text
    assert "181.4" in days and "entered 82.0 kg" in days  # kg weigh-in shown in lb with what was typed


def test_summary_and_goal_in_display_unit():
    c = _user()
    for d, w in [("2026-10-10", 190), ("2026-10-19", 187), ("2026-11-11", 184), ("2026-11-18", 182)]:
        _add(c, f"{d}T07:00", w)
    _add(c, "2026-11-18T20:00", 183)  # two today -> daily average 182.5
    c.post("/weight/settings", data={"unit": "lb", "goal": "175"})
    with db.connect() as conn:
        s = service.summary(conn, _uid(c), date(2026, 11, 18))
    assert s["latest"] == 183.0 and s["unit"] == "lb"
    assert s["change7"] == -1.5 and s["change30"] == -4.5  # 182.5 vs 184 / vs 187 (Oct 19 = 30 days back)
    assert s["goal"] == 175.0 and s["to_goal"] == 8.0
    # switch to kg: same data, converted
    c.post("/weight/settings", data={"unit": "kg", "goal": "79.4"})
    with db.connect() as conn:
        s = service.summary(conn, _uid(c), date(2026, 11, 18))
    assert s["unit"] == "kg" and s["latest"] == 83.0 and s["goal"] == 79.4
    assert c.post("/weight/settings", data={"unit": "kg", "goal": "5"}).status_code == 422


def test_trends_stats_daily_average_and_7day():
    c = _user()
    for d, w in [("2026-11-01", 80), ("2026-11-02", 82), ("2026-11-02", 84), ("2026-11-05", 81),
                 ("2026-11-10", 79)]:
        _add(c, f"{d}T07:00", w, "kg")
    c.put("/api/weight/settings", json={"unit": "kg", "goal": 75})
    with db.connect() as conn:
        st = service.stats(conn, _uid(c), date(2026, 11, 10), "week")
    assert [p["date"] for p in st["points"]] == ["2026-11-05", "2026-11-10"]
    # 7-day avg on the 5th uses the days before the range too: (80 + 83 + 81) / 3
    assert st["points"][0]["avg7"] == round((80 + 83 + 81) / 3, 1)
    assert st["points"][1]["avg7"] == 80.0  # (81 + 79) / 2 (the 2nd is > 6 days back)
    assert st["tiles"] == {"latest": 79.0, "change": -2.0, "low": 79.0, "high": 81.0, "avg": 80.0, "days": 2}
    assert st["goal"] == 75.0
    assert c.get("/api/weight/stats?range=all").json()["points"][1]["weight"] == 83.0
    assert c.get("/api/weight/stats?range=decade").status_code == 422


def test_pages_isolation_and_delete():
    c, other = _user(), _user()
    e = _add(c, "2026-11-18T07:00", 180)
    for p in ("/weight", "/weight/trends", "/weight/history", "/weight/settings"):
        assert c.get(p).status_code == 200, p
    page = c.get("/weight/trends").text
    assert "weight-chart" in page and 'href="/weight/trends" class="active"' in page
    assert "Add weigh-in" in c.get("/weight").text and 'name="unit" value="lb" checked' in c.get("/weight").text
    assert other.get("/api/weight/entries").json() == []
    other.post(f"/weight/entries/{e['id']}/delete")
    assert len(c.get("/api/weight/entries").json()) == 1
    c.post(f"/weight/entries/{e['id']}/delete")
    assert c.get("/api/weight/entries").json() == []
    anon = TestClient(app)
    assert anon.get("/weight", follow_redirects=False).headers["location"] == "/login"
    assert anon.get("/api/weight/stats").status_code == 401
    assert 'href="/weight"' in c.get("/").text  # launcher tile


def test_reminders(monkeypatch):
    c = _user("America/New_York")
    r = c.post("/weight/reminders", data={"at_time": "07:00"}, follow_redirects=False)
    assert r.status_code == 303
    assert c.post("/weight/reminders", data={"at_time": "07:00"}).status_code == 422
    uid = _uid(c)
    with db.connect() as conn:
        conn.execute("DELETE FROM weight_reminders WHERE user_id != ?", (uid,))
        conn.commit()
    sent = []
    monkeypatch.setattr(push, "send_to_user", lambda conn, u, title, body, url: sent.append((u, title, url)) or 1)
    with db.connect() as conn:
        service.send_reminders(conn, datetime(2026, 11, 18, 11, 59, tzinfo=timezone.utc))  # 06:59 NY
        service.send_reminders(conn, datetime(2026, 11, 18, 12, 5, tzinfo=timezone.utc))   # 07:05
        service.send_reminders(conn, datetime(2026, 11, 18, 12, 6, tzinfo=timezone.utc))
    assert sent == [(uid, "Time to weigh in", "/weight")]
    _add(c, "2026-11-19T06:30", 180)  # weighed in before the reminder -> not sent
    with db.connect() as conn:
        service.send_reminders(conn, datetime(2026, 11, 19, 12, 1, tzinfo=timezone.utc))
    assert len(sent) == 1
    assert "07:00" in c.get("/weight/settings").text


def test_workout_calories_use_latest_weigh_in():
    c = _user()
    ex = c.post("/api/workout/exercises", json={"name": "Squat"}).json()
    s = c.post("/api/workout/sessions").json()
    se = c.post(f"/api/workout/sessions/{s['id']}/exercises", json={"exercise_id": ex["id"]}).json()["session_exercise_id"]
    day = s["started_at"][:10]
    c.put("/api/workout/sets", json=[{"uid": "abcdefgh-wt1", "session_exercise_id": se,
                                      "started_at": f"{day}T10:00:00", "ended_at": f"{day}T10:00:40",
                                      "reps": 5, "weight": 225}])
    with db.connect() as conn:
        conn.execute("UPDATE workout_sessions SET started_at = ? WHERE id = ?", (f"{day}T09:00:00", s["id"]))
        conn.commit()
    c.post(f"/api/workout/sessions/{s['id']}/finish")
    c.put("/api/workout/settings", json={"body_weight": 200, "unit": "lb", "intensity": "moderate"})
    secs = 3600 + 40
    assert c.get(f"/api/workout/sessions/{s['id']}").json()["calories_estimate"] == round(5 * 200 / 2.20462 * secs / 3600)
    _add(c, f"{day}T07:00", 90, "kg")  # a weigh-in that morning wins over the settings value
    assert c.get(f"/api/workout/sessions/{s['id']}").json()["calories_estimate"] == round(5 * 90 * secs / 3600)
    assert "Using your latest weigh-in" in c.get("/workout/settings").text


def test_backup_roundtrip():
    a = _user("America/New_York")
    _add(a, "2026-11-17T07:00", 181.2)
    _add(a, "2026-11-18T07:00", 82, "kg")
    a.post("/weight/settings", data={"unit": "kg", "goal": "78"})
    a.post("/weight/reminders", data={"at_time": "06:45"})
    data = a.get("/backup/download").json()
    assert len(data["weight_entries"]) == 2 and data["weight_settings"]["unit"] == "kg"
    assert data["weight_reminders"] == [{"at_time": "06:45", "tz": "America/New_York"}]
    b = _user()
    up = lambda: b.post("/backup/restore", files={"file": ("b.json", io.BytesIO(json.dumps(data).encode()),  # noqa
                                                           "application/json")})
    assert up().status_code == 200
    got = b.get("/api/weight/entries").json()
    assert [(e["taken_at"], e["unit"]) for e in got] == [("2026-11-18T07:00", "kg"), ("2026-11-17T07:00", "lb")]
    assert b.get("/api/weight/settings").json()["goal"] == 78.0
    assert "Added 0 items" in up().text
