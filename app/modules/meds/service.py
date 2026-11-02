"""Medications module: meds + daily times, logged doses, reminders.

Times are local wall-clock 'HH:MM' in the med's `tz` (the browser's zone when
it was saved). A scheduled dose is identified by its slot 'YYYY-MM-DDTHH:MM';
as-needed doses have no slot. Every query filters by the acting user's id.
"""
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .schemas import Med, MedIn

logger = logging.getLogger("bp_tracker.meds")

REMINDER_WINDOW = timedelta(minutes=30)  # late-fire allowance after a restart


def _iso(dt: datetime) -> str:
    return dt.replace(second=0, microsecond=0).isoformat(timespec="minutes")


# --- meds

def _med(conn, row) -> Med:
    times = [r[0] for r in conn.execute(
        "SELECT at_time FROM med_times WHERE med_id = ? ORDER BY at_time", (row["id"],))]
    return Med(id=row["id"], name=row["name"], kind=row["kind"], dose=row["dose"], notes=row["notes"],
               tz=row["tz"], active=bool(row["active"]), times=times)


def list_meds(conn, user_id: int, active_only: bool = False) -> list[Med]:
    sql = "SELECT * FROM meds WHERE user_id = ?" + (" AND active = 1" if active_only else "")
    rows = conn.execute(sql + " ORDER BY active DESC, name COLLATE NOCASE", (user_id,)).fetchall()
    return [_med(conn, r) for r in rows]


def get_med(conn, user_id: int, med_id: int) -> Med | None:
    row = conn.execute("SELECT * FROM meds WHERE id = ? AND user_id = ?", (med_id, user_id)).fetchone()
    return _med(conn, row) if row else None


def save_med(conn, user_id: int, m: MedIn, tz: str, med_id: int | None = None) -> Med | None:
    """Create (med_id None) or update one of the user's meds. Times are
    replaced; a time that stays keeps its reminder state."""
    if med_id is None:
        med_id = conn.execute(
            "INSERT INTO meds (user_id, name, kind, dose, notes, tz) VALUES (?,?,?,?,?,?)",
            (user_id, m.name, m.kind, m.dose, m.notes, tz)).lastrowid
    else:
        cur = conn.execute("UPDATE meds SET name=?, kind=?, dose=?, notes=?, tz=? WHERE id=? AND user_id=?",
                           (m.name, m.kind, m.dose, m.notes, tz, med_id, user_id))
        if cur.rowcount == 0:
            return None
    marks = ",".join("?" * len(m.times))
    conn.execute(f"DELETE FROM med_times WHERE med_id = ? AND at_time NOT IN ({marks})",
                 (med_id, *m.times))
    conn.executemany("INSERT OR IGNORE INTO med_times (med_id, at_time) VALUES (?, ?)",
                     [(med_id, t) for t in m.times])
    conn.commit()
    return get_med(conn, user_id, med_id)


def set_active(conn, user_id: int, med_id: int, active: bool) -> None:
    conn.execute("UPDATE meds SET active = ? WHERE id = ? AND user_id = ?",
                 (int(active), med_id, user_id))
    conn.commit()


def delete_med(conn, user_id: int, med_id: int) -> None:
    """Delete a med and its whole dose history (pause keeps the history)."""
    conn.execute("DELETE FROM meds WHERE id = ? AND user_id = ?", (med_id, user_id))
    conn.commit()


# --- doses

def log_dose(conn, user_id: int, med_id: int, status: str, now: datetime,
             slot: str | None = None) -> bool:
    """Mark a scheduled slot taken/skipped (replacing an earlier mark), or log
    an as-needed dose (slot None). `now` is stored as logged_at (a past time
    when filling in an earlier day). False if the med isn't the user's."""
    if get_med(conn, user_id, med_id) is None:
        return False
    if slot is None:
        conn.execute("INSERT INTO med_doses (user_id, med_id, status, logged_at) VALUES (?,?,?,?)",
                     (user_id, med_id, status, _iso(now)))
    else:
        conn.execute(
            "INSERT INTO med_doses (user_id, med_id, scheduled_for, status, logged_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(med_id, scheduled_for)"
            " DO UPDATE SET status=excluded.status, logged_at=excluded.logged_at",
            (user_id, med_id, slot, status, _iso(now)))
    conn.commit()
    return True


def undo_dose(conn, user_id: int, dose_id: int) -> None:
    conn.execute("DELETE FROM med_doses WHERE id = ? AND user_id = ?", (dose_id, user_id))
    conn.commit()


def today_plan(conn, user_id: int, now: datetime) -> dict:
    return day_plan(conn, user_id, now.date(), now)


def day_plan(conn, user_id: int, day: date, now: datetime) -> dict:
    """One day's checklist (today, or a past day being filled in), from the
    current active meds and times. scheduled: one row per med x time with
    status taken / skipped / due (time passed) / upcoming. as_needed: active
    meds without times, with that day's logged doses."""
    today = day.isoformat()
    doses = {(r["med_id"], r["scheduled_for"]): r for r in conn.execute(
        "SELECT * FROM med_doses WHERE user_id = ? AND scheduled_for LIKE ?", (user_id, today + "T%"))}
    scheduled, as_needed = [], []
    for m in list_meds(conn, user_id, active_only=True):
        if not m.times:
            logged = conn.execute(
                "SELECT * FROM med_doses WHERE user_id = ? AND med_id = ? AND scheduled_for IS NULL"
                " AND logged_at LIKE ? ORDER BY logged_at", (user_id, m.id, today + "T%")).fetchall()
            as_needed.append({"med": m, "doses": [dict(d) for d in logged]})
            continue
        for t in m.times:
            slot = f"{today}T{t}"
            d = doses.get((m.id, slot))
            status = d["status"] if d else ("due" if slot <= _iso(now) else "upcoming")
            scheduled.append({"med": m, "time": t, "slot": slot, "status": status,
                              "dose": dict(d) if d else None})
    scheduled.sort(key=lambda x: (x["time"], x["med"].name.lower()))
    return {"scheduled": scheduled, "as_needed": as_needed}


def history(conn, user_id: int, days: int = 30, today: date | None = None) -> list[tuple[str, list[dict]]]:
    """Logged doses grouped by day (newest first) for the last `days` days."""
    since = ((today or date.today()) - timedelta(days=days - 1)).isoformat()
    rows = conn.execute(
        "SELECT d.*, m.name, m.kind, m.dose FROM med_doses d JOIN meds m ON m.id = d.med_id"
        " WHERE d.user_id = ? AND COALESCE(d.scheduled_for, d.logged_at) >= ?"
        " ORDER BY COALESCE(d.scheduled_for, d.logged_at) DESC, m.name",
        (user_id, since)).fetchall()
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["scheduled_for"] or r["logged_at"])[:10], []).append(dict(r))
    return list(groups.items())


# --- reminders

def _local(now_utc: datetime, tz: str) -> datetime:
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return now_utc.astimezone(zone).replace(tzinfo=None)


def due_reminders(conn, now_utc: datetime) -> list[dict]:
    """Active med times whose moment has come today (in the med's zone, within
    the window), not yet reminded today and with no dose marked for the slot."""
    due = []
    rows = conn.execute(
        "SELECT t.id AS time_id, t.at_time, t.last_reminded, m.id AS med_id, m.user_id,"
        " m.name, m.kind, m.dose, m.tz FROM med_times t JOIN meds m ON m.id = t.med_id"
        " WHERE m.active = 1").fetchall()
    for r in rows:
        now = _local(now_utc, r["tz"])
        at = datetime.combine(now.date(), datetime.strptime(r["at_time"], "%H:%M").time())
        if r["last_reminded"] == now.date().isoformat() or not (at <= now < at + REMINDER_WINDOW):
            continue
        slot = _iso(at)
        if conn.execute("SELECT 1 FROM med_doses WHERE med_id = ? AND scheduled_for = ?",
                        (r["med_id"], slot)).fetchone():
            continue  # already marked (possibly early)
        due.append({**dict(r), "slot": slot, "local_date": now.date().isoformat()})
    return due


def _count_label(items: list[dict]) -> str:
    """'2 medications', '1 supplement', '1 medication and 2 supplements'."""
    parts = []
    for kind in ("medication", "supplement"):
        n = sum(1 for i in items if i["kind"] == kind)
        if n:
            parts.append(f"{n} {kind}{'' if n == 1 else 's'}")
    return " and ".join(parts)


def send_reminders(conn, now_utc: datetime) -> None:
    """Scheduler job: one push per user listing every med due now."""
    from ... import push
    by_user: dict[int, list[dict]] = {}
    for r in due_reminders(conn, now_utc):
        by_user.setdefault(r["user_id"], []).append(r)
    for user_id, items in by_user.items():
        names = [f"{i['name']} ({i['dose']})" if i["dose"] else i["name"] for i in items]
        title = f"Time for {items[0]['name']}" if len(items) == 1 else f"Time for {_count_label(items)}"
        n = push.send_to_user(conn, user_id, title, ", ".join(names) + " · tap to mark taken", "/meds")
        logger.info("med reminder for user %s (%d med(s)) sent to %d device(s)", user_id, len(items), n)
        conn.executemany("UPDATE med_times SET last_reminded = ? WHERE id = ?",
                         [(i["local_date"], i["time_id"]) for i in items])
        conn.commit()


def adopt_zone(conn, user_id: int, tz: str) -> None:
    """Rows saved as 'UTC' while the tz cookie was misread (every browser
    looked like UTC) move to the browser's real zone once we know it."""
    if tz != "UTC":
        if conn.execute("UPDATE meds SET tz = ? WHERE user_id = ? AND tz = 'UTC'", (tz, user_id)).rowcount:
            conn.commit()
