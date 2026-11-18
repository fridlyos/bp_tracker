"""Workout module: exercise types, sessions, sets, and the numbers derived
from them.

Set times are naive local 'YYYY-MM-DDTHH:MM:SS' from the phone's clock.
Derived, never stored:
- duration (time under tension, measured) = ended_at - started_at
- rest before a set = its started_at - the previous set's ended_at in the
  same session-exercise (the first set has no rest)
- estimated TUT = reps x tempo seconds, when a tempo is given
- volume = reps x weight
Every query filters by the acting user's id.
"""
from datetime import datetime, timedelta

from .schemas import Exercise, ExerciseIn, SetIn, SetUpdate, tempo_seconds

STALE_SESSION = timedelta(hours=6)  # an unfinished session older than this is not "in progress"


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(timespec="seconds")


def _secs(a: str, b: str) -> int:
    return int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())


# --- exercise types

def _exercise(row) -> Exercise:
    return Exercise(id=row["id"], name=row["name"], category=row["category"], equipment=row["equipment"],
                    track_reps=bool(row["track_reps"]), track_weight=bool(row["track_weight"]),
                    unit=row["unit"], default_rest_sec=row["default_rest_sec"], notes=row["notes"],
                    target_duration_sec=row["target_duration_sec"], active=bool(row["active"]))


def list_exercises(conn, user_id: int, active_only: bool = False) -> list[Exercise]:
    sql = "SELECT * FROM workout_exercises WHERE user_id = ?" + (" AND active = 1" if active_only else "")
    rows = conn.execute(sql + " ORDER BY active DESC, COALESCE(category, '~') COLLATE NOCASE,"
                              " name COLLATE NOCASE", (user_id,)).fetchall()
    return [_exercise(r) for r in rows]


def get_exercise(conn, user_id: int, exercise_id: int) -> Exercise | None:
    row = conn.execute("SELECT * FROM workout_exercises WHERE id = ? AND user_id = ?",
                       (exercise_id, user_id)).fetchone()
    return _exercise(row) if row else None


class DuplicateName(ValueError):
    pass


def save_exercise(conn, user_id: int, e: ExerciseIn, exercise_id: int | None = None) -> Exercise | None:
    """Create (id None) or update. Raises DuplicateName if the name is taken."""
    clash = conn.execute("SELECT id FROM workout_exercises WHERE user_id = ? AND name = ? COLLATE NOCASE"
                         " AND id IS NOT ?", (user_id, e.name, exercise_id)).fetchone()
    if clash:
        raise DuplicateName(f"You already have an exercise called {e.name}")
    vals = (e.name, e.category, e.equipment, int(e.track_reps), int(e.track_weight), e.unit,
            e.default_rest_sec, e.notes, e.target_duration_sec)
    if exercise_id is None:
        exercise_id = conn.execute(
            "INSERT INTO workout_exercises (name, category, equipment, track_reps, track_weight, unit,"
            " default_rest_sec, notes, target_duration_sec, user_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (*vals, user_id)).lastrowid
    elif conn.execute("UPDATE workout_exercises SET name=?, category=?, equipment=?, track_reps=?,"
                      " track_weight=?, unit=?, default_rest_sec=?, notes=?, target_duration_sec=?"
                      " WHERE id=? AND user_id=?",
                      (*vals, exercise_id, user_id)).rowcount == 0:
        return None
    conn.commit()
    return get_exercise(conn, user_id, exercise_id)


def set_exercise_active(conn, user_id: int, exercise_id: int, active: bool) -> None:
    conn.execute("UPDATE workout_exercises SET active = ? WHERE id = ? AND user_id = ?",
                 (int(active), exercise_id, user_id))
    conn.commit()


def delete_exercise(conn, user_id: int, exercise_id: int) -> None:
    """Deletes the exercise and every set ever logged for it (archive keeps them)."""
    conn.execute("DELETE FROM workout_exercises WHERE id = ? AND user_id = ?", (exercise_id, user_id))
    conn.commit()


# --- sessions

def start_session(conn, user_id: int, now: datetime) -> int:
    """Start a workout, or return the one already in progress."""
    current = active_session(conn, user_id, now)
    if current:
        return current["id"]
    sid = conn.execute("INSERT INTO workout_sessions (user_id, started_at) VALUES (?, ?)",
                       (user_id, _iso(now))).lastrowid
    conn.commit()
    return sid


def active_session(conn, user_id: int, now: datetime):
    """The unfinished session started within STALE_SESSION, or None."""
    return conn.execute(
        "SELECT * FROM workout_sessions WHERE user_id = ? AND ended_at IS NULL AND started_at >= ?"
        " ORDER BY started_at DESC LIMIT 1", (user_id, _iso(now - STALE_SESSION))).fetchone()


def finish_session(conn, user_id: int, session_id: int, now: datetime, notes: str | None = None) -> None:
    """End time = the last set's end if there is one (walking out later
    shouldn't stretch the workout), else now."""
    last = conn.execute(
        "SELECT MAX(s.ended_at) FROM workout_sets s JOIN workout_session_exercises se"
        " ON se.id = s.session_exercise_id WHERE se.session_id = ? AND s.user_id = ?",
        (session_id, user_id)).fetchone()[0]
    conn.execute("UPDATE workout_sessions SET ended_at = COALESCE(ended_at, ?), notes = COALESCE(?, notes)"
                 " WHERE id = ? AND user_id = ?", (last or _iso(now), notes, session_id, user_id))
    conn.commit()


def delete_session(conn, user_id: int, session_id: int) -> None:
    conn.execute("DELETE FROM workout_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    conn.commit()


def _own_session(conn, user_id: int, session_id: int):
    return conn.execute("SELECT * FROM workout_sessions WHERE id = ? AND user_id = ?",
                        (session_id, user_id)).fetchone()


def add_session_exercise(conn, user_id: int, session_id: int, exercise_id: int,
                         target_rest_sec: int | None = None) -> int | None:
    """Add an exercise to a session (reuses it if already there)."""
    ex = get_exercise(conn, user_id, exercise_id)
    if ex is None or _own_session(conn, user_id, session_id) is None:
        return None
    row = conn.execute("SELECT id FROM workout_session_exercises WHERE session_id = ? AND exercise_id = ?",
                       (session_id, exercise_id)).fetchone()
    if row:
        return row[0]
    pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM workout_session_exercises"
                       " WHERE session_id = ?", (session_id,)).fetchone()[0]
    se_id = conn.execute(
        "INSERT INTO workout_session_exercises (session_id, exercise_id, position, target_rest_sec)"
        " VALUES (?,?,?,?)", (session_id, exercise_id, pos,
                              ex.default_rest_sec if target_rest_sec is None else target_rest_sec)).lastrowid
    conn.commit()
    return se_id


def remove_session_exercise(conn, user_id: int, se_id: int) -> None:
    conn.execute("DELETE FROM workout_session_exercises WHERE id = ? AND session_id IN"
                 " (SELECT id FROM workout_sessions WHERE user_id = ?)", (se_id, user_id))
    conn.commit()


def _own_session_exercise(conn, user_id: int, se_id: int):
    return conn.execute(
        "SELECT se.* FROM workout_session_exercises se JOIN workout_sessions s ON s.id = se.session_id"
        " WHERE se.id = ? AND s.user_id = ?", (se_id, user_id)).fetchone()


# --- sets

def upsert_set(conn, user_id: int, s: SetIn) -> bool:
    """Insert or replace a set by its client uid. False if the
    session-exercise isn't the user's (or the uid belongs to someone else)."""
    if _own_session_exercise(conn, user_id, s.session_exercise_id) is None:
        return False
    owner = conn.execute("SELECT user_id FROM workout_sets WHERE uid = ?", (s.uid,)).fetchone()
    if owner and owner[0] != user_id:
        return False
    conn.execute(
        "INSERT INTO workout_sets (uid, user_id, session_exercise_id, reps, weight, unit, started_at,"
        " ended_at, rpe, tempo, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(uid) DO UPDATE SET reps=excluded.reps, weight=excluded.weight, unit=excluded.unit,"
        " started_at=excluded.started_at, ended_at=excluded.ended_at, rpe=excluded.rpe,"
        " tempo=excluded.tempo, note=excluded.note",
        (s.uid, user_id, s.session_exercise_id, s.reps, s.weight, s.unit, _iso(s.started_at),
         _iso(s.ended_at), s.rpe, s.tempo, s.note))
    conn.commit()
    return True


def update_set(conn, user_id: int, uid: str, u: SetUpdate) -> bool:
    cur = conn.execute("UPDATE workout_sets SET reps = ?, weight = ?, rpe = ? WHERE uid = ? AND user_id = ?",
                       (u.reps, u.weight, u.rpe, uid, user_id))
    conn.commit()
    return cur.rowcount > 0


def delete_set(conn, user_id: int, uid: str) -> None:
    conn.execute("DELETE FROM workout_sets WHERE uid = ? AND user_id = ?", (uid, user_id))
    conn.commit()


def _set_dict(row, prev_end: str | None) -> dict:
    d = {k: row[k] for k in ("uid", "reps", "weight", "unit", "started_at", "ended_at", "rpe", "tempo", "note")}
    d["duration_sec"] = _secs(row["started_at"], row["ended_at"])
    d["rest_before_sec"] = max(0, _secs(prev_end, row["started_at"])) if prev_end else None
    tps = tempo_seconds(row["tempo"])
    d["est_tut_sec"] = tps * row["reps"] if tps and row["reps"] else None
    return d


def _summary(sets: list[dict]) -> dict:
    """Totals for a list of sets (one exercise, or a whole session)."""
    rests = [s["rest_before_sec"] for s in sets if s["rest_before_sec"] is not None]
    return {
        "sets": len(sets),
        "reps": sum(s["reps"] or 0 for s in sets),
        "volume": round(sum((s["reps"] or 0) * (s["weight"] or 0) for s in sets), 1),
        "tut_sec": sum(s["duration_sec"] for s in sets),
        "avg_rest_sec": round(sum(rests) / len(rests)) if rests else None,
        "top_weight": max((s["weight"] for s in sets if s["weight"] is not None), default=None),
    }


def session_detail(conn, user_id: int, session_id: int) -> dict | None:
    """A session with its exercises, each with its sets and derived numbers."""
    sess = _own_session(conn, user_id, session_id)
    if sess is None:
        return None
    exercises = []
    for se in conn.execute(
            "SELECT se.*, e.name, e.track_reps, e.track_weight, e.unit, e.target_duration_sec AS hold_sec"
            " FROM workout_session_exercises se"
            " JOIN workout_exercises e ON e.id = se.exercise_id WHERE se.session_id = ? ORDER BY se.position",
            (session_id,)):
        sets, prev_end = [], None
        prs, best_before = session_prs(conn, user_id, se["exercise_id"], session_id)
        for r in conn.execute("SELECT * FROM workout_sets WHERE session_exercise_id = ? AND user_id = ?"
                              " ORDER BY started_at", (se["id"], user_id)):
            sets.append(_set_dict(r, prev_end) | {"prs": prs.get(r["uid"], [])})
            prev_end = r["ended_at"]
        exercises.append({
            "target_sets": se["target_sets"], "target_reps": se["target_reps"],
            "target_weight": se["target_weight"],
            "pr_kinds": list(pr_kinds(bool(se["track_reps"]), bool(se["track_weight"]))),
            "best_before": best_before,
            "id": se["id"], "exercise_id": se["exercise_id"], "name": se["name"],
            "track_reps": bool(se["track_reps"]), "track_weight": bool(se["track_weight"]),
            "unit": se["unit"], "target_rest_sec": se["target_rest_sec"], "group_id": se["group_id"],
            "target_duration_sec": se["hold_sec"],
            "sets": sets, "summary": _summary(sets),
            "previous": last_sets(conn, user_id, se["exercise_id"], before_session=session_id)})
    all_sets = [s for e in exercises for s in e["sets"]]
    end = sess["ended_at"] or (max(s["ended_at"] for s in all_sets) if all_sets else None)
    duration = max(0, _secs(sess["started_at"], end)) if end else 0
    estimate = estimate_calories(get_settings(conn, user_id), duration)
    routine = conn.execute("SELECT name FROM workout_routines WHERE id = ? AND user_id = ?",
                           (sess["routine_id"], user_id)).fetchone() if sess["routine_id"] else None
    return {"id": sess["id"], "started_at": sess["started_at"], "ended_at": sess["ended_at"],
            "notes": sess["notes"], "in_progress": sess["ended_at"] is None,
            "duration_sec": duration, "routine": routine[0] if routine else None,
            "calories": sess["calories"], "calories_estimate": estimate,
            "kcal": sess["calories"] if sess["calories"] is not None else estimate,
            "exercises": exercises, "summary": _summary(all_sets)}


def last_sets(conn, user_id: int, exercise_id: int, before_session: int | None = None) -> list[dict]:
    """Sets of the most recent earlier session that had this exercise, for
    pre-filling reps/weight ("last time: 3 x 8 @ 135")."""
    row = conn.execute(
        "SELECT se.id FROM workout_session_exercises se JOIN workout_sessions s ON s.id = se.session_id"
        " WHERE s.user_id = ? AND se.exercise_id = ? AND s.id IS NOT ?"
        " AND EXISTS (SELECT 1 FROM workout_sets x WHERE x.session_exercise_id = se.id)"
        " ORDER BY s.started_at DESC LIMIT 1", (user_id, exercise_id, before_session)).fetchone()
    if not row:
        return []
    return [{"reps": r["reps"], "weight": r["weight"], "unit": r["unit"]} for r in conn.execute(
        "SELECT reps, weight, unit FROM workout_sets WHERE session_exercise_id = ? ORDER BY started_at",
        (row[0],))]


def recent_sessions(conn, user_id: int, limit: int = 30) -> list[dict]:
    """Newest first, each with exercise names and totals."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM workout_sessions WHERE user_id = ? ORDER BY started_at DESC LIMIT ?", (user_id, limit))]
    return [d for d in (session_detail(conn, user_id, i) for i in ids) if d]


def exercise_history(conn, user_id: int, exercise_id: int, limit: int = 20) -> list[dict]:
    """Per session (newest first): date, sets, top weight, volume, best set."""
    out = []
    for se in conn.execute(
            "SELECT se.id, s.started_at FROM workout_session_exercises se JOIN workout_sessions s"
            " ON s.id = se.session_id WHERE s.user_id = ? AND se.exercise_id = ?"
            " ORDER BY s.started_at DESC LIMIT ?", (user_id, exercise_id, limit)):
        sets, prev_end = [], None
        for r in conn.execute("SELECT * FROM workout_sets WHERE session_exercise_id = ? ORDER BY started_at",
                              (se["id"],)):
            sets.append(_set_dict(r, prev_end))
            prev_end = r["ended_at"]
        if sets:
            out.append({"date": se["started_at"][:10], "sets": sets, "summary": _summary(sets)})
    return out


# --- progress & personal records (phase 2)
#
# All weights are converted to the exercise's unit. Estimated 1RM uses the
# Epley formula, only for 1-12 reps (beyond that it's guesswork). PR kinds
# depend on what the exercise tracks:
#   weight tracked          -> "weight" (heaviest set), "e1rm" (if reps too)
#   reps only (bodyweight)  -> "reps" (most reps in a set)
#   neither (timed hold)    -> "time" (longest set)
# A set is flagged as a PR only when it beats every earlier set *and* there
# is an earlier session to beat (your first session isn't all PRs).

LB_PER_KG = 2.20462
PR_LABELS = {"weight": "Heaviest", "e1rm": "Best est. 1RM", "reps": "Most reps", "time": "Longest set"}


def convert(weight: float | None, from_unit: str, to_unit: str) -> float | None:
    if weight is None or from_unit == to_unit:
        return weight
    return weight * LB_PER_KG if to_unit == "lb" else weight / LB_PER_KG


def e1rm(weight: float | None, reps: int | None) -> float | None:
    if not weight or not reps or not 1 <= reps <= 12:
        return None
    return round(weight if reps == 1 else weight * (1 + reps / 30), 1)


def pr_kinds(track_reps: bool, track_weight: bool) -> tuple[str, ...]:
    if track_weight:
        return ("weight", "e1rm") if track_reps else ("weight",)
    return ("reps",) if track_reps else ("time",)


def _metrics(row, unit: str) -> dict:
    w = convert(row["weight"], row["unit"], unit)
    return {"weight": round(w, 1) if w is not None else None, "e1rm": e1rm(w, row["reps"]),
            "reps": row["reps"], "time": _secs(row["started_at"], row["ended_at"])}


def _scan(conn, user_id: int, ex) -> list[dict]:
    """Every session that has sets of exercise `ex` (a workout_exercises row
    or Exercise), oldest first, with per-set metrics and PR flags and the
    bests as they stood before the session."""
    kinds = pr_kinds(bool(ex["track_reps"]), bool(ex["track_weight"]))
    best = {k: None for k in kinds}
    out, cur = [], None
    for r in conn.execute(
            "SELECT x.*, s.id AS session_id, s.started_at AS session_start FROM workout_sets x"
            " JOIN workout_session_exercises se ON se.id = x.session_exercise_id"
            " JOIN workout_sessions s ON s.id = se.session_id"
            " WHERE s.user_id = ? AND x.user_id = ? AND se.exercise_id = ?"
            " ORDER BY s.started_at, x.started_at", (user_id, user_id, ex["id"])):
        if cur is None or cur["session_id"] != r["session_id"]:
            cur = {"session_id": r["session_id"], "date": r["session_start"][:10],
                   "best_before": dict(best), "sets": []}
            out.append(cur)
        m = _metrics(r, ex["unit"])
        flags = [k for k in kinds if len(out) > 1 and m[k] is not None
                 and best[k] is not None and m[k] > best[k]]
        for k in kinds:
            if m[k] is not None and (best[k] is None or m[k] > best[k]):
                best[k] = m[k]
        cur["sets"].append({"uid": r["uid"], "row": r, "m": m, "prs": flags})
    return out


def _ex_row(conn, user_id: int, exercise_id: int):
    return conn.execute("SELECT * FROM workout_exercises WHERE id = ? AND user_id = ?",
                        (exercise_id, user_id)).fetchone()


def session_prs(conn, user_id: int, exercise_id: int, session_id: int) -> tuple[dict, dict]:
    """(uid -> PR kinds, bests before this session) for one exercise."""
    ex = _ex_row(conn, user_id, exercise_id)
    if ex is None:
        return {}, {}
    entries = _scan(conn, user_id, ex)
    for i, e in enumerate(entries):
        if e["session_id"] == session_id:
            return {s["uid"]: s["prs"] for s in e["sets"]}, e["best_before"]
    # no sets yet this session: the bests so far, if there is history to beat
    kinds = pr_kinds(bool(ex["track_reps"]), bool(ex["track_weight"]))
    best = {k: None for k in kinds}
    for e in entries:
        for s in e["sets"]:
            for k in kinds:
                v = s["m"][k]
                if v is not None and (best[k] is None or v > best[k]):
                    best[k] = v
    return {}, best


def exercise_progress(conn, user_id: int, exercise_id: int, since: str | None = None) -> dict | None:
    """Per-session series for charts (from `since` 'YYYY-MM-DD') and all-time records."""
    ex = _ex_row(conn, user_id, exercise_id)
    if ex is None:
        return None
    kinds = pr_kinds(bool(ex["track_reps"]), bool(ex["track_weight"]))
    points, records = [], {}
    for e in _scan(conn, user_id, ex):
        ms = [s["m"] for s in e["sets"]]
        top = max((m for m in ms if m["weight"] is not None), key=lambda m: (m["weight"], m["reps"] or 0),
                  default=None)
        volume = round(sum((m["weight"] or 0) * (m["reps"] or 0) for m in ms), 1)
        rests, prev = [], None
        for s in e["sets"]:
            if prev:
                rests.append(max(0, _secs(prev, s["row"]["started_at"])))
            prev = s["row"]["ended_at"]
        p = {"date": e["date"], "session_id": e["session_id"], "sets": len(ms),
             "reps": sum(m["reps"] or 0 for m in ms),
             "top_weight": top["weight"] if top else None, "top_reps": top["reps"] if top else None,
             "e1rm": max((m["e1rm"] for m in ms if m["e1rm"] is not None), default=None),
             "max_reps": max((m["reps"] for m in ms if m["reps"] is not None), default=None),
             "max_time": max(m["time"] for m in ms), "tut": sum(m["time"] for m in ms),
             "volume": volume or None, "avg_rest": round(sum(rests) / len(rests)) if rests else None,
             "prs": sorted({k for s in e["sets"] for k in s["prs"]})}
        # all-time records (every session counts, not just the chart range)
        for k, val, extra in (("weight", p["top_weight"], {"reps": p["top_reps"]}), ("e1rm", p["e1rm"], {}),
                              ("reps", p["max_reps"], {}), ("time", p["max_time"], {}),
                              ("volume", p["volume"], {})):
            if (k in kinds or k == "volume" and "e1rm" in kinds) and val is not None \
                    and (k not in records or val > records[k]["value"]):
                records[k] = {"value": val, "date": p["date"], "session_id": p["session_id"], **extra}
        if since is None or p["date"] >= since:
            points.append(p)
    return {"exercise": _exercise(ex).model_dump(), "kinds": list(kinds), "points": points,
            "records": records}


def recent_prs(conn, user_id: int, since: str, limit: int = 8) -> list[dict]:
    """PR sets logged on or after `since` (YYYY-MM-DD), newest first; the
    best value per exercise and kind."""
    found = {}
    for ex in conn.execute("SELECT * FROM workout_exercises WHERE user_id = ?", (user_id,)).fetchall():
        for e in _scan(conn, user_id, ex):
            if e["date"] < since:
                continue
            for s in e["sets"]:
                for k in s["prs"]:
                    key = (ex["id"], k)
                    if key not in found or s["m"][k] > found[key]["value"]:
                        found[key] = {"exercise_id": ex["id"], "exercise": ex["name"], "kind": k,
                                      "label": PR_LABELS[k], "value": s["m"][k], "reps": s["m"]["reps"],
                                      "unit": ex["unit"], "date": e["date"], "session_id": e["session_id"]}
    return sorted(found.values(), key=lambda x: x["date"], reverse=True)[:limit]


def weekly(conn, user_id: int, today, weeks: int = 8) -> list[dict]:
    """Mon-Sun weeks, newest first: workouts, sets, training time (seconds)."""
    from datetime import date as _date, timedelta as _td
    monday = today - _td(days=today.weekday())
    start = monday - _td(weeks=weeks - 1)
    rows = {}
    settings = get_settings(conn, user_id)
    for s in conn.execute("SELECT id, started_at, ended_at, calories FROM workout_sessions WHERE user_id = ?"
                          " AND started_at >= ?", (user_id, start.isoformat())).fetchall():
        d = _date.fromisoformat(s["started_at"][:10])
        wk = d - _td(days=d.weekday())
        n_sets, last_end = conn.execute(
            "SELECT COUNT(*), MAX(x.ended_at) FROM workout_sets x JOIN workout_session_exercises se"
            " ON se.id = x.session_exercise_id WHERE se.session_id = ? AND x.user_id = ?",
            (s["id"], user_id)).fetchone()
        if not n_sets:
            continue
        end = s["ended_at"] or last_end
        r = rows.setdefault(wk, {"workouts": 0, "sets": 0, "time_sec": 0, "kcal": None})
        secs = max(0, _secs(s["started_at"], end))
        r["workouts"] += 1
        r["sets"] += n_sets
        r["time_sec"] += secs
        kcal = s["calories"] if s["calories"] is not None else estimate_calories(settings, secs)
        if kcal is not None:
            r["kcal"] = (r["kcal"] or 0) + kcal
    return [{"week": (monday - _td(weeks=i)).isoformat(),
             **rows.get(monday - _td(weeks=i), {"workouts": 0, "sets": 0, "time_sec": 0, "kcal": None})}
            for i in range(weeks)]


# --- phase 3: settings & calories
#
# Estimate = MET x body weight (kg) x hours, the standard MET method. MET
# values from the 2024 Adult Compendium of Physical Activities: 02054
# resistance training, multiple exercises 8-15 reps (3.5); 02052 squats /
# deadlift (5.0); powerlifting / bodybuilding, vigorous effort (6.0).
# A rough estimate: the user can overwrite it per workout (sessions.calories).

MET = {"light": 3.5, "moderate": 5.0, "vigorous": 6.0}


def get_settings(conn, user_id: int) -> dict:
    row = conn.execute("SELECT body_weight, unit, intensity FROM workout_settings WHERE user_id = ?",
                       (user_id,)).fetchone()
    return dict(row) if row else {"body_weight": None, "unit": "lb", "intensity": "moderate"}


def save_settings(conn, user_id: int, s) -> None:
    conn.execute("INSERT INTO workout_settings (user_id, body_weight, unit, intensity) VALUES (?,?,?,?)"
                 " ON CONFLICT(user_id) DO UPDATE SET body_weight=excluded.body_weight, unit=excluded.unit,"
                 " intensity=excluded.intensity", (user_id, s.body_weight, s.unit, s.intensity))
    conn.commit()


def estimate_calories(settings: dict, duration_sec: int) -> int | None:
    bw = settings.get("body_weight")
    if not bw or not duration_sec:
        return None
    kg = bw if settings.get("unit") == "kg" else bw / LB_PER_KG
    return round(MET[settings.get("intensity") or "moderate"] * kg * duration_sec / 3600)


def set_calories(conn, user_id: int, session_id: int, calories: int | None) -> bool:
    cur = conn.execute("UPDATE workout_sessions SET calories = ? WHERE id = ? AND user_id = ?",
                       (calories, session_id, user_id))
    conn.commit()
    return cur.rowcount > 0


# --- phase 3: supersets
#
# A superset is a run of consecutive exercises sharing group_id (the id of
# the run's first member). Edits go through "linked to the one above" flags,
# then group ids are rewritten, so runs of one never keep a group id.

def _regroup(conn, table: str, parent_col: str, parent_id: int, links: dict[int, bool]) -> None:
    rows = conn.execute(f"SELECT id, group_id FROM {table} WHERE {parent_col} = ? ORDER BY position, id",
                        (parent_id,)).fetchall()
    flags, prev_gid = [], object()
    for r in rows:
        flags.append(links.get(r["id"], r["group_id"] is not None and r["group_id"] == prev_gid))
        prev_gid = r["group_id"]
    runs, cur = [], []
    for r, linked in zip(rows, flags):
        if linked and cur:
            cur.append(r["id"])
        else:
            if cur:
                runs.append(cur)
            cur = [r["id"]]
    if cur:
        runs.append(cur)
    for run in runs:
        gid = run[0] if len(run) > 1 else None
        conn.executemany(f"UPDATE {table} SET group_id = ? WHERE id = ?", [(gid, i) for i in run])


def set_superset(conn, user_id: int, se_id: int, with_previous: bool) -> int | None:
    """Link a session exercise to the one above (or unlink). Returns the session id."""
    se = _own_session_exercise(conn, user_id, se_id)
    if se is None:
        return None
    _regroup(conn, "workout_session_exercises", "session_id", se["session_id"], {se_id: with_previous})
    conn.commit()
    return se["session_id"]


# --- phase 3: routines (saved workouts)

def list_routines(conn, user_id: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM workout_routines WHERE user_id = ? ORDER BY name COLLATE NOCASE",
                          (user_id,)).fetchall():
        out.append(_routine(conn, r))
    return out


def _routine(conn, r) -> dict:
    items, prev_gid = [], object()
    for i in conn.execute(
            "SELECT i.*, e.name, e.unit, e.track_reps, e.track_weight, e.default_rest_sec, e.active"
            " FROM workout_routine_items i JOIN workout_exercises e ON e.id = i.exercise_id"
            " WHERE i.routine_id = ? ORDER BY i.position", (r["id"],)):
        items.append({"exercise_id": i["exercise_id"], "name": i["name"], "unit": i["unit"],
                      "track_reps": bool(i["track_reps"]), "track_weight": bool(i["track_weight"]),
                      "target_sets": i["target_sets"], "target_reps": i["target_reps"],
                      "target_weight": i["target_weight"], "rest_sec": i["rest_sec"],
                      "rest": i["rest_sec"] if i["rest_sec"] is not None else i["default_rest_sec"],
                      "group_id": i["group_id"],
                      "superset_with_previous": i["group_id"] is not None and i["group_id"] == prev_gid})
        prev_gid = i["group_id"]
    return {"id": r["id"], "name": r["name"], "notes": r["notes"], "items": items}


def get_routine(conn, user_id: int, routine_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM workout_routines WHERE id = ? AND user_id = ?",
                     (routine_id, user_id)).fetchone()
    return _routine(conn, r) if r else None


def save_routine(conn, user_id: int, rt, routine_id: int | None = None) -> int | None:
    """Create or replace a routine. Raises DuplicateName / ValueError (an
    exercise that isn't the user's)."""
    if conn.execute("SELECT 1 FROM workout_routines WHERE user_id = ? AND name = ? COLLATE NOCASE"
                    " AND id IS NOT ?", (user_id, rt.name, routine_id)).fetchone():
        raise DuplicateName(f"You already have a routine called {rt.name}")
    own = {r[0] for r in conn.execute("SELECT id FROM workout_exercises WHERE user_id = ?", (user_id,))}
    if any(i.exercise_id not in own for i in rt.items):
        raise ValueError("unknown exercise")
    if routine_id is None:
        routine_id = conn.execute("INSERT INTO workout_routines (user_id, name, notes) VALUES (?,?,?)",
                                  (user_id, rt.name, rt.notes)).lastrowid
    elif conn.execute("UPDATE workout_routines SET name = ?, notes = ? WHERE id = ? AND user_id = ?",
                      (rt.name, rt.notes, routine_id, user_id)).rowcount == 0:
        return None
    conn.execute("DELETE FROM workout_routine_items WHERE routine_id = ?", (routine_id,))
    links = {}
    for pos, i in enumerate(rt.items, 1):
        item_id = conn.execute(
            "INSERT INTO workout_routine_items (routine_id, exercise_id, position, target_sets, target_reps,"
            " target_weight, rest_sec) VALUES (?,?,?,?,?,?,?)",
            (routine_id, i.exercise_id, pos, i.target_sets, i.target_reps, i.target_weight, i.rest_sec)).lastrowid
        links[item_id] = i.superset_with_previous and pos > 1
    _regroup(conn, "workout_routine_items", "routine_id", routine_id, links)
    conn.commit()
    return routine_id


def delete_routine(conn, user_id: int, routine_id: int) -> None:
    conn.execute("DELETE FROM workout_routines WHERE id = ? AND user_id = ?", (routine_id, user_id))
    conn.commit()


def start_routine(conn, user_id: int, routine_id: int, now: datetime) -> int | None:
    """Start a workout from a routine: its exercises, order, supersets,
    targets and rests. If a workout is already in progress, return that one."""
    rt = get_routine(conn, user_id, routine_id)
    if rt is None:
        return None
    current = active_session(conn, user_id, now)
    if current:
        return current["id"]
    sid = conn.execute("INSERT INTO workout_sessions (user_id, started_at, routine_id) VALUES (?,?,?)",
                       (user_id, _iso(now), routine_id)).lastrowid
    gid_map = {}
    for pos, i in enumerate(rt["items"], 1):
        se_id = conn.execute(
            "INSERT INTO workout_session_exercises (session_id, exercise_id, position, target_rest_sec,"
            " target_sets, target_reps, target_weight) VALUES (?,?,?,?,?,?,?)",
            (sid, i["exercise_id"], pos, i["rest"], i["target_sets"], i["target_reps"],
             i["target_weight"])).lastrowid
        if i["group_id"] is not None:
            gid_map.setdefault(i["group_id"], se_id)
            conn.execute("UPDATE workout_session_exercises SET group_id = ? WHERE id = ?",
                         (gid_map[i["group_id"]], se_id))
    conn.commit()
    return sid


def routine_from_session(conn, user_id: int, session_id: int, name: str) -> int | None:
    """Save a workout as a routine: same exercises, order, supersets and
    rests; targets = number of sets done, the first set's reps, the top weight."""
    from .schemas import RoutineIn, RoutineItemIn
    d = session_detail(conn, user_id, session_id)
    if d is None or not d["exercises"]:
        return None
    items, prev_gid = [], object()
    for e in d["exercises"]:
        sets = e["sets"]
        items.append(RoutineItemIn(
            exercise_id=e["exercise_id"], target_sets=min(20, len(sets)) or e.get("target_sets") or 3,
            target_reps=(sets[0]["reps"] or None) if sets and e["track_reps"] else e.get("target_reps"),
            target_weight=e["summary"]["top_weight"] if e["track_weight"] else None,
            rest_sec=e["target_rest_sec"],
            superset_with_previous=e["group_id"] is not None and e["group_id"] == prev_gid))
        prev_gid = e["group_id"]
    return save_routine(conn, user_id, RoutineIn(name=name, items=items))


# --- reminders
#
# Fire on the reminder's weekdays at at_time in its zone (up to 30 min late
# after a restart), at most once a local day. Skipped (and marked for today)
# when the target was already done today, or the weekly goal is already met.
# "Done" = a set of that exercise logged that local day, or a workout started
# from that routine with at least one set.

REMINDER_WINDOW = timedelta(minutes=30)


def _local(now_utc: datetime, tz: str) -> datetime:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    aware = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=ZoneInfo("UTC"))
    return aware.astimezone(zone).replace(tzinfo=None)


def _reminder(conn, r) -> dict:
    if r["exercise_id"]:
        name = conn.execute("SELECT name FROM workout_exercises WHERE id = ?", (r["exercise_id"],)).fetchone()
    else:
        name = conn.execute("SELECT name FROM workout_routines WHERE id = ?", (r["routine_id"],)).fetchone()
    return {"id": r["id"], "exercise_id": r["exercise_id"], "routine_id": r["routine_id"],
            "target": "e:%d" % r["exercise_id"] if r["exercise_id"] else "r:%d" % r["routine_id"],
            "name": name[0] if name else "?", "kind": "exercise" if r["exercise_id"] else "routine",
            "days": [int(d) for d in r["days"]], "at_time": r["at_time"], "tz": r["tz"],
            "weekly_goal": r["weekly_goal"], "active": bool(r["active"]), "user_id": r["user_id"]}


def list_reminders(conn, user_id: int) -> list[dict]:
    return [_reminder(conn, r) for r in conn.execute(
        "SELECT * FROM workout_reminders WHERE user_id = ? ORDER BY active DESC, at_time", (user_id,))]


def get_reminder(conn, user_id: int, reminder_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM workout_reminders WHERE id = ? AND user_id = ?",
                     (reminder_id, user_id)).fetchone()
    return _reminder(conn, r) if r else None


def save_reminder(conn, user_id: int, m, tz: str, reminder_id: int | None = None) -> int | None:
    """Create or update. Raises ValueError if the target isn't the user's."""
    if m.exercise_id is not None and get_exercise(conn, user_id, m.exercise_id) is None:
        raise ValueError("unknown exercise")
    if m.routine_id is not None and not conn.execute(
            "SELECT 1 FROM workout_routines WHERE id = ? AND user_id = ?", (m.routine_id, user_id)).fetchone():
        raise ValueError("unknown routine")
    vals = (m.exercise_id, m.routine_id, "".join(map(str, m.days)), m.at_time, tz, m.weekly_goal)
    if reminder_id is None:
        reminder_id = conn.execute(
            "INSERT INTO workout_reminders (exercise_id, routine_id, days, at_time, tz, weekly_goal, user_id)"
            " VALUES (?,?,?,?,?,?,?)", (*vals, user_id)).lastrowid
    elif conn.execute("UPDATE workout_reminders SET exercise_id=?, routine_id=?, days=?, at_time=?, tz=?,"
                      " weekly_goal=?, last_sent=NULL WHERE id=? AND user_id=?",
                      (*vals, reminder_id, user_id)).rowcount == 0:
        return None
    conn.commit()
    return reminder_id


def set_reminder_active(conn, user_id: int, reminder_id: int, active: bool) -> None:
    conn.execute("UPDATE workout_reminders SET active = ? WHERE id = ? AND user_id = ?",
                 (int(active), reminder_id, user_id))
    conn.commit()


def delete_reminder(conn, user_id: int, reminder_id: int) -> None:
    conn.execute("DELETE FROM workout_reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id))
    conn.commit()


def done_days(conn, rem: dict, start: str, end: str) -> set[str]:
    """Local dates in [start, end] (YYYY-MM-DD) on which the reminder's target was done."""
    if rem["exercise_id"]:
        rows = conn.execute(
            "SELECT DISTINCT substr(x.started_at, 1, 10) FROM workout_sets x"
            " JOIN workout_session_exercises se ON se.id = x.session_exercise_id"
            " WHERE x.user_id = ? AND se.exercise_id = ? AND substr(x.started_at, 1, 10) BETWEEN ? AND ?",
            (rem["user_id"], rem["exercise_id"], start, end))
    else:
        rows = conn.execute(
            "SELECT DISTINCT substr(s.started_at, 1, 10) FROM workout_sessions s"
            " WHERE s.user_id = ? AND s.routine_id = ? AND substr(s.started_at, 1, 10) BETWEEN ? AND ?"
            " AND EXISTS (SELECT 1 FROM workout_sets x JOIN workout_session_exercises se"
            "   ON se.id = x.session_exercise_id WHERE se.session_id = s.id)",
            (rem["user_id"], rem["routine_id"], start, end))
    return {r[0] for r in rows}


def reminder_status(conn, rem: dict, today) -> dict:
    """This week (Mon-Sun): days done, goal, done today, scheduled today."""
    monday = today - timedelta(days=today.weekday())
    done = done_days(conn, rem, monday.isoformat(), (monday + timedelta(days=6)).isoformat())
    goal = rem["weekly_goal"] or len(rem["days"])
    skipped = {r[0] for r in conn.execute(
        "SELECT day FROM workout_reminder_skips WHERE reminder_id = ? AND day BETWEEN ? AND ?",
        (rem["id"], monday.isoformat(), (monday + timedelta(days=6)).isoformat()))}
    return {**rem, "done_week": len(done), "goal": goal, "done_today": today.isoformat() in done,
            "skipped_today": today.isoformat() in skipped,
            "week_skipped": [(monday + timedelta(days=i)).isoformat() in skipped for i in range(7)],
            "today": today.weekday() in rem["days"], "goal_met": len(done) >= goal,
            "week_days": [(monday + timedelta(days=i)).isoformat() in done for i in range(7)]}


def due_reminders(conn, now_utc: datetime) -> list[dict]:
    due = []
    for r in conn.execute("SELECT * FROM workout_reminders WHERE active = 1").fetchall():
        now = _local(now_utc, r["tz"])
        if str(now.weekday()) not in r["days"] or r["last_sent"] == now.date().isoformat():
            continue
        at = datetime.combine(now.date(), datetime.strptime(r["at_time"], "%H:%M").time())
        if at <= now < at + REMINDER_WINDOW:
            due.append({**_reminder(conn, r), "local_now": now})
    return due


def send_reminders(conn, now_utc: datetime) -> None:
    """Scheduler job (once a minute, idempotent)."""
    import logging
    from ... import push  # late: keeps this module importable without push deps
    log = logging.getLogger("bp_tracker.workout")
    for r in due_reminders(conn, now_utc):
        st = reminder_status(conn, r, r["local_now"].date())
        if st["done_today"] or st["goal_met"] or st["skipped_today"]:
            log.info("workout reminder %s not sent (done, skipped today or goal met)", r["id"])
        else:
            body = (f"{st['done_week']} of {st['goal']} days done this week. Tap to start."
                    if st["goal"] > 1 else "Tap to start.")
            n = push.send_to_user(conn, r["user_id"], f"Time for {r['name']}", body,
                                  f"/workout?remind={r['id']}")
            log.info("workout reminder %s for user %s sent to %d device(s)", r["id"], r["user_id"], n)
        conn.execute("UPDATE workout_reminders SET last_sent = ? WHERE id = ?",
                     (r["local_now"].date().isoformat(), r["id"]))
        conn.commit()


def skip_reminder(conn, user_id: int, reminder_id: int, day: str, skip: bool = True) -> bool:
    """Skip today's workout for a reminder (no push, not counted toward the
    goal), or undo the skip."""
    if get_reminder(conn, user_id, reminder_id) is None:
        return False
    if skip:
        conn.execute("INSERT OR IGNORE INTO workout_reminder_skips (reminder_id, day) VALUES (?, ?)",
                     (reminder_id, day))
    else:
        conn.execute("DELETE FROM workout_reminder_skips WHERE reminder_id = ? AND day = ?", (reminder_id, day))
    conn.commit()
    return True


def start_reminder(conn, user_id: int, reminder_id: int, now: datetime) -> int | None:
    """Start (or continue) a workout for a reminder: the routine, or a
    workout with the exercise added."""
    rem = get_reminder(conn, user_id, reminder_id)
    if rem is None:
        return None
    if rem["routine_id"]:
        return start_routine(conn, user_id, rem["routine_id"], now)
    sid = start_session(conn, user_id, now)
    add_session_exercise(conn, user_id, sid, rem["exercise_id"])
    return sid
