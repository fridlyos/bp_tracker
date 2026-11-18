"""Workout reminders: days the user chose to skip (no push, shown as skipped)."""
from yoyo import step

steps = [
    step(
        """
        CREATE TABLE workout_reminder_skips (
            reminder_id INTEGER NOT NULL REFERENCES workout_reminders(id) ON DELETE CASCADE,
            day         TEXT NOT NULL,          -- local 'YYYY-MM-DD' in the reminder's zone
            PRIMARY KEY (reminder_id, day)
        )
        """,
        "DROP TABLE workout_reminder_skips",
    ),
]
