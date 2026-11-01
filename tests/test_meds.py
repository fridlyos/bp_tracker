"""Medications module: manage, today checklist, history, reminders."""
import itertools
import os
import tempfile
from datetime import datetime, timezone

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, push, scheduler  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.meds import service  # noqa: E402

db.init_db()
_n = itertools.count()


def _user(tz="UTC"):
    c = TestClient(app)
    c.cookies.set("tz", tz)
    c.username = f"med{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": c.username, "password": "pw"})
    with db.connect() as conn:
        c.uid = conn.execute("SELECT id FROM users WHERE username = ?", (c.username,)).fetchone()[0]
    return c


def _add(c, name, times=(), dose=""):
    r = c.post("/meds/manage", data={"name": name, "dose": dose, "notes": "", "times": list(times) or [""]},
               follow_redirects=False)
    assert r.status_code == 303, r.text
    return next(m for m in c.get("/api/meds").json() if m["name"] == name)


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(push, "webpush", lambda info, data, **kw: calls.append(data))
    with db.connect() as conn:  # isolate: the job scans everyone's meds
        conn.execute("UPDATE meds SET active = 0")
        conn.commit()
    return calls


def _subscribe(c):
    c.post("/api/push/subscribe", json={"endpoint": f"https://web.push.apple.com/{c.username}",
                                         "keys": {"p256dh": "k", "auth": "a"}})


def test_add_edit_pause_delete():
    c = _user()
    m = _add(c, "Lisinopril", ["20:00", "08:00", "08:00"], "10 mg")
    assert m["times"] == ["08:00", "20:00"] and m["dose"] == "10 mg" and m["tz"] == "UTC"
    page = c.get("/meds/manage").text
    assert "Lisinopril" in page and "08:00, 20:00" in page
    r = c.post(f"/meds/manage/{m['id']}", data={"name": "Lisinopril", "dose": "20 mg", "times": ["09:00"]})
    m2 = c.get("/api/meds").json()[0]
    assert m2["dose"] == "20 mg" and m2["times"] == ["09:00"]
    c.post(f"/meds/manage/{m['id']}/active", data={"active": 0})
    assert c.get("/api/meds").json()[0]["active"] is False
    assert "Lisinopril" not in c.get("/meds").text.split("Scheduled")[-1]  # paused: not on Today
    c.post(f"/meds/manage/{m['id']}/delete")
    assert c.get("/api/meds").json() == []


def test_validation_rerenders_with_error():
    c = _user()
    r = c.post("/meds/manage", data={"name": "  ", "times": ["08:00"]})
    assert r.status_code == 422 and "Name is required" in r.text
    r = c.post("/meds/manage", data={"name": "X", "times": ["25:00"]})
    assert r.status_code == 422 and "Not a time: 25:00" in r.text
    assert c.get("/api/meds").json() == []


def test_today_checklist_take_skip_undo():
    c = _user()
    m = _add(c, "Metformin", ["00:00", "23:59"])
    prn = _add(c, "Ibuprofen", dose="200 mg")
    plan = c.get("/api/meds/today").json()
    assert [(s["time"], s["status"]) for s in plan["scheduled"]] == [("00:00", "due"), ("23:59", "upcoming")]
    slot = plan["scheduled"][0]["slot"]
    c.post("/meds/doses", data={"med_id": m["id"], "slot": slot, "status": "taken"})
    c.post("/meds/doses", data={"med_id": m["id"], "slot": plan["scheduled"][1]["slot"], "status": "skipped"})
    c.post("/meds/doses", data={"med_id": prn["id"]})  # as-needed dose
    c.post("/meds/doses", data={"med_id": prn["id"]})
    plan = c.get("/api/meds/today").json()
    assert [s["status"] for s in plan["scheduled"]] == ["taken", "skipped"]
    assert len(plan["as_needed"][0]["doses"]) == 2
    html = c.get("/meds").text
    assert "✓ Taken" in html and "Skipped" in html and "Log dose" in html
    # re-marking a slot replaces, never duplicates
    c.post("/meds/doses", data={"med_id": m["id"], "slot": slot, "status": "skipped"})
    plan = c.get("/api/meds/today").json()
    assert plan["scheduled"][0]["status"] == "skipped"
    c.post(f"/meds/doses/{plan['scheduled'][0]['dose']['id']}/undo")
    assert c.get("/api/meds/today").json()["scheduled"][0]["status"] == "due"
    hist = c.get("/meds/history").text
    assert "Metformin" in hist and "Ibuprofen" in hist and "as needed" in hist


def test_meds_are_per_user():
    a, b = _user(), _user()
    m = _add(a, "Private", ["08:00"])
    assert b.get("/api/meds").json() == []
    assert b.post("/api/meds/doses", json={"med_id": m["id"]}).status_code == 404
    b.post("/meds/doses", data={"med_id": m["id"]})
    b.post(f"/meds/manage/{m['id']}", data={"name": "Hacked"})
    b.post(f"/meds/manage/{m['id']}/delete")
    assert a.get("/api/meds").json()[0]["name"] == "Private"
    assert a.get("/api/meds/today").json()["scheduled"][0]["status"] in ("due", "upcoming")
    assert TestClient(app).get("/api/meds").status_code == 401
    assert TestClient(app).get("/meds", follow_redirects=False).headers["location"] == "/login"


def test_reminder_groups_meds_and_respects_marks(sent):
    c = _user("Europe/Berlin")
    _subscribe(c)
    a = _add(c, "Aspirin", ["08:00"], "100 mg")
    _add(c, "Vitamin D", ["08:00"])
    b = _add(c, "Statin", ["21:00"])
    # Berlin is UTC+1 on 2026-11-10: 08:00 local = 07:00 UTC
    scheduler.run_jobs_once(datetime(2026, 11, 10, 6, 59, tzinfo=timezone.utc))
    assert sent == []
    scheduler.run_jobs_once(datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc))
    assert len(sent) == 1 and "Time for 2 medications" in sent[0]
    assert "Aspirin (100 mg)" in sent[0] and "Vitamin D" in sent[0] and "/meds" in sent[0]
    scheduler.run_jobs_once(datetime(2026, 11, 10, 7, 5, tzinfo=timezone.utc))
    assert len(sent) == 1  # once per day
    # Statin marked taken early -> no 21:00 reminder
    with db.connect() as conn:
        service.log_dose(conn, c.uid, b["id"], "taken", datetime(2026, 11, 10, 20, 40), "2026-11-10T21:00")
    scheduler.run_jobs_once(datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc))
    assert len(sent) == 1
    # next day only Aspirin is unmarked -> single-med title
    with db.connect() as conn:
        vit = next(m for m in service.list_meds(conn, c.uid) if m.name == "Vitamin D")
        service.log_dose(conn, c.uid, vit.id, "skipped", datetime(2026, 11, 11, 7, 30), "2026-11-11T08:00")
    scheduler.run_jobs_once(datetime(2026, 11, 11, 7, 10, tzinfo=timezone.utc))
    assert len(sent) == 2 and "Time for Aspirin" in sent[1]
    # paused meds never remind
    c.post(f"/meds/manage/{a['id']}/active", data={"active": 0})
    scheduler.run_jobs_once(datetime(2026, 11, 12, 7, 0, tzinfo=timezone.utc))
    assert len(sent) == 3 and "Vitamin D" in sent[2] and "Aspirin" not in sent[2]


def test_launcher_shows_meds_icon():
    c = _user()
    assert 'class="app-icon" href="/meds" aria-label="Medications"' in c.get("/").text
    assert 'aria-label="Medications"' in c.get("/meds/manage").text  # module tab bar
