"""Backup & restore: per-user JSON export/merge-restore, admin DB download."""
import io
import json
import sqlite3

from tests.test_meds import _add, _user


def _upload(c, data):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return c.post("/backup/restore", files={"file": ("b.json", io.BytesIO(raw), "application/json")})


def test_export_then_restore_into_another_account_and_again():
    a = _user("America/New_York")
    a.post("/api/bp/readings", json={"taken_at": "2026-10-30T08:15", "systolic": 128, "diastolic": 82,
                                     "pulse": 70, "note": "after coffee"})
    assert a.post("/bp/reminders", data={"at_time": "07:30"}).status_code < 400
    m = _add(a, "Lisinopril", ["08:00"], dose="10 mg")
    prn = _add(a, "Ibuprofen")
    a.post("/meds/doses", data={"med_id": m["id"], "slot": "2026-10-30T08:00", "status": "taken",
                                "day": "2026-10-30"})
    a.post("/meds/doses", data={"med_id": prn["id"], "day": "2026-10-30", "at": "21:15"})

    r = a.get("/backup/download")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["format"] == "my-apps-backup" and len(data["bp_readings"]) == 1
    assert data["bp_reminders"] == [{"at_time": "07:30", "tz": "America/New_York"}]
    assert {x["name"] for x in data["meds"]} == {"Lisinopril", "Ibuprofen"}
    assert "user_id" not in json.dumps(data)

    b = _user()
    r = _upload(b, data)
    assert r.status_code == 200 and "Restore finished" in r.text
    assert b.get("/api/bp/readings").json()[0]["note"] == "after coffee"
    assert {x["name"] for x in b.get("/api/meds").json()} == {"Lisinopril", "Ibuprofen"}
    again = b.get("/backup/download").json()
    for k in ("bp_readings", "bp_reminders", "meds"):
        assert again[k] == data[k]
    # restoring twice adds nothing
    assert "Added 0 items" in _upload(b, data).text
    assert b.get("/backup/download").json()["meds"] == data["meds"]


def test_restore_rejects_junk_and_skips_bad_rows():
    c = _user()
    assert _upload(c, b"not json").status_code == 422
    assert "a My Apps backup" in _upload(c, {"hello": 1}).text
    r = _upload(c, {"format": "my-apps-backup", "version": 1, "bp_readings": [
        {"taken_at": "2026-10-30T08:00", "systolic": 80, "diastolic": 120},  # sys < dia
        {"taken_at": "2026-10-30T09:00", "systolic": 120, "diastolic": 80}], "meds": [{"name": ""}]})
    assert "Added 1 item," in r.text and "2 couldn" in r.text


def test_backup_page_and_db_download_admin_only():
    c = _user()
    assert "Download backup" in c.get("/backup").text
    assert "Whole database" not in c.get("/backup").text
    assert c.get("/backup/database", follow_redirects=False).status_code == 303
    assert "Backup &amp; restore" in c.get("/").text
    admin = _user()
    from app import db
    with db.connect() as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin.uid,))
        conn.commit()
    r = admin.get("/backup/database")
    assert r.status_code == 200 and r.content[:16] == b"SQLite format 3\x00"
    conn = sqlite3.connect(":memory:")
    conn.deserialize(r.content)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 2
