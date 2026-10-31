"""Initial schema: users + readings."""
from yoyo import step

steps = [
    step(
        """
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "DROP TABLE users",
    ),
    step(
        """
        CREATE TABLE readings (
            id        INTEGER PRIMARY KEY,
            user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            taken_at  TEXT NOT NULL,  -- ISO local datetime, e.g. 2026-10-31T08:15
            systolic  INTEGER NOT NULL,
            diastolic INTEGER NOT NULL,
            pulse     INTEGER,
            note      TEXT
        )
        """,
        "DROP TABLE readings",
    ),
    step(
        "CREATE INDEX idx_readings_user_taken ON readings(user_id, taken_at)",
        "DROP INDEX idx_readings_user_taken",
    ),
]
