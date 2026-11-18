"""Workout: a target hold time for timed exercises (wall sit 1:30)."""
from yoyo import step

steps = [
    step("ALTER TABLE workout_exercises ADD COLUMN target_duration_sec INTEGER",
         "ALTER TABLE workout_exercises DROP COLUMN target_duration_sec"),
]
