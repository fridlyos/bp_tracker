"""Medications module: tell medications and supplements apart."""
from yoyo import step

steps = [
    step(
        "ALTER TABLE meds ADD COLUMN kind TEXT NOT NULL DEFAULT 'medication'"
        " CHECK (kind IN ('medication', 'supplement'))",
        "ALTER TABLE meds DROP COLUMN kind",
    ),
]
