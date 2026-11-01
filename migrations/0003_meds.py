"""Medications module: meds, their daily times, and logged doses."""
from yoyo import step

steps = [
    step(
        """
        CREATE TABLE meds (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name       TEXT NOT NULL,
            dose       TEXT,              -- free text, e.g. '10 mg', '1 tablet'
            notes      TEXT,
            tz         TEXT NOT NULL,     -- IANA zone the times are in
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "DROP TABLE meds",
    ),
    step(
        """
        CREATE TABLE med_times (
            id            INTEGER PRIMARY KEY,
            med_id        INTEGER NOT NULL REFERENCES meds(id) ON DELETE CASCADE,
            at_time       TEXT NOT NULL,  -- local 'HH:MM'
            last_reminded TEXT,           -- local date the reminder last fired
            UNIQUE (med_id, at_time)
        )
        """,
        "DROP TABLE med_times",
    ),
    step(
        """
        CREATE TABLE med_doses (
            id            INTEGER PRIMARY KEY,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            med_id        INTEGER NOT NULL REFERENCES meds(id) ON DELETE CASCADE,
            scheduled_for TEXT,           -- local slot 'YYYY-MM-DDTHH:MM'; NULL = as-needed dose
            status        TEXT NOT NULL CHECK (status IN ('taken', 'skipped')),
            logged_at     TEXT NOT NULL,  -- local 'YYYY-MM-DDTHH:MM'
            UNIQUE (med_id, scheduled_for)
        )
        """,
        "DROP TABLE med_doses",
    ),
    step(
        "CREATE INDEX idx_med_doses_user_logged ON med_doses(user_id, logged_at)",
        "DROP INDEX idx_med_doses_user_logged",
    ),
]
