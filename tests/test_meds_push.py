"""Medication reminders: enable-notifications UI and zones saved as UTC by the old tz bug."""
from app import db
from tests.test_meds import _add, _subscribe, _user


def test_notifications_can_be_enabled_from_meds():
    c = _user()
    _add(c, "Aspirin", ["08:00"])
    assert "data-push-enable" in c.get("/meds/manage").text
    assert "Get reminded" in c.get("/meds").text  # no device yet
    _subscribe(c)
    assert "Get reminded" not in c.get("/meds").text
    assert "data-push-enable" in c.get("/bp/settings").text  # BP page still has it


def test_utc_rows_move_to_browser_zone():
    c = _user("UTC")
    _add(c, "Aspirin", ["08:00"])
    c.post("/bp/reminders", data={"at_time": "07:30"})
    c.cookies.set("tz", "America/New_York")
    c.get("/meds")
    c.get("/bp")
    with db.connect() as conn:
        assert conn.execute("SELECT tz FROM meds WHERE user_id = ?", (c.uid,)).fetchone()[0] == "America/New_York"
        assert conn.execute("SELECT tz FROM bp_reminders WHERE user_id = ?",
                            (c.uid,)).fetchone()[0] == "America/New_York"
    # a real non-UTC zone is never rewritten
    c.cookies.set("tz", "Europe/Berlin")
    c.get("/meds")
    with db.connect() as conn:
        assert conn.execute("SELECT tz FROM meds WHERE user_id = ?", (c.uid,)).fetchone()[0] == "America/New_York"
