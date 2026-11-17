"""Workout phase 3: routines (saved workouts), per-exercise targets,
supersets in use, calories per workout, body-weight settings."""
from yoyo import step

steps = [
    step(
        """
        CREATE TABLE workout_routines (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name       TEXT NOT NULL,
            notes      TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (user_id, name)
        )
        """,
        "DROP TABLE workout_routines",
    ),
    step(
        """
        CREATE TABLE workout_routine_items (
            id            INTEGER PRIMARY KEY,
            routine_id    INTEGER NOT NULL REFERENCES workout_routines(id) ON DELETE CASCADE,
            exercise_id   INTEGER NOT NULL REFERENCES workout_exercises(id) ON DELETE CASCADE,
            position      INTEGER NOT NULL,
            group_id      INTEGER,          -- same value = superset (id of the run's first item)
            target_sets   INTEGER,
            target_reps   INTEGER,
            target_weight REAL,             -- in the exercise's unit
            rest_sec      INTEGER           -- NULL = the exercise's default
        )
        """,
        "DROP TABLE workout_routine_items",
    ),
    step("ALTER TABLE workout_session_exercises ADD COLUMN target_sets INTEGER",
         "ALTER TABLE workout_session_exercises DROP COLUMN target_sets"),
    step("ALTER TABLE workout_session_exercises ADD COLUMN target_reps INTEGER",
         "ALTER TABLE workout_session_exercises DROP COLUMN target_reps"),
    step("ALTER TABLE workout_session_exercises ADD COLUMN target_weight REAL",
         "ALTER TABLE workout_session_exercises DROP COLUMN target_weight"),
    step("ALTER TABLE workout_sessions ADD COLUMN routine_id INTEGER",  # informational; no FK
         "ALTER TABLE workout_sessions DROP COLUMN routine_id"),
    step("ALTER TABLE workout_sessions ADD COLUMN calories INTEGER",    # NULL = use the estimate
         "ALTER TABLE workout_sessions DROP COLUMN calories"),
    step(
        """
        CREATE TABLE workout_settings (
            user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            body_weight REAL,
            unit        TEXT NOT NULL DEFAULT 'lb' CHECK (unit IN ('lb', 'kg')),
            intensity   TEXT NOT NULL DEFAULT 'moderate' CHECK (intensity IN ('light', 'moderate', 'vigorous'))
        )
        """,
        "DROP TABLE workout_settings",
    ),
]
