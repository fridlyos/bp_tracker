"""Workout module: exercise types, sessions, exercises in a session, sets.

Times are naive local datetimes with seconds ('YYYY-MM-DDTHH:MM:SS'), taken
from the phone's clock: set duration (time under tension) and rest are
computed from them, never stored.
"""
from yoyo import step

steps = [
    step(
        """
        CREATE TABLE workout_exercises (
            id               INTEGER PRIMARY KEY,
            user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name             TEXT NOT NULL,
            category         TEXT,                          -- free text: push, pull, legs, core, ...
            equipment        TEXT,                          -- free text: barbell, kettlebell, ...
            track_reps       INTEGER NOT NULL DEFAULT 1,
            track_weight     INTEGER NOT NULL DEFAULT 1,
            unit             TEXT NOT NULL DEFAULT 'lb' CHECK (unit IN ('lb', 'kg')),
            default_rest_sec INTEGER NOT NULL DEFAULT 90,
            notes            TEXT,
            active           INTEGER NOT NULL DEFAULT 1,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (user_id, name)
        )
        """,
        "DROP TABLE workout_exercises",
    ),
    step(
        """
        CREATE TABLE workout_sessions (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,     -- local 'YYYY-MM-DDTHH:MM:SS'
            ended_at   TEXT,              -- NULL while in progress
            notes      TEXT
        )
        """,
        "DROP TABLE workout_sessions",
    ),
    step(
        "CREATE INDEX idx_workout_sessions_user ON workout_sessions(user_id, started_at)",
        "DROP INDEX idx_workout_sessions_user",
    ),
    step(
        """
        CREATE TABLE workout_session_exercises (
            id              INTEGER PRIMARY KEY,
            session_id      INTEGER NOT NULL REFERENCES workout_sessions(id) ON DELETE CASCADE,
            exercise_id     INTEGER NOT NULL REFERENCES workout_exercises(id) ON DELETE CASCADE,
            position        INTEGER NOT NULL,
            group_id        INTEGER,          -- same value = superset / circuit (UI later)
            target_rest_sec INTEGER NOT NULL
        )
        """,
        "DROP TABLE workout_session_exercises",
    ),
    step(
        """
        CREATE TABLE workout_sets (
            id                  INTEGER PRIMARY KEY,
            uid                 TEXT NOT NULL UNIQUE,   -- client UUID: offline sync is an idempotent upsert
            user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_exercise_id INTEGER NOT NULL REFERENCES workout_session_exercises(id) ON DELETE CASCADE,
            reps                INTEGER,
            weight              REAL,
            unit                TEXT NOT NULL DEFAULT 'lb' CHECK (unit IN ('lb', 'kg')),
            started_at          TEXT NOT NULL,          -- local 'YYYY-MM-DDTHH:MM:SS'
            ended_at            TEXT NOT NULL,
            rpe                 REAL,
            tempo               TEXT,                   -- e.g. '3-1-1-0'
            note                TEXT
        )
        """,
        "DROP TABLE workout_sets",
    ),
    step(
        "CREATE INDEX idx_workout_sets_se ON workout_sets(session_exercise_id, started_at)",
        "DROP INDEX idx_workout_sets_se",
    ),
]
