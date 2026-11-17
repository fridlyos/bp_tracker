import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Unit = Literal["lb", "kg"]
_TEMPO = re.compile(r"^\d{1,2}([-/]?\d{1,2}){2,3}$")
_UID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MAX_SET_SEC = 60 * 60  # a single set longer than an hour is a forgotten timer


def _strip(v):
    return (v.strip() or None) if isinstance(v, str) else v


class ExerciseIn(BaseModel):
    """A user-defined exercise type. Reps/weight switches decide what a set
    asks for: weight off = bodyweight, reps off = timed hold (plank)."""
    name: str = Field(max_length=80)
    category: str | None = Field(None, max_length=40)
    equipment: str | None = Field(None, max_length=40)
    track_reps: bool = True
    track_weight: bool = True
    unit: Unit = "lb"
    default_rest_sec: int = Field(90, ge=0, le=1800)
    notes: str | None = Field(None, max_length=300)

    @field_validator("name", "category", "equipment", "notes", mode="before")
    @classmethod
    def _strip(cls, v):
        return _strip(v)

    @field_validator("name", mode="before")
    @classmethod
    def _name_required(cls, v):
        if not (isinstance(v, str) and v.strip()):
            raise ValueError("name is required")
        return v


class Exercise(ExerciseIn):
    id: int
    active: bool = True


def tempo_seconds(tempo: str | None) -> int | None:
    """'3-1-1-0' / '3110' -> 5 seconds per rep (sum of the phases)."""
    if not tempo:
        return None
    parts = re.split(r"[-/]", tempo) if re.search(r"[-/]", tempo) else list(tempo)
    try:
        return sum(int(p) for p in parts)
    except ValueError:
        return None


class SetIn(BaseModel):
    """One set, sent by the live page when the set ends (or later from the
    offline queue). Upserted by `uid`, so resending is harmless."""
    uid: str
    session_exercise_id: int
    started_at: datetime
    ended_at: datetime
    reps: int | None = Field(None, ge=0, le=1000)
    weight: float | None = Field(None, ge=0, le=2000)
    unit: Unit = "lb"
    rpe: float | None = Field(None, ge=1, le=10)
    tempo: str | None = Field(None, max_length=11)
    note: str | None = Field(None, max_length=200)

    @field_validator("tempo", "note", mode="before")
    @classmethod
    def _strip(cls, v):
        return _strip(v)

    @field_validator("uid")
    @classmethod
    def _uid(cls, v):
        if not _UID.match(v):
            raise ValueError("bad uid")
        return v

    @field_validator("tempo")
    @classmethod
    def _tempo(cls, v):
        if v is not None and not _TEMPO.match(v):
            raise ValueError("tempo looks like 3-1-1-0")
        return v

    @field_validator("started_at", "ended_at")
    @classmethod
    def _naive(cls, v: datetime):
        return v.replace(tzinfo=None, microsecond=0)

    @model_validator(mode="after")
    def _order(self):
        secs = (self.ended_at - self.started_at).total_seconds()
        if secs < 0:
            raise ValueError("set ends before it starts")
        if secs > MAX_SET_SEC:
            raise ValueError("set is longer than an hour")
        return self


class SetUpdate(BaseModel):
    """Fix the numbers of a logged set (the times stay)."""
    reps: int | None = Field(None, ge=0, le=1000)
    weight: float | None = Field(None, ge=0, le=2000)
    rpe: float | None = Field(None, ge=1, le=10)


class SessionExerciseIn(BaseModel):
    exercise_id: int
    target_rest_sec: int | None = Field(None, ge=0, le=1800)  # None = exercise default


# --- phase 3: routines, settings

MAX_ROUTINE_ITEMS = 20
Intensity = Literal["light", "moderate", "vigorous"]


class RoutineItemIn(BaseModel):
    exercise_id: int
    target_sets: int | None = Field(3, ge=1, le=20)
    target_reps: int | None = Field(None, ge=1, le=200)
    target_weight: float | None = Field(None, ge=0, le=2000)
    rest_sec: int | None = Field(None, ge=0, le=1800)
    superset_with_previous: bool = False


class RoutineIn(BaseModel):
    name: str = Field(max_length=60)
    notes: str | None = Field(None, max_length=300)
    items: list[RoutineItemIn] = Field(default_factory=list)

    @field_validator("name", "notes", mode="before")
    @classmethod
    def _strip(cls, v):
        return _strip(v)

    @field_validator("name", mode="before")
    @classmethod
    def _name_required(cls, v):
        if not (isinstance(v, str) and v.strip()):
            raise ValueError("name is required")
        return v

    @field_validator("items")
    @classmethod
    def _items(cls, v):
        if not v:
            raise ValueError("add at least one exercise")
        if len(v) > MAX_ROUTINE_ITEMS:
            raise ValueError(f"at most {MAX_ROUTINE_ITEMS} exercises")
        return v


class SettingsIn(BaseModel):
    body_weight: float | None = Field(None, ge=20, le=1000)
    unit: Unit = "lb"
    intensity: Intensity = "moderate"


class CaloriesIn(BaseModel):
    calories: int | None = Field(None, ge=0, le=5000)  # None = back to the estimate


class GroupIn(BaseModel):
    superset_with_previous: bool
