"""Per-user backup: export everything a user owns as JSON, and restore it.

Restore merges into the acting user's data and never deletes or overwrites:
rows that already exist are skipped, invalid rows are counted and skipped.
So restoring the same file twice is harmless, and a backup can be restored
into a different account. The admin can also download the whole SQLite file.
"""
import re
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from .modules.bp.schemas import ReadingIn
from .modules.meds.schemas import MedIn

FORMAT = "my-apps-backup"
VERSION = 1
_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def export_user(conn, user_id: int, username: str) -> dict:
    q = lambda sql: [dict(r) for r in conn.execute(sql, (user_id,))]  # noqa: E731
    meds = q("SELECT id, name, kind, dose, notes, tz, active FROM meds WHERE user_id = ? ORDER BY id")
    for m in meds:
        m["active"] = bool(m["active"])
        m["times"] = [r[0] for r in conn.execute(
            "SELECT at_time FROM med_times WHERE med_id = ? ORDER BY at_time", (m["id"],))]
        m["doses"] = [dict(r) for r in conn.execute(
            "SELECT scheduled_for, status, logged_at FROM med_doses WHERE med_id = ? AND user_id = ?"
            " ORDER BY COALESCE(scheduled_for, logged_at)", (m["id"], user_id))]
        del m["id"]
    return {
        "format": FORMAT, "version": VERSION, "username": username,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bp_readings": q("SELECT taken_at, systolic, diastolic, pulse, note FROM readings"
                         " WHERE user_id = ? ORDER BY taken_at"),
        "bp_reminders": q("SELECT at_time, tz FROM bp_reminders WHERE user_id = ? ORDER BY at_time"),
        "meds": meds,
    }


def _tz(name) -> str:
    try:
        return ZoneInfo(name).key if isinstance(name, str) and name else "UTC"
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"


def restore_user(conn, user_id: int, data) -> dict:
    """Merge a backup into the user's data. Returns counts of added /
    skipped (already there) / invalid rows. Raises ValueError if the file
    isn't a backup at all."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("This file isn't a My Apps backup.")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise ValueError("This backup was made by a newer version of the app.")
    n = {"added": 0, "skipped": 0, "invalid": 0}

    def rows(key):
        v = data.get(key)
        return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []

    for r in rows("bp_readings"):
        try:
            x = ReadingIn(**{k: r.get(k) for k in ("taken_at", "systolic", "diastolic", "pulse", "note")})
            assert x.taken_at is not None
        except (ValidationError, AssertionError, TypeError):
            n["invalid"] += 1
            continue
        at = x.taken_at.replace(tzinfo=None).isoformat(timespec="minutes")
        if conn.execute("SELECT 1 FROM readings WHERE user_id = ? AND taken_at = ? AND systolic = ?"
                        " AND diastolic = ? AND pulse IS ?",
                        (user_id, at, x.systolic, x.diastolic, x.pulse)).fetchone():
            n["skipped"] += 1
            continue
        conn.execute("INSERT INTO readings (user_id, taken_at, systolic, diastolic, pulse, note)"
                     " VALUES (?,?,?,?,?,?)", (user_id, at, x.systolic, x.diastolic, x.pulse, x.note))
        n["added"] += 1

    for r in rows("bp_reminders"):
        if not (isinstance(r.get("at_time"), str) and _HHMM.match(r["at_time"])):
            n["invalid"] += 1
            continue
        cur = conn.execute("INSERT OR IGNORE INTO bp_reminders (user_id, at_time, tz) VALUES (?,?,?)",
                           (user_id, r["at_time"], _tz(r.get("tz"))))
        n["added" if cur.rowcount else "skipped"] += 1

    for r in rows("meds"):
        try:
            m = MedIn(name=r.get("name"), kind=r.get("kind") or "medication", dose=r.get("dose"),
                      notes=r.get("notes"), times=r.get("times") or [])
        except (ValidationError, TypeError):
            n["invalid"] += 1
            continue
        row = conn.execute("SELECT id FROM meds WHERE user_id = ? AND name = ? AND kind = ?",
                           (user_id, m.name, m.kind)).fetchone()
        if row:
            med_id = row[0]
            n["skipped"] += 1
        else:
            med_id = conn.execute(
                "INSERT INTO meds (user_id, name, kind, dose, notes, tz, active) VALUES (?,?,?,?,?,?,?)",
                (user_id, m.name, m.kind, m.dose, m.notes, _tz(r.get("tz")),
                 0 if r.get("active") is False else 1)).lastrowid
            n["added"] += 1
            conn.executemany("INSERT OR IGNORE INTO med_times (med_id, at_time) VALUES (?, ?)",
                             [(med_id, t) for t in m.times])
        for d in r.get("doses") if isinstance(r.get("doses"), list) else []:
            slot, logged = (d.get("scheduled_for"), d.get("logged_at")) if isinstance(d, dict) else (0, 0)
            if (d.get("status") if isinstance(d, dict) else None) not in ("taken", "skipped") \
                    or not (isinstance(logged, str) and _MINUTE.match(logged)) \
                    or not (slot is None or (isinstance(slot, str) and _MINUTE.match(slot))):
                n["invalid"] += 1
                continue
            if slot is None and conn.execute(
                    "SELECT 1 FROM med_doses WHERE med_id = ? AND scheduled_for IS NULL AND logged_at = ?",
                    (med_id, logged)).fetchone():
                n["skipped"] += 1
                continue
            cur = conn.execute("INSERT OR IGNORE INTO med_doses (user_id, med_id, scheduled_for, status,"
                               " logged_at) VALUES (?,?,?,?,?)", (user_id, med_id, slot, d["status"], logged))
            n["added" if cur.rowcount else "skipped"] += 1
    conn.commit()
    return n


def copy_database(conn, dest_path: str) -> None:
    """Consistent snapshot of the whole DB (safe while the app is running)."""
    out = sqlite3.connect(dest_path)
    try:
        conn.backup(out)
    finally:
        out.close()
