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


# --- daily averages, calibrated by measuring session
#
# Readings are often taken in quick runs (e.g. 2-3 cuffs a minute apart). A
# run shouldn't outweigh a single reading at another time of day, so:
#   1. Sessions: sort a day's readings by time; a reading joins the current
#      session if it's within SESSION_GAP (5 min) of the previous reading,
#      otherwise it starts a new one. Sessions never cross midnight.
#   2. Session average = mean of its readings (pulse: readings with a pulse).
#   3. Daily average = mean of that day's session averages (pulse: sessions
#      with a pulse).
# Range and report averages then average the daily averages (each day counts
# once). Min/max and zone counts stay per reading.

SESSION_GAP = timedelta(minutes=5)


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def sessions(readings: list[Reading]) -> list[list[Reading]]:
    """Group readings into measuring sessions (oldest first): consecutive
    readings on the same day at most SESSION_GAP apart."""
    out: list[list[Reading]] = []
    prev = None
    for r in sorted(readings, key=lambda r: r.taken_at):
        t = datetime.fromisoformat(r.taken_at)
        if prev is not None and t.date() == prev.date() and t - prev <= SESSION_GAP:
            out[-1].append(r)
        else:
            out.append([r])
        prev = t
    return out


def session_average(group: list[Reading]) -> dict:
    return {"start": group[0].taken_at, "end": group[-1].taken_at, "count": len(group),
            "systolic": _mean([r.systolic for r in group]), "diastolic": _mean([r.diastolic for r in group]),
            "pulse": _mean([r.pulse for r in group])}


def day_values(readings: list[Reading]) -> dict[str, dict]:
    """{'YYYY-MM-DD': {systolic, diastolic, pulse, count, sessions}} (unrounded),
    each value the mean of the day's session averages."""
    by_day: dict[str, list[Reading]] = {}
    for r in readings:
        by_day.setdefault(r.taken_at[:10], []).append(r)
    out = {}
    for d in sorted(by_day):
        avgs = [session_average(g) for g in sessions(by_day[d])]
        out[d] = {"systolic": _mean([a["systolic"] for a in avgs]),
                  "diastolic": _mean([a["diastolic"] for a in avgs]),
                  "pulse": _mean([a["pulse"] for a in avgs]),
                  "count": len(by_day[d]), "sessions": len(avgs)}
    return out


def daily_averages(conn, user_id: int, start: date, end: date) -> list[dict]:
    """Per-day averages in [start, end] (inclusive), oldest first, session-
    calibrated (see above). Days without readings are omitted."""
    days = day_values(list_readings(conn, user_id, start, end))
    return [{"x": d, "systolic": _r1(v["systolic"]), "diastolic": _r1(v["diastolic"]),
             "pulse": _r1(v["pulse"]), "count": v["count"], "sessions": v["sessions"]}
            for d, v in days.items()]


def _r1(v):
    return None if v is None else round(v, 1)


def _summary(conn, user_id: int, start: date, end: date) -> dict:
    """Range averages = average of the daily averages, so a day with many
    readings weighs the same as a day with one. Pulse: days with a pulse only.
    count = number of readings."""
    days = day_values(list_readings(conn, user_id, start, end)).values()
    return {"systolic": _r1(_mean([v["systolic"] for v in days])),
            "diastolic": _r1(_mean([v["diastolic"] for v in days])),
            "pulse": _r1(_mean([v["pulse"] for v in days])),
            "count": sum(v["count"] for v in days)}


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


def _min_avg_max(readings: list[Reading], field: str, days: dict | None = None) -> dict | None:
    """avg = average of the session-calibrated daily averages (each day counts
    once); min/max = lowest/highest single reading. Readings without a value
    for `field` (pulse) are ignored."""
    values = [getattr(r, field) for r in readings if getattr(r, field) is not None]
    if not values:
        return None
    daily = [v[field] for v in (days or day_values(readings)).values() if v[field] is not None]
    return {"avg": round(sum(daily) / len(daily), 1), "min": min(values), "max": max(values)}


def _zone_counts(values: list[int], zones) -> list[dict]:
    counts = []
    for i, (hi, label, token) in enumerate(zones):
        lo = zones[i - 1][0] if i else None
        n = sum(1 for v in values if (lo is None or v > lo) and (hi is None or v <= hi))
        counts.append({"label": label, "token": token, "count": n})
    return counts


def reports(conn, user_id: int, period: str) -> list[dict]:
    """One summary per week (Mon–Sun) or calendar month that has readings,
    newest first: reading and session counts, avg/min/max of systolic,
    diastolic and pulse (avg is the average of session-calibrated daily
    averages; see _min_avg_max), and readings per BP zone."""
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
        days = day_values(rs)
        out.append({
            "start": start.isoformat(), "end": end.isoformat(),
            "label": _period_label(start, end, period),
            "count": len(rs),
            "days": len({r.taken_at[:10] for r in rs}),
            "sessions": sum(v["sessions"] for v in days.values()),
            "systolic": _min_avg_max(rs, "systolic", days), "diastolic": _min_avg_max(rs, "diastolic", days),
            "pulse": _min_avg_max(rs, "pulse", days),
            "systolic_zones": _zone_counts(sys_, SYS_ZONES),
            "diastolic_zones": _zone_counts(dia, DIA_ZONES),
        })
    return out


# --- reminders (push notification to log a reading)

MAX_REMINDERS = 6
REMINDER_WINDOW = timedelta(minutes=30)  # late-fire allowance after a restart


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


def reminder_window_start(conn, reminder: dict) -> datetime:
    """Start of the stretch of today in which a reading answers this reminder:
    half-way back to the user's previous reminder today, or midnight if it is
    the first. E.g. reminders 08:00 + 20:00: 08:00 covers 00:00-08:00 and
    20:00 covers 14:00-20:00, so a late 09:00 reading answers the morning
    reminder without silencing the evening one."""
    day = reminder["local_now"].date()
    at = datetime.strptime(reminder["at_time"], "%H:%M")
    earlier = [datetime.strptime(t, "%H:%M") for (t,) in conn.execute(
        "SELECT at_time FROM bp_reminders WHERE user_id = ? AND at_time < ?",
        (reminder["user_id"], reminder["at_time"]))]
    if not earlier:
        return datetime.combine(day, datetime.min.time())
    prev = max(earlier)
    return datetime.combine(day, (prev + (at - prev) / 2).time())


def already_logged(conn, reminder: dict) -> bool:
    """True if the user logged a reading that answers this reminder (see
    reminder_window_start), up to now. Readings are naive local times."""
    return conn.execute(
        "SELECT 1 FROM readings WHERE user_id = ? AND taken_at >= ? AND taken_at <= ?",
        (reminder["user_id"], _iso(reminder_window_start(conn, reminder)),
         _iso(reminder["local_now"]))).fetchone() is not None


def send_reminders(conn, now_utc: datetime) -> None:
    """Scheduler job: push 'time to log' for each due reminder, unless a reading
    for it is already logged today (already_logged). Marked done for today
    either way, so it never fires twice."""
    from ... import push  # late: keeps this module importable without push deps
    for r in due_reminders(conn, now_utc):
        if already_logged(conn, r):
            logger.info("reminder %s skipped: user %s already logged a reading", r["id"], r["user_id"])
        else:
            n = push.send_to_user(conn, r["user_id"], "Time to check your blood pressure",
                                  "Tap to log a reading.", "/bp")
            logger.info("reminder %s for user %s sent to %d device(s)", r["id"], r["user_id"], n)
        conn.execute("UPDATE bp_reminders SET last_sent = ? WHERE id = ?",
                     (r["local_now"].date().isoformat(), r["id"]))
        conn.commit()
