"""Weight: weigh-ins, summaries, trend series, reminders.

Weights are stored in kg (`weight_entries.kg`) with the unit that was typed;
everything shown is converted to the user's display unit (`weight_settings`).
Like blood pressure, ranges work on daily averages: several weigh-ins on one
day count once. The 7-day average at a day = mean of the daily values in the
7 days ending that day (days without a weigh-in are skipped, not zero).
Every query filters by the acting user's id.
"""
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .schemas import Entry, EntryIn, from_kg, to_kg

log = logging.getLogger("bp_tracker.weight")
MAX_REMINDERS = 3
REMINDER_WINDOW = timedelta(minutes=30)
RANGES = {"week": 7, "month": 30, "3m": 91, "year": 365, "all": None}


# --- settings

def get_settings(conn, user_id: int) -> dict:
    row = conn.execute("SELECT unit, goal_kg FROM weight_settings WHERE user_id = ?", (user_id,)).fetchone()
    s = dict(row) if row else {"unit": "lb", "goal_kg": None}
    s["goal"] = disp(s["goal_kg"], s["unit"])
    return s


def save_settings(conn, user_id: int, s) -> None:
    goal_kg = round(to_kg(s.goal, s.unit), 3) if s.goal is not None else None
    conn.execute("INSERT INTO weight_settings (user_id, unit, goal_kg) VALUES (?,?,?)"
                 " ON CONFLICT(user_id) DO UPDATE SET unit = excluded.unit, goal_kg = excluded.goal_kg",
                 (user_id, s.unit, goal_kg))
    conn.commit()


def disp(kg: float | None, unit: str) -> float | None:
    """kg -> display unit, one decimal."""
    v = from_kg(kg, unit)
    return round(v, 1) if v is not None else None


# --- entries

def _entry(row) -> Entry:
    return Entry(id=row["id"], taken_at=row["taken_at"], kg=row["kg"], unit=row["unit"], note=row["note"])


def add_entry(conn, user_id: int, e: EntryIn) -> int:
    eid = conn.execute("INSERT INTO weight_entries (user_id, taken_at, kg, unit, note) VALUES (?,?,?,?,?)",
                       (user_id, e.taken_at.strftime("%Y-%m-%dT%H:%M"), e.kg, e.unit, e.note)).lastrowid
    conn.commit()
    return eid


def delete_entry(conn, user_id: int, entry_id: int) -> None:
    conn.execute("DELETE FROM weight_entries WHERE id = ? AND user_id = ?", (entry_id, user_id))
    conn.commit()


def list_entries(conn, user_id: int, start: date | None = None, end: date | None = None) -> list[Entry]:
    sql, args = "SELECT * FROM weight_entries WHERE user_id = ?", [user_id]
    if start:
        sql, args = sql + " AND taken_at >= ?", args + [start.isoformat()]
    if end:
        sql, args = sql + " AND taken_at < ?", args + [(end + timedelta(days=1)).isoformat()]
    return [_entry(r) for r in conn.execute(sql + " ORDER BY taken_at DESC, id DESC", args)]


def entries_by_day(conn, user_id: int) -> list[tuple[str, list[Entry]]]:
    days: dict[str, list[Entry]] = {}
    for e in list_entries(conn, user_id):
        days.setdefault(e.taken_at[:10], []).append(e)
    return list(days.items())


def latest_kg(conn, user_id: int, on_or_before: date | None = None) -> float | None:
    """The most recent weigh-in (kg), optionally on or before a day. Used by
    the workout module's calorie estimate."""
    sql, args = "SELECT kg FROM weight_entries WHERE user_id = ?", [user_id]
    if on_or_before:
        sql, args = sql + " AND taken_at < ?", args + [(on_or_before + timedelta(days=1)).isoformat()]
    row = conn.execute(sql + " ORDER BY taken_at DESC, id DESC LIMIT 1", args).fetchone()
    return row[0] if row else None


# --- daily series & summaries

def daily(conn, user_id: int, start: date | None = None, end: date | None = None) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, average kg that day)] oldest first."""
    sql, args = ("SELECT substr(taken_at, 1, 10) AS d, AVG(kg) FROM weight_entries WHERE user_id = ?", [user_id])
    if start:
        sql, args = sql + " AND taken_at >= ?", args + [start.isoformat()]
    if end:
        sql, args = sql + " AND taken_at < ?", args + [(end + timedelta(days=1)).isoformat()]
    return [(r[0], r[1]) for r in conn.execute(sql + " GROUP BY d ORDER BY d", args)]


def _avg7(days: list[tuple[str, float]]) -> list[float]:
    out = []
    for i, (d, _) in enumerate(days):
        lo = (date.fromisoformat(d) - timedelta(days=6)).isoformat()
        win = [kg for dd, kg in days[: i + 1] if dd >= lo]
        out.append(sum(win) / len(win))
    return out


def _change_since(days: list[tuple[str, float]], today: date, n: int) -> float | None:
    """Latest daily value minus the value on the latest day at least n days ago."""
    if len(days) < 2:
        return None
    cutoff = (today - timedelta(days=n)).isoformat()
    before = [kg for d, kg in days if d <= cutoff]
    return days[-1][1] - before[-1] if before else None


def summary(conn, user_id: int, today: date) -> dict:
    """Latest weigh-in, 7/30-day change, goal distance — all in the display unit."""
    s = get_settings(conn, user_id)
    u = s["unit"]
    days = daily(conn, user_id, end=today)
    last = conn.execute("SELECT * FROM weight_entries WHERE user_id = ? ORDER BY taken_at DESC, id DESC LIMIT 1",
                        (user_id,)).fetchone()
    latest = disp(last["kg"], u) if last else None
    to_goal = round(latest - s["goal"], 1) if latest is not None and s["goal"] is not None else None
    c7, c30 = _change_since(days, today, 7), _change_since(days, today, 30)
    return {"unit": u, "latest": latest, "latest_at": last["taken_at"] if last else None,
            "change7": disp(c7, u) if c7 is not None else None,
            "change30": disp(c30, u) if c30 is not None else None,
            "goal": s["goal"], "to_goal": to_goal}


def stats(conn, user_id: int, today: date, rng: str) -> dict:
    """Chart + tiles for a range ('week' | 'month' | '3m' | 'year' | 'all')."""
    s = get_settings(conn, user_id)
    u = s["unit"]
    n = RANGES.get(rng, 30)
    start = today - timedelta(days=n - 1) if n else None
    # the 7-day average needs the 6 days before the range too
    days_all = daily(conn, user_id, start - timedelta(days=6) if start else None, today)
    avg = _avg7(days_all)
    keep = [i for i, (d, _) in enumerate(days_all) if start is None or d >= start.isoformat()]
    points = [{"date": days_all[i][0], "weight": disp(days_all[i][1], u), "avg7": disp(avg[i], u)} for i in keep]
    vals = [p["weight"] for p in points]
    return {
        "unit": u, "range": rng, "start": start.isoformat() if start else (points[0]["date"] if points else None),
        "end": today.isoformat(), "goal": s["goal"], "points": points,
        "tiles": {
            "latest": vals[-1] if vals else None,
            "change": round(vals[-1] - vals[0], 1) if len(vals) > 1 else None,
            "low": min(vals) if vals else None, "high": max(vals) if vals else None,
            "avg": round(sum(vals) / len(vals), 1) if vals else None, "days": len(vals),
        },
    }


# --- reminders (a daily nudge to weigh in; not sent once you have today)

def _local(now_utc: datetime, tz: str) -> datetime:
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    aware = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=ZoneInfo("UTC"))
    return aware.astimezone(zone).replace(tzinfo=None)


def list_reminders(conn, user_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, at_time, tz FROM weight_reminders WHERE user_id = ? ORDER BY at_time", (user_id,))]


def add_reminder(conn, user_id: int, at_time: str, tz: str) -> None:
    at_time = (at_time or "").strip()[:5]
    try:
        datetime.strptime(at_time, "%H:%M")
    except ValueError:
        raise ValueError("Pick a time")
    if len(list_reminders(conn, user_id)) >= MAX_REMINDERS:
        raise ValueError(f"At most {MAX_REMINDERS} reminders")
    if conn.execute("SELECT 1 FROM weight_reminders WHERE user_id = ? AND at_time = ?",
                    (user_id, at_time)).fetchone():
        raise ValueError(f"You already have a reminder at {at_time}")
    conn.execute("INSERT INTO weight_reminders (user_id, at_time, tz) VALUES (?,?,?)", (user_id, at_time, tz))
    conn.commit()


def delete_reminder(conn, user_id: int, reminder_id: int) -> None:
    conn.execute("DELETE FROM weight_reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id))
    conn.commit()


def send_reminders(conn, now_utc: datetime) -> None:
    """Scheduler job (once a minute, idempotent): each reminder fires at most
    once per local day, up to 30 min late, and not if you've weighed in today."""
    from ... import push
    for r in conn.execute("SELECT * FROM weight_reminders").fetchall():
        now = _local(now_utc, r["tz"])
        today = now.date().isoformat()
        if r["last_reminded"] == today:
            continue
        at = datetime.combine(now.date(), datetime.strptime(r["at_time"], "%H:%M").time())
        if not at <= now < at + REMINDER_WINDOW:
            continue
        done = conn.execute("SELECT 1 FROM weight_entries WHERE user_id = ? AND taken_at >= ? LIMIT 1",
                            (r["user_id"], today)).fetchone()
        if done:
            log.info("weight reminder %s skipped (already weighed in today)", r["id"])
        else:
            n = push.send_to_user(conn, r["user_id"], "Time to weigh in", "Tap to log today's weight.", "/weight")
            log.info("weight reminder %s for user %s sent to %d device(s)", r["id"], r["user_id"], n)
        conn.execute("UPDATE weight_reminders SET last_reminded = ? WHERE id = ?", (today, r["id"]))
        conn.commit()
