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
from .modules.workout.schemas import ExerciseIn, ReminderIn, RoutineIn, RoutineItemIn, SetIn, SettingsIn

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
        "workout_exercises": [
            {**r, "track_reps": bool(r["track_reps"]), "track_weight": bool(r["track_weight"]),
             "active": bool(r["active"])}
            for r in q("SELECT name, category, equipment, track_reps, track_weight, unit, default_rest_sec,"
                       " notes, target_duration_sec, active FROM workout_exercises WHERE user_id = ? ORDER BY id")],
        "workout_sessions": _export_sessions(conn, user_id),
        "workout_routines": _export_routines(conn, user_id),
        "weight_entries": q("SELECT taken_at, kg, unit, note FROM weight_entries WHERE user_id = ?"
                            " ORDER BY taken_at, id"),
        "weight_settings": next(iter(q("SELECT unit, goal_kg FROM weight_settings WHERE user_id = ?")), None),
        "weight_reminders": q("SELECT at_time, tz FROM weight_reminders WHERE user_id = ? ORDER BY at_time"),
        "workout_reminders": [
            {"exercise": r["name"] if r["kind"] == "exercise" else None,
             "routine": r["name"] if r["kind"] == "routine" else None,
             **{k: r[k] for k in ("days", "at_time", "tz", "weekly_goal", "active")},
             "skipped": [d for (d,) in conn.execute(
                 "SELECT day FROM workout_reminder_skips WHERE reminder_id = ? ORDER BY day", (r["id"],))]}
            for r in _wo().list_reminders(conn, user_id)],
        "workout_settings": next(iter(q("SELECT body_weight, unit, intensity FROM workout_settings"
                                        " WHERE user_id = ?")), None),
    }


def _wo():
    from .modules.workout import service
    return service


def _export_routines(conn, user_id: int) -> list[dict]:
    from .modules.workout import service as wo
    return [{"name": r["name"], "notes": r["notes"],
             "items": [{"exercise": i["name"], **{k: i[k] for k in ("target_sets", "target_reps", "target_weight",
                                                                   "rest_sec", "superset_with_previous")}}
                       for i in r["items"]]}
            for r in wo.list_routines(conn, user_id)]


_SET_COLS = ("uid", "reps", "weight", "unit", "started_at", "ended_at", "rpe", "tempo", "note")


def _export_sessions(conn, user_id: int) -> list[dict]:
    out = []
    for s in conn.execute(
            "SELECT s.id, s.started_at, s.ended_at, s.notes, s.calories, r.name AS routine FROM workout_sessions s"
            " LEFT JOIN workout_routines r ON r.id = s.routine_id AND r.user_id = s.user_id"
            " WHERE s.user_id = ? ORDER BY s.started_at", (user_id,)).fetchall():
        exs = []
        for se in conn.execute(
                "SELECT se.id, e.name, se.position, se.group_id, se.target_rest_sec, se.target_sets, se.target_reps,"
                " se.target_weight FROM workout_session_exercises se"
                " JOIN workout_exercises e ON e.id = se.exercise_id WHERE se.session_id = ? ORDER BY se.position",
                (s["id"],)).fetchall():
            sets = [dict(r) for r in conn.execute(
                f"SELECT {', '.join(_SET_COLS)} FROM workout_sets WHERE session_exercise_id = ? AND user_id = ?"
                " ORDER BY started_at", (se["id"], user_id))]
            exs.append({"exercise": se["name"], "position": se["position"], "group_id": se["group_id"],
                        "target_rest_sec": se["target_rest_sec"], "target_sets": se["target_sets"],
                        "target_reps": se["target_reps"], "target_weight": se["target_weight"], "sets": sets})
        out.append({"started_at": s["started_at"], "ended_at": s["ended_at"], "notes": s["notes"],
                    "calories": s["calories"], "routine": s["routine"], "exercises": exs})
    return out


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
    ws = data.get("workout_settings")
    if isinstance(ws, dict) and not conn.execute("SELECT 1 FROM workout_settings WHERE user_id = ?",
                                                 (user_id,)).fetchone():
        try:  # only into an account that has no settings yet (never overwrite)
            from .modules.workout import service as wo
            wo.save_settings(conn, user_id, SettingsIn(**{k: ws.get(k) for k in ("body_weight", "unit", "intensity")
                                                          if ws.get(k) is not None}))
            n["added"] += 1
        except (ValidationError, TypeError):
            n["invalid"] += 1
    _restore_workout(conn, user_id, rows, n)
    _restore_weight(conn, user_id, data, rows, n)
    conn.commit()
    return n


_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


def _restore_workout(conn, user_id: int, rows, n: dict) -> None:
    """Exercises matched by name; sessions by start time; sets by uid. A set
    uid owned by another account (restoring someone's backup) gets a stable
    per-account suffix, so restoring twice still adds nothing."""
    ex_ids = {}
    for r in rows("workout_exercises"):
        try:
            e = ExerciseIn(**{k: r.get(k) for k in ("name", "category", "equipment", "track_reps", "track_weight",
                                                       "unit", "default_rest_sec", "notes", "target_duration_sec")
                              if r.get(k) is not None})
        except (ValidationError, TypeError):
            n["invalid"] += 1
            continue
        row = conn.execute("SELECT id FROM workout_exercises WHERE user_id = ? AND name = ? COLLATE NOCASE",
                           (user_id, e.name)).fetchone()
        if row:
            n["skipped"] += 1
            ex_ids[e.name.lower()] = row[0]
            continue
        ex_ids[e.name.lower()] = conn.execute(
            "INSERT INTO workout_exercises (user_id, name, category, equipment, track_reps, track_weight, unit,"
            " default_rest_sec, notes, target_duration_sec, active) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, e.name, e.category, e.equipment, int(e.track_reps), int(e.track_weight), e.unit,
             e.default_rest_sec, e.notes, e.target_duration_sec, 0 if r.get("active") is False else 1)).lastrowid
        n["added"] += 1

    routine_ids = _restore_routines(conn, user_id, rows, n, ex_ids)
    _restore_reminders(conn, user_id, rows, n, ex_ids, routine_ids)

    for s in rows("workout_sessions"):
        start, end = s.get("started_at"), s.get("ended_at")
        if not (isinstance(start, str) and _SECOND.match(start)) or \
                not (end is None or (isinstance(end, str) and _SECOND.match(end))):
            n["invalid"] += 1
            continue
        row = conn.execute("SELECT id FROM workout_sessions WHERE user_id = ? AND started_at = ?",
                           (user_id, start)).fetchone()
        if row:
            sid = row[0]
            n["skipped"] += 1
        else:
            notes = s.get("notes") if isinstance(s.get("notes"), str) else None
            kcal = s.get("calories")
            kcal = kcal if isinstance(kcal, int) and not isinstance(kcal, bool) and 0 <= kcal <= 5000 else None
            rname = s.get("routine")
            sid = conn.execute(
                "INSERT INTO workout_sessions (user_id, started_at, ended_at, notes, calories, routine_id)"
                " VALUES (?,?,?,?,?,?)", (user_id, start, end, notes[:300] if notes else None, kcal,
                                          routine_ids.get(rname.lower()) if isinstance(rname, str) else None)).lastrowid
            n["added"] += 1
        for x in s.get("exercises") if isinstance(s.get("exercises"), list) else []:
            ex_id = ex_ids.get(x.get("exercise", "").lower()) if isinstance(x, dict) and \
                isinstance(x.get("exercise"), str) else None
            if ex_id is None:
                n["invalid"] += 1
                continue
            row = conn.execute("SELECT id FROM workout_session_exercises WHERE session_id = ? AND exercise_id = ?",
                               (sid, ex_id)).fetchone()
            if row:
                se_id = row[0]
            else:
                rest = x.get("target_rest_sec")
                tgt = lambda k, hi, typ=int: x.get(k) if isinstance(x.get(k), typ) and not isinstance(  # noqa: E731
                    x.get(k), bool) and 0 <= x.get(k) <= hi else None
                se_id = conn.execute(
                    "INSERT INTO workout_session_exercises (session_id, exercise_id, position, group_id,"
                    " target_rest_sec, target_sets, target_reps, target_weight) VALUES (?,?,?,?,?,?,?,?)",
                    (sid, ex_id, x.get("position") if isinstance(x.get("position"), int) else 0,
                     x.get("group_id") if isinstance(x.get("group_id"), int) else None,
                     rest if isinstance(rest, int) and 0 <= rest <= 1800 else 90,
                     tgt("target_sets", 20), tgt("target_reps", 200),
                     tgt("target_weight", 2000, (int, float)))).lastrowid
            for st in x.get("sets") if isinstance(x.get("sets"), list) else []:
                try:
                    v = SetIn(**{k: st.get(k) for k in _SET_COLS}, session_exercise_id=se_id)
                except (ValidationError, TypeError, AttributeError):
                    n["invalid"] += 1
                    continue
                uid = v.uid
                owner = conn.execute("SELECT user_id FROM workout_sets WHERE uid = ?", (uid,)).fetchone()
                if owner and owner[0] != user_id:
                    uid = f"{v.uid[:50]}-u{user_id}"
                    owner = conn.execute("SELECT user_id FROM workout_sets WHERE uid = ?", (uid,)).fetchone()
                if owner:
                    n["skipped"] += 1
                    continue
                conn.execute(
                    "INSERT INTO workout_sets (uid, user_id, session_exercise_id, reps, weight, unit, started_at,"
                    " ended_at, rpe, tempo, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, user_id, se_id, v.reps, v.weight, v.unit, v.started_at.isoformat(timespec="seconds"),
                     v.ended_at.isoformat(timespec="seconds"), v.rpe, v.tempo, v.note))
                n["added"] += 1


_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


def _restore_weight(conn, user_id: int, data: dict, rows, n: dict) -> None:
    """Weigh-ins (matched on time + weight), settings (only into an account
    without them), reminders (by time)."""
    from .modules.weight.schemas import MAX_KG, MIN_KG
    for e in rows("weight_entries"):
        kg, t, unit = e.get("kg"), e.get("taken_at"), e.get("unit")
        if not (isinstance(kg, (int, float)) and not isinstance(kg, bool) and MIN_KG <= kg <= MAX_KG
                and isinstance(t, str) and _MINUTE.match(t) and unit in ("lb", "kg")):
            n["invalid"] += 1
            continue
        if conn.execute("SELECT 1 FROM weight_entries WHERE user_id = ? AND taken_at = ? AND ABS(kg - ?) < 0.001",
                        (user_id, t, kg)).fetchone():
            n["skipped"] += 1
            continue
        note = e.get("note") if isinstance(e.get("note"), str) else None
        conn.execute("INSERT INTO weight_entries (user_id, taken_at, kg, unit, note) VALUES (?,?,?,?,?)",
                     (user_id, t, round(kg, 3), unit, note[:300] if note else None))
        n["added"] += 1
    ws = data.get("weight_settings")
    if isinstance(ws, dict) and ws.get("unit") in ("lb", "kg") and not conn.execute(
            "SELECT 1 FROM weight_settings WHERE user_id = ?", (user_id,)).fetchone():
        goal = ws.get("goal_kg")
        goal = goal if isinstance(goal, (int, float)) and not isinstance(goal, bool) and MIN_KG <= goal <= MAX_KG \
            else None
        conn.execute("INSERT INTO weight_settings (user_id, unit, goal_kg) VALUES (?,?,?)", (user_id, ws["unit"], goal))
        n["added"] += 1
    for r in rows("weight_reminders"):
        at = r.get("at_time")
        if not (isinstance(at, str) and re.match(r"^([01]\d|2[0-3]):[0-5]\d$", at)):
            n["invalid"] += 1
            continue
        cur = conn.execute("INSERT OR IGNORE INTO weight_reminders (user_id, at_time, tz) VALUES (?,?,?)",
                           (user_id, at, _tz(r.get("tz"))))
        n["added" if cur.rowcount else "skipped"] += 1


def _restore_reminders(conn, user_id: int, rows, n: dict, ex_ids: dict, routine_ids: dict) -> None:
    """Reminders, matched by target + time + days (never duplicated)."""
    for r in rows("workout_reminders"):
        ex = r.get("exercise") if isinstance(r.get("exercise"), str) else None
        rt = r.get("routine") if isinstance(r.get("routine"), str) else None
        try:
            m = ReminderIn(exercise_id=ex_ids.get(ex.lower()) if ex else None,
                           routine_id=routine_ids.get(rt.lower()) if rt else None,
                           days=r.get("days"), at_time=r.get("at_time"), weekly_goal=r.get("weekly_goal"))
        except (ValidationError, TypeError, AttributeError):
            n["invalid"] += 1
            continue
        row = conn.execute("SELECT id FROM workout_reminders WHERE user_id = ? AND exercise_id IS ? AND routine_id IS ?"
                           " AND at_time = ? AND days = ?", (user_id, m.exercise_id, m.routine_id, m.at_time,
                                                             "".join(map(str, m.days)))).fetchone()
        if row:
            rid = row[0]
            n["skipped"] += 1
        else:
            rid = conn.execute("INSERT INTO workout_reminders (user_id, exercise_id, routine_id, days, at_time, tz,"
                               " weekly_goal, active) VALUES (?,?,?,?,?,?,?,?)",
                               (user_id, m.exercise_id, m.routine_id, "".join(map(str, m.days)), m.at_time,
                                _tz(r.get("tz")), m.weekly_goal, 0 if r.get("active") is False else 1)).lastrowid
            n["added"] += 1
        for day in r.get("skipped") if isinstance(r.get("skipped"), list) else []:
            if isinstance(day, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", day):
                conn.execute("INSERT OR IGNORE INTO workout_reminder_skips (reminder_id, day) VALUES (?, ?)",
                             (rid, day))


def _restore_routines(conn, user_id: int, rows, n: dict, ex_ids: dict) -> dict:
    """Routines (by name).
    Returns routine name (lowercased) -> id for linking sessions."""
    from .modules.workout import service as wo
    out = {r["name"].lower(): r["id"] for r in wo.list_routines(conn, user_id)}
    for r in rows("workout_routines"):
        try:
            items = []
            for i in r.get("items") or []:
                ex = ex_ids.get(i.get("exercise", "").lower()) if isinstance(i.get("exercise"), str) else None
                if ex is None:
                    raise ValueError("unknown exercise")
                items.append(RoutineItemIn(exercise_id=ex, **{k: i.get(k) for k in (
                    "target_sets", "target_reps", "target_weight", "rest_sec") if i.get(k) is not None},
                    superset_with_previous=bool(i.get("superset_with_previous"))))
            rt = RoutineIn(name=r.get("name"), notes=r.get("notes"), items=items)
        except (ValidationError, TypeError, ValueError, AttributeError):
            n["invalid"] += 1
            continue
        if rt.name.lower() in out:
            n["skipped"] += 1
            continue
        out[rt.name.lower()] = wo.save_routine(conn, user_id, rt)
        n["added"] += 1
    return out


def copy_database(conn, dest_path: str) -> None:
    """Consistent snapshot of the whole DB (safe while the app is running)."""
    out = sqlite3.connect(dest_path)
    try:
        conn.backup(out)
    finally:
        out.close()
