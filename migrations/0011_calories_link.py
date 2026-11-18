"""Calories module: per-user link to the separate calorie counter app."""
from yoyo import step

steps = [
    step(
        """
        CREATE TABLE calories_settings (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            url     TEXT NOT NULL
        )
        """,
        "DROP TABLE calories_settings",
    ),
]
