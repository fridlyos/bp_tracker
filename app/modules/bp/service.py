"""Blood pressure module: readings, chart stats and reports.

Each function takes an open sqlite connection and the acting user's id, and
every query filters by it.
"""
import logging
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .schemas import Reading, ReadingIn

logger = logging.getLogger("bp_tracker.bp")


# --- readings
# taken_at is stored as naive local ISO text at minute precision, so string
# comparison == chronological order and the (user_id, taken_at) index serves
# every range query.

def _iso(dt: datetime) -> str:
    return dt.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def _reading(row) -> Reading:
    return Reading(**{k: row[k] for k in row.keys() if k != "user_id"})


def add_reading(conn, user_id: int, r: ReadingIn) -> Reading:
    """Insert one reading. r.taken_at must already be a naive local datetime."""
    if r.taken_at is None:
        raise ValueError("taken_at required")
    cur = conn.execute(
        "INSERT INTO readings (user_id, taken_at, systolic, diastolic, pulse, note)"
        " VALUES (?,?,?,?,?,?)",
        (user_id, _iso(r.taken_at), r.systolic, r.diastolic, r.pulse, r.note),
    )
    conn.commit()
    return get_reading(conn, user_id, cur.lastrowid)


def get_reading(conn, user_id: int, reading_id: int) -> Reading | None:
    row = conn.execute("SELECT * FROM readings WHERE id = ? AND user_id = ?",
                       (reading_id, user_id)).fetchone()
    return _reading(row) if row else None


def list_readings(conn, user_id: int, start: date | None = None,
                  end: date | None = None) -> list[Reading]:
    """Readings in [start, end] (whole days, both inclusive), newest first."""
    sql, args = "SELECT * FROM readings WHERE user_id = ?", [user_id]
    if start is not None:
        sql += " AND taken_at >= ?"
        args.append(start.isoformat())
    if end is not None:
        sql += " AND taken_at < ?"
        args.append((end + timedelta(days=1)).isoformat())
    sql += " ORDER BY taken_at DESC, id DESC"
    return [_reading(r) for r in conn.execute(sql, args)]


def readings_by_day(conn, user_id: int) -> list[tuple[str, list[Reading]]]:
    """All readings grouped by day ('YYYY-MM-DD'), newest day first."""
    groups: dict[str, list[Reading]] = {}
    for r in list_readings(conn, user_id):
        groups.setdefault(r.taken_at[:10], []).append(r)
    return list(groups.items())


def delete_reading(conn, user_id: int, reading_id: int) -> bool:
    cur = conn.execute("DELETE FROM readings WHERE id = ? AND user_id = ?",
                       (reading_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def purge_before(conn, user_id: int, before: date, dry_run: bool = False) -> int:
    """Delete (or with dry_run just count) readings taken strictly before `before`."""
    where = "WHERE user_id = ? AND taken_at < ?"
    args = (user_id, before.isoformat())
    if dry_run:
        return conn.execute(f"SELECT COUNT(*) FROM readings {where}", args).fetchone()[0]
    cur = conn.execute(f"DELETE FROM readings {where}", args)
    conn.commit()
    logger.info("user %s purged %d reading(s) before %s", user_id, cur.rowcount, before)
    return cur.rowcount


# --- stats

RANGE_DAYS = {"week": 7, "month": 30, "3m": 90}


def daily_averages(conn, user_id: int, start: date, end: date) -> list[dict]:
    """Per-day averages in [start, end] (inclusive), oldest first. Days without
    readings are omitted. Pulse averages only readings that have a pulse."""
    rows = conn.execute(
        "SELECT substr(taken_at, 1, 10) AS day,"
        " AVG(systolic) AS systolic, AVG(diastolic) AS diastolic,"
        " AVG(pulse) AS pulse, COUNT(*) AS count"
        " FROM readings WHERE user_id = ? AND taken_at >= ? AND taken_at < ?"
        " GROUP BY day ORDER BY day",
        (user_id, start.isoformat(), (end + timedelta(days=1)).isoformat()),
    )
    return [{"x": r["day"], "systolic": _r1(r["systolic"]), "diastolic": _r1(r["diastolic"]),
             "pulse": _r1(r["pulse"]), "count": r["count"]} for r in rows]


def _r1(v):
    return None if v is None else round(v, 1)


def _summary(conn, user_id: int, start: date, end: date) -> dict:
    r = conn.execute(
        "SELECT AVG(systolic) AS systolic, AVG(diastolic) AS diastolic,"
        " AVG(pulse) AS pulse, COUNT(*) AS count"
        " FROM readings WHERE user_id = ? AND taken_at >= ? AND taken_at < ?",
        (user_id, start.isoformat(), (end + timedelta(days=1)).isoformat()),
    ).fetchone()
    return {"systolic": _r1(r["systolic"]), "diastolic": _r1(r["diastolic"]),
            "pulse": _r1(r["pulse"]), "count": r["count"]}


def stats(conn, user_id: int, range_: str, today: date, day: date | None = None) -> dict:
    """Chart data.

    day          -> individual readings of `day` (default today), oldest first;
                    each point's x is the full 'YYYY-MM-DDTHH:MM'.
    week/month/3m-> daily averages over the last 7/30/90 days ending today;
                    x is 'YYYY-MM-DD'.
    """
    if range_ == "day":
        start = end = day or today
        points = [{"x": r.taken_at, "systolic": r.systolic, "diastolic": r.diastolic,
                   "pulse": r.pulse, "count": 1}
                  for r in reversed(list_readings(conn, user_id, start, end))]
    elif range_ in RANGE_DAYS:
        end, start = today, today - timedelta(days=RANGE_DAYS[range_] - 1)
        points = daily_averages(conn, user_id, start, end)
    else:
        raise ValueError(f"unknown range {range_!r}")
    return {"range": range_, "start": start.isoformat(), "end": end.isoformat(),
            "points": points, "summary": _summary(conn, user_id, start, end)}


# --- reports (weekly / monthly summaries)

# Owner's BP zones, mirrored from trends.html: (upper bound inclusive, label, css token).
SYS_ZONES = [(120, "≤120", "--z-good"), (129, "121–129", "--z-sys-ok"),
             (139, "130–139", "--z-warn"), (None, "140+", "--z-high")]
DIA_ZONES = [(80, "≤80", "--z-good"), (90, "81–90", "--z-warn"), (None, "91+", "--z-high")]
REPORT_PERIODS = ("week", "month")


def _period_bounds(day: date, period: str) -> tuple[date, date]:
    """First and last day of the week (Monday start) or calendar month holding `day`."""
    if period == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    start = day.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


def _period_label(start: date, end: date, period: str) -> str:
    if period == "month":
        return start.strftime("%B %Y")
    if start.year != end.year:
        return f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"
    return f"{start:%b} {start.day}–{end.day}, {end.year}"


def _min_avg_max(values: list[int]) -> dict | None:
    if not values:
        return None
    return {"avg": round(sum(values) / len(values), 1), "min": min(values), "max": max(values)}


def _zone_counts(values: list[int], zones) -> list[dict]:
    counts = []
    for i, (hi, label, token) in enumerate(zones):
        lo = zones[i - 1][0] if i else None
        n = sum(1 for v in values if (lo is None or v > lo) and (hi is None or v <= hi))
        counts.append({"label": label, "token": token, "count": n})
    return counts


def reports(conn, user_id: int, period: str) -> list[dict]:
    """One summary per week (Mon–Sun) or calendar month that has readings,
    newest first: reading count, avg/min/max of systolic, diastolic and pulse
    (pulse over readings that have one), and readings per BP zone."""
    if period not in REPORT_PERIODS:
        raise ValueError(f"unknown period {period!r}")
    groups: dict[date, list[Reading]] = {}
    for r in list_readings(conn, user_id):  # newest first, so groups come out newest first
        start, _ = _period_bounds(date.fromisoformat(r.taken_at[:10]), period)
        groups.setdefault(start, []).append(r)
    out = []
    for start, rs in groups.items():
        _, end = _period_bounds(start, period)
        sys_ = [r.systolic for r in rs]
        dia = [r.diastolic for r in rs]
        out.append({
            "start": start.isoformat(), "end": end.isoformat(),
            "label": _period_label(start, end, period),
            "count": len(rs),
            "days": len({r.taken_at[:10] for r in rs}),
            "systolic": _min_avg_max(sys_), "diastolic": _min_avg_max(dia),
            "pulse": _min_avg_max([r.pulse for r in rs if r.pulse is not None]),
            "systolic_zones": _zone_counts(sys_, SYS_ZONES),
            "diastolic_zones": _zone_counts(dia, DIA_ZONES),
        })
    return out


# --- reminders (push notification to log a reading)

MAX_REMINDERS = 6
REMINDER_WINDOW = timedelta(minutes=30)  # late-fire allowance after a restart
SKIP_IF_LOGGED_WITHIN = timedelta(hours=2)


def list_reminders(conn, user_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, at_time, tz FROM bp_reminders WHERE user_id = ? ORDER BY at_time", (user_id,))]


def add_reminder(conn, user_id: int, at_time: str, tz: str) -> None:
    """at_time 'HH:MM' in zone tz. ValueError on bad time, duplicate or too many."""
    try:
        t = datetime.strptime(at_time, "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ValueError("Pick a time.")
    if len(list_reminders(conn, user_id)) >= MAX_REMINDERS:
        raise ValueError(f"At most {MAX_REMINDERS} reminders.")
    try:
        conn.execute("INSERT INTO bp_reminders (user_id, at_time, tz) VALUES (?,?,?)",
                     (user_id, t, tz))
    except sqlite3.IntegrityError:
        raise ValueError(f"You already have a reminder at {t}.")
    conn.commit()


def delete_reminder(conn, user_id: int, reminder_id: int) -> None:
    conn.execute("DELETE FROM bp_reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id))
    conn.commit()


def _local(now_utc: datetime, tz: str) -> datetime:
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return now_utc.astimezone(zone).replace(tzinfo=None)


def due_reminders(conn, now_utc: datetime) -> list[dict]:
    """Reminders whose time has come today (in their zone, within the window)
    and that haven't fired today. Each carries `local_now`."""
    due = []
    for r in conn.execute("SELECT * FROM bp_reminders").fetchall():
        now = _local(now_utc, r["tz"])
        at = datetime.combine(now.date(), datetime.strptime(r["at_time"], "%H:%M").time())
        if r["last_sent"] != now.date().isoformat() and at <= now < at + REMINDER_WINDOW:
            due.append({**dict(r), "local_now": now})
    return due


def send_reminders(conn, now_utc: datetime) -> None:
    """Scheduler job: push 'time to log' for each due reminder, unless the user
    logged a reading in the last 2 hours. Marked done for today either way."""
    from ... import push  # late: keeps this module importable without push deps
    for r in due_reminders(conn, now_utc):
        since = _iso(r["local_now"] - SKIP_IF_LOGGED_WITHIN)
        recent = conn.execute("SELECT 1 FROM readings WHERE user_id = ? AND taken_at >= ?",
                              (r["user_id"], since)).fetchone()
        if recent:
            logger.info("reminder %s skipped: user %s logged recently", r["id"], r["user_id"])
        else:
            n = push.send_to_user(conn, r["user_id"], "Time to check your blood pressure",
                                  "Tap to log a reading.", "/bp")
            logger.info("reminder %s for user %s sent to %d device(s)", r["id"], r["user_id"], n)
        conn.execute("UPDATE bp_reminders SET last_sent = ? WHERE id = ?",
                     (r["local_now"].date().isoformat(), r["id"]))
        conn.commit()
