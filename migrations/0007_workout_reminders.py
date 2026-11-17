"""Workout reminders: on chosen weekdays at a local time, for an exercise or
a routine, with an optional weekly goal (stop once it's met)."""
from yoyo import step

steps = [
    step(
        """
        CREATE TABLE workout_reminders (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            exercise_id INTEGER REFERENCES workout_exercises(id) ON DELETE CASCADE,
            routine_id  INTEGER REFERENCES workout_routines(id) ON DELETE CASCADE,
            days        TEXT NOT NULL,              -- weekdays, Mon=0: e.g. '01234'
            at_time     TEXT NOT NULL,              -- local 'HH:MM'
            tz          TEXT NOT NULL,              -- IANA zone at_time is in
            weekly_goal INTEGER,                    -- days per week; NULL = every chosen day
            active      INTEGER NOT NULL DEFAULT 1,
            last_sent   TEXT,                       -- local date it last fired (or was skipped)
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK ((exercise_id IS NULL) <> (routine_id IS NULL))
        )
        """,
        "DROP TABLE workout_reminders",
    ),
]
