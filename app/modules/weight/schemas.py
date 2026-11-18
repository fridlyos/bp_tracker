"""Weight validation. Weights are typed in lb or kg and stored in kg."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Unit = Literal["lb", "kg"]
LB_PER_KG = 2.20462
MIN_KG, MAX_KG = 20.0, 400.0  # ~44-880 lb


def to_kg(value: float, unit: str) -> float:
    return value / LB_PER_KG if unit == "lb" else value


def from_kg(kg: float | None, unit: str) -> float | None:
    if kg is None:
        return None
    return kg * LB_PER_KG if unit == "lb" else kg


def _range_text(unit: str) -> str:
    lo, hi = (round(from_kg(MIN_KG, unit)), round(from_kg(MAX_KG, unit)))
    return f"{lo}-{hi} {unit}"


class EntryIn(BaseModel):
    taken_at: datetime | None = None
    weight: float
    unit: Unit = "lb"
    note: str | None = Field(None, max_length=300)

    @field_validator("note", mode="before")
    @classmethod
    def _strip(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator("taken_at")
    @classmethod
    def _minutes(cls, v):
        return v.replace(second=0, microsecond=0) if v else v

    @model_validator(mode="after")
    def _range(self):
        if not MIN_KG <= to_kg(self.weight, self.unit) <= MAX_KG:
            raise ValueError(f"weight must be {_range_text(self.unit)}")
        return self

    @property
    def kg(self) -> float:
        return round(to_kg(self.weight, self.unit), 3)


class Entry(BaseModel):
    id: int
    taken_at: str
    kg: float
    unit: Unit
    note: str | None = None


class SettingsIn(BaseModel):
    unit: Unit = "lb"
    goal: float | None = None  # in `unit`

    @model_validator(mode="after")
    def _goal(self):
        if self.goal is not None and not MIN_KG <= to_kg(self.goal, self.unit) <= MAX_KG:
            raise ValueError(f"goal must be {_range_text(self.unit)}")
        return self
