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
                    active=bool(row["active"]))


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
            e.default_rest_sec, e.notes)
    if exercise_id is None:
        exercise_id = conn.execute(
            "INSERT INTO workout_exercises (name, category, equipment, track_reps, track_weight, unit,"
            " default_rest_sec, notes, user_id) VALUES (?,?,?,?,?,?,?,?,?)", (*vals, user_id)).lastrowid
    elif conn.execute("UPDATE workout_exercises SET name=?, category=?, equipment=?, track_reps=?,"
                      " track_weight=?, unit=?, default_rest_sec=?, notes=? WHERE id=? AND user_id=?",
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
            "SELECT se.*, e.name, e.track_reps, e.track_weight, e.unit FROM workout_session_exercises se"
            " JOIN workout_exercises e ON e.id = se.exercise_id WHERE se.session_id = ? ORDER BY se.position",
            (session_id,)):
        sets, prev_end = [], None
        prs, best_before = session_prs(conn, user_id, se["exercise_id"], session_id)
        for r in conn.execute("SELECT * FROM workout_sets WHERE session_exercise_id = ? AND user_id = ?"
                              " ORDER BY started_at", (se["id"], user_id)):
            sets.append(_set_dict(r, prev_end) | {"prs": prs.get(r["uid"], [])})
            prev_end = r["ended_at"]
        exercises.append({
            "pr_kinds": list(pr_kinds(bool(se["track_reps"]), bool(se["track_weight"]))),
            "best_before": best_before,
            "id": se["id"], "exercise_id": se["exercise_id"], "name": se["name"],
            "track_reps": bool(se["track_reps"]), "track_weight": bool(se["track_weight"]),
            "unit": se["unit"], "target_rest_sec": se["target_rest_sec"], "group_id": se["group_id"],
            "sets": sets, "summary": _summary(sets),
            "previous": last_sets(conn, user_id, se["exercise_id"], before_session=session_id)})
    all_sets = [s for e in exercises for s in e["sets"]]
    end = sess["ended_at"] or (max(s["ended_at"] for s in all_sets) if all_sets else None)
    return {"id": sess["id"], "started_at": sess["started_at"], "ended_at": sess["ended_at"],
            "notes": sess["notes"], "in_progress": sess["ended_at"] is None,
            "duration_sec": _secs(sess["started_at"], end) if end else 0,
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
    for s in conn.execute("SELECT id, started_at, ended_at FROM workout_sessions WHERE user_id = ?"
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
        r = rows.setdefault(wk, {"workouts": 0, "sets": 0, "time_sec": 0})
        r["workouts"] += 1
        r["sets"] += n_sets
        r["time_sec"] += max(0, _secs(s["started_at"], end))
    return [{"week": (monday - _td(weeks=i)).isoformat(),
             **rows.get(monday - _td(weeks=i), {"workouts": 0, "sets": 0, "time_sec": 0})}
            for i in range(weeks)]
