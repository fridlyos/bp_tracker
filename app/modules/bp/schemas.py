from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator


class ReadingIn(BaseModel):
    """One measurement. taken_at omitted -> now (in the user's local time)."""
    taken_at: datetime | None = None
    systolic: int = Field(ge=50, le=260)
    diastolic: int = Field(ge=30, le=160)
    pulse: int | None = Field(None, ge=30, le=220)
    note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _sys_above_dia(self):
        if self.systolic <= self.diastolic:
            raise ValueError("systolic must be higher than diastolic")
        if self.note is not None:
            self.note = self.note.strip() or None
        return self


class Reading(BaseModel):
    id: int
    taken_at: str  # ISO local datetime, minute precision: 2026-10-31T08:15
    systolic: int
    diastolic: int
    pulse: int | None = None
    note: str | None = None


class PurgeIn(BaseModel):
    before: date  # delete readings taken strictly before this day
    dry_run: bool = False
