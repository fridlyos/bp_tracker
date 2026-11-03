"""Reminder/med zones saved as 'UTC' while the tz cookie wasn't decoded
(before 9d49fbe) must be corrected once the browser reports the real zone."""
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


def _legacy_user():
    """A user whose reminder + med were saved while the server read every zone as UTC."""
    c = TestClient(app)
    c.cookies.set("tz", "UTC")
    name = f"tzr{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": name, "password": "pw"})
    c.post("/api/push/subscribe", json={"endpoint": f"https://web.push.apple.com/{name}",
                                         "keys": {"p256dh": "k", "auth": "a"}})
    c.post("/bp/reminders", data={"at_time": "08:00"})
    c.post("/meds/manage", data={"name": "Aspirin", "kind": "medication", "times": ["09:00"]})
    with db.connect() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]
        assert conn.execute("SELECT tz FROM bp_reminders WHERE user_id = ?", (uid,)).fetchone()[0] == "UTC"
    c.uid = uid
    return c


def _zones(uid):
    with db.connect() as conn:
        return (conn.execute("SELECT tz FROM bp_reminders WHERE user_id = ?", (uid,)).fetchone()[0],
                conn.execute("SELECT tz FROM meds WHERE user_id = ?", (uid,)).fetchone()[0])


def test_stale_utc_zones_fire_at_the_users_local_time_after_repair(sent):
    c = _legacy_user()
    # the browser now sends the real zone (cookie as the fixed base.html writes it)
    c.cookies.set("tz", "America/New_York")
    c.get("/")
    assert _zones(c.uid) == ("America/New_York", "America/New_York")
    # 08:00 UTC is 03:00 in New York (EST since Nov 1): must NOT fire
    scheduler.run_jobs_once(datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc))
    assert sent == []
    # 08:00 New York = 13:00 UTC: BP reminder fires; 09:00 = 14:00 UTC: med
    scheduler.run_jobs_once(datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc))
    assert len(sent) == 1 and "blood pressure" in sent[0]
    scheduler.run_jobs_once(datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc))
    assert len(sent) == 2 and "Aspirin" in sent[1]


def test_old_percent_encoded_cookie_also_repairs(sent):
    c = _legacy_user()
    c.cookies.set("tz", "Europe%2FBerlin")  # cookie written by the pre-fix page
    c.get("/bp")
    assert _zones(c.uid) == ("Europe/Berlin", "Europe/Berlin")


def test_real_zones_are_left_alone():
    c = _legacy_user()
    c.cookies.set("tz", "Asia/Tokyo")
    c.post("/bp/reminders", data={"at_time": "21:00"})  # saved correctly as Tokyo
    c.get("/")
    # later the phone reports another zone (travel): correct zones are not rewritten
    c.cookies.set("tz", "America/Chicago")
    c.get("/")
    with db.connect() as conn:
        zones = dict(conn.execute("SELECT at_time, tz FROM bp_reminders WHERE user_id = ?", (c.uid,)).fetchall())
    assert zones == {"08:00": "Asia/Tokyo", "21:00": "Asia/Tokyo"}


def test_unknown_or_utc_cookie_changes_nothing():
    c = _legacy_user()
    for value in ("UTC", "Not/A_Zone", ""):
        c.cookies.set("tz", value)
        c.get("/")
    assert _zones(c.uid) == ("UTC", "UTC")
