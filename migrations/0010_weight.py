"""Weight module: weigh-ins (stored in kg, with the unit typed), settings
(display unit, goal), daily weigh-in reminders."""
from yoyo import step

steps = [
    step(
        """
        CREATE TABLE weight_entries (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            taken_at   TEXT NOT NULL,              -- local 'YYYY-MM-DDTHH:MM'
            kg         REAL NOT NULL,              -- canonical value
            unit       TEXT NOT NULL DEFAULT 'lb' CHECK (unit IN ('lb', 'kg')),  -- as entered
            note       TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "DROP TABLE weight_entries",
    ),
    step("CREATE INDEX idx_weight_entries_user ON weight_entries(user_id, taken_at)",
         "DROP INDEX idx_weight_entries_user"),
    step(
        """
        CREATE TABLE weight_settings (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            unit    TEXT NOT NULL DEFAULT 'lb' CHECK (unit IN ('lb', 'kg')),
            goal_kg REAL
        )
        """,
        "DROP TABLE weight_settings",
    ),
    step(
        """
        CREATE TABLE weight_reminders (
            id            INTEGER PRIMARY KEY,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            at_time       TEXT NOT NULL,           -- local 'HH:MM'
            tz            TEXT NOT NULL,
            last_reminded TEXT,                    -- local date it last fired
            UNIQUE (user_id, at_time)
        )
        """,
        "DROP TABLE weight_reminders",
    ),
]
