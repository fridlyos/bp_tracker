"""Push notifications (core) + BP reminders."""
from yoyo import step

steps = [
    step(
        # small key/value store for app-wide settings (e.g. generated VAPID keys)
        "CREATE TABLE app_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "DROP TABLE app_kv",
    ),
    step(
        """
        CREATE TABLE push_subscriptions (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            endpoint   TEXT NOT NULL UNIQUE,
            p256dh     TEXT NOT NULL,
            auth       TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "DROP TABLE push_subscriptions",
    ),
    step(
        """
        CREATE TABLE bp_reminders (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            at_time    TEXT NOT NULL,  -- local wall-clock 'HH:MM'
            tz         TEXT NOT NULL,  -- IANA zone the time is in, e.g. America/New_York
            last_sent  TEXT,           -- local date 'YYYY-MM-DD' it last fired
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (user_id, at_time)
        )
        """,
        "DROP TABLE bp_reminders",
    ),
]
