"""Push subscriptions + BP reminders (push service mocked)."""
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
from app.modules.bp import service  # noqa: E402

db.init_db()
_n = itertools.count()
SUB = {"endpoint": "https://web.push.apple.com/abc", "keys": {"p256dh": "BPk", "auth": "au"}}


def _user(tz="UTC"):
    c = TestClient(app)
    c.cookies.set("tz", tz)
    c.username = f"rem{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": c.username, "password": "pw"})
    with db.connect() as conn:
        c.uid = conn.execute("SELECT id FROM users WHERE username = ?", (c.username,)).fetchone()[0]
    return c


@pytest.fixture
def sent(monkeypatch):
    """Capture pushes instead of calling Apple/Google."""
    calls = []
    monkeypatch.setattr(push, "webpush", lambda info, data, **kw: calls.append((info["endpoint"], data, kw)))
    return calls


def _clear_reminders():
    # due_reminders() scans everyone's reminders; isolate each test
    with db.connect() as conn:
        conn.execute("DELETE FROM bp_reminders")
        conn.commit()


def test_vapid_key_generated_once_and_stable():
    with db.connect() as conn:
        _, k1 = push.vapid(conn)
        _, k2 = push.vapid(conn)
    assert k1 == k2 and len(k1) == 87  # 65-byte uncompressed P-256 point, base64url
    c = _user()
    assert c.get("/api/push/key").json() == {"key": k1}
    assert TestClient(app).get("/api/push/key").status_code == 401


def test_subscribe_test_and_unsubscribe(sent):
    c = _user()
    assert c.post("/api/push/subscribe", json=SUB).json() == {"devices": 1}
    assert c.post("/api/push/subscribe", json=SUB).json() == {"devices": 1}  # idempotent
    assert c.post("/api/push/test").json() == {"sent": 1}
    endpoint, data, kw = sent[-1]
    assert endpoint == SUB["endpoint"] and '"title": "Notifications work"' in data
    assert kw["vapid_claims"]["sub"].startswith(("https://", "mailto:"))
    assert c.post("/api/push/unsubscribe", json={"endpoint": SUB["endpoint"]}).json() == {"devices": 0}
    bad = dict(SUB, endpoint="http://insecure.example/x")
    assert c.post("/api/push/subscribe", json=bad).status_code == 422


def test_expired_subscription_is_dropped(monkeypatch):
    c = _user()
    c.post("/api/push/subscribe", json=dict(SUB, endpoint="https://web.push.apple.com/gone"))

    class Resp:
        status_code = 410

    def boom(*a, **kw):
        raise push.WebPushException("gone", response=Resp())
    monkeypatch.setattr(push, "webpush", boom)
    assert c.post("/api/push/test").json() == {"sent": 0}
    with db.connect() as conn:
        assert push.device_count(conn, c.uid) == 0


def test_reminder_crud_via_settings():
    c = _user("America/New_York")
    r = c.post("/bp/reminders", data={"at_time": "08:00"}, follow_redirects=False)
    assert r.status_code == 303
    c.post("/bp/reminders", data={"at_time": "20:30"})
    dup = c.post("/bp/reminders", data={"at_time": "08:00"})
    assert dup.status_code == 422 and "already have a reminder at 08:00" in dup.text
    assert c.post("/bp/reminders", data={"at_time": "nope"}).status_code == 422
    with db.connect() as conn:
        rows = service.list_reminders(conn, c.uid)
    assert [(x["at_time"], x["tz"]) for x in rows] == [("08:00", "America/New_York"), ("20:30", "America/New_York")]
    page = c.get("/bp/settings").text
    assert "08:00" in page and "20:30" in page and "data-push-enable" in page
    other = _user()
    other.post(f"/bp/reminders/{rows[0]['id']}/delete")  # not theirs: no effect
    c.post(f"/bp/reminders/{rows[0]['id']}/delete")
    with db.connect() as conn:
        assert [x["at_time"] for x in service.list_reminders(conn, c.uid)] == ["20:30"]


def test_reminder_fires_once_in_its_timezone(sent):
    _clear_reminders()
    c = _user("America/New_York")
    c.post("/api/push/subscribe", json=dict(SUB, endpoint="https://web.push.apple.com/ny"))
    c.post("/bp/reminders", data={"at_time": "08:00"})
    # 2026-11-02 is after the DST change: New York is UTC-5, so 08:00 local = 13:00 UTC
    scheduler.run_jobs_once(datetime(2026, 11, 2, 12, 59, tzinfo=timezone.utc))
    assert sent == []
    scheduler.run_jobs_once(datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc))
    assert len(sent) == 1 and "/bp" in sent[0][1]
    scheduler.run_jobs_once(datetime(2026, 11, 2, 13, 1, tzinfo=timezone.utc))
    assert len(sent) == 1  # once per day
    scheduler.run_jobs_once(datetime(2026, 11, 3, 13, 20, tzinfo=timezone.utc))
    assert len(sent) == 2  # next day, late but inside the 30-min window
    scheduler.run_jobs_once(datetime(2026, 11, 4, 13, 31, tzinfo=timezone.utc))
    assert len(sent) == 2  # too late (missed, e.g. machine was down): skip, don't spam


def test_reminder_skipped_if_logged_recently(sent):
    _clear_reminders()
    c = _user()
    c.post("/api/push/subscribe", json=dict(SUB, endpoint="https://web.push.apple.com/skip"))
    c.post("/bp/reminders", data={"at_time": "09:00"})
    c.post("/api/bp/readings", json={"taken_at": "2026-11-05T07:30", "systolic": 120, "diastolic": 80})
    scheduler.run_jobs_once(datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc))
    assert sent == []  # logged 07:30, within 2h of 09:00
    scheduler.run_jobs_once(datetime(2026, 11, 5, 9, 5, tzinfo=timezone.utc))
    assert sent == []  # and marked done for today
    c.post("/api/bp/readings", json={"taken_at": "2026-11-05T21:00", "systolic": 120, "diastolic": 80})
    scheduler.run_jobs_once(datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc))
    assert len(sent) == 1  # last reading was 12h ago


def test_failing_job_does_not_stop_others(monkeypatch, caplog):
    from app.modules import MODULES
    seen = []
    bp = MODULES[0]

    def bad(conn, now):
        raise RuntimeError("x")

    def good(conn, now):
        seen.append(now)
    monkeypatch.setattr(type(bp), "jobs", property(lambda self: (bad, good)), raising=False)
    scheduler.run_jobs_once(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert len(seen) == 1 and "job bp.bad failed" in caplog.text
