"""BP reminders are not sent when a reading for that reminder is already logged."""
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

db.init_db()
_n = itertools.count()


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(push, "webpush", lambda info, data, **kw: calls.append(data))
    with db.connect() as conn:  # the jobs scan everyone: isolate
        conn.execute("DELETE FROM bp_reminders")
        conn.execute("UPDATE meds SET active = 0")
        conn.commit()
    return calls


def _user(*times):
    c = TestClient(app)
    c.cookies.set("tz", "UTC")
    name = f"skip{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": name, "password": "pw"})
    c.post("/api/push/subscribe", json={"endpoint": f"https://web.push.apple.com/{name}",
                                         "keys": {"p256dh": "k", "auth": "a"}})
    for t in times:
        c.post("/bp/reminders", data={"at_time": t})
    return c


def _read(c, taken_at):
    assert c.post("/api/bp/readings", json={"taken_at": taken_at, "systolic": 120,
                                            "diastolic": 80}).status_code == 201


def _tick(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    scheduler.run_jobs_once(datetime(2026, 11, day, h, m, tzinfo=timezone.utc))


def test_early_morning_reading_answers_the_morning_reminder(sent):
    c = _user("08:00")
    _read(c, "2026-11-10T05:30")  # 2.5h before: the old 2-hour rule still notified
    _tick(10, "08:00")
    assert sent == []


def test_no_reading_today_sends(sent):
    c = _user("08:00")
    _read(c, "2026-11-09T22:00")  # last night doesn't count for today
    _tick(10, "08:00")
    assert len(sent) == 1


def test_late_morning_reading_does_not_silence_evening(sent):
    c = _user("08:00", "20:00")
    _tick(11, "08:00")
    assert len(sent) == 1              # nothing logged yet
    _read(c, "2026-11-11T09:00")       # answers the 08:00 reminder (before 14:00 half-way)
    _tick(11, "20:00")
    assert len(sent) == 2              # evening still reminds


def test_afternoon_reading_answers_the_evening_reminder(sent):
    c = _user("08:00", "20:00")
    _read(c, "2026-11-12T07:45")
    _tick(12, "08:00")
    assert sent == []                  # morning answered
    _read(c, "2026-11-12T17:30")       # after 14:00: counts for 20:00
    _tick(12, "20:00")
    assert sent == []


def test_future_dated_reading_does_not_count(sent):
    c = _user("08:00")
    _read(c, "2026-11-13T12:00")       # logged ahead of time (typo / planned)
    _tick(13, "08:00")
    assert len(sent) == 1


def test_reminders_still_fire_once_per_day(sent):
    _user("08:00")
    _tick(14, "08:00")
    _tick(14, "08:05")
    assert len(sent) == 1


def test_marked_med_dose_is_not_reminded(sent):
    # medications already skip a marked slot; keep that guaranteed
    c = _user()
    c.post("/meds/manage", data={"name": "Aspirin", "kind": "medication", "times": ["09:00"]})
    med = c.get("/api/meds").json()[0]
    c.post("/api/meds/doses", json={"med_id": med["id"], "status": "taken", "slot": "2026-11-15T09:00"})
    _tick(15, "09:00")
    assert sent == []
    _tick(16, "09:00")                 # next day, not marked -> reminded
    assert len(sent) == 1 and "Aspirin" in sent[0]
