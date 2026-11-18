"""Calories module: link to the calorie counter app, today's numbers, backup."""
import io
import itertools
import json
import os
import tempfile

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.calories import service  # noqa: E402

db.init_db()
_n = itertools.count()


def _user():
    c = TestClient(app)
    c.username = f"cal{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": c.username, "password": "pw"})
    return c


def test_tile_page_and_default_link():
    c = _user()
    assert 'href="/calories"' in c.get("/").text and 'aria-label="Calories"' in c.get("/").text
    page = c.get("/calories").text
    assert 'href="/calories/open" target="_blank" rel="noopener"' in page
    assert "Opens lose-weight-fridl.fly.dev" in page and "no workout yet today" in page
    anon = TestClient(app)
    assert anon.get("/calories", follow_redirects=False).headers["location"] == "/login"


def test_change_and_reset_link():
    c, other = _user(), _user()
    r = c.post("/calories/link", data={"url": "my-calories.example.com"}, follow_redirects=False)
    assert r.status_code == 303
    assert 'value="https://my-calories.example.com"' in c.get("/calories").text
    assert f'value="{service.DEFAULT_URL}"' in other.get("/calories").text  # per user
    for bad in ("javascript:alert(1)", "ftp://x.com", "https:// spaced .com", ""):
        r = c.post("/calories/link", data={"url": bad})
        assert r.status_code == 422 and "Enter a web address" in r.text, bad
    c.post("/calories/link", data={"reset": "1"})
    assert f'value="{service.DEFAULT_URL}"' in c.get("/calories").text


def test_today_numbers_from_workout_and_weight():
    c = _user()
    c.post("/api/weight/entries", json={"taken_at": "2026-11-18T07:00", "weight": 180, "unit": "lb"})
    ex = c.post("/api/workout/exercises", json={"name": "Squat"}).json()
    s = c.post("/api/workout/sessions").json()
    se = c.post(f"/api/workout/sessions/{s['id']}/exercises", json={"exercise_id": ex["id"]}).json()["session_exercise_id"]
    day = s["started_at"][:10]
    c.put("/api/workout/sets", json=[{"uid": "abcdefgh-cal1", "session_exercise_id": se,
                                      "started_at": f"{day}T10:00:00", "ended_at": f"{day}T10:00:40",
                                      "reps": 5, "weight": 225}])
    c.post(f"/api/workout/sessions/{s['id']}/finish")
    c.put(f"/api/workout/sessions/{s['id']}/calories", json={"calories": 310})
    page = c.get("/calories").text
    assert "310" in page and "1 workout" in page and "180.0" in page


def test_backup_link():
    a = _user()
    a.post("/calories/link", data={"url": "https://cal.example.com"})
    data = a.get("/backup/download").json()
    assert data["calories_link"] == "https://cal.example.com"
    b = _user()
    b.post("/backup/restore", files={"file": ("b.json", io.BytesIO(json.dumps(data).encode()), "application/json")})
    assert 'value="https://cal.example.com"' in b.get("/calories").text
