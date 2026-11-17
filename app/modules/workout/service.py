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
        for r in conn.execute("SELECT * FROM workout_sets WHERE session_exercise_id = ? AND user_id = ?"
                              " ORDER BY started_at", (se["id"], user_id)):
            sets.append(_set_dict(r, prev_end))
            prev_end = r["ended_at"]
        exercises.append({
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
