import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_TIMES = 6
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
Kind = Literal["medication", "supplement"]
KINDS: tuple[Kind, ...] = ("medication", "supplement")


class MedIn(BaseModel):
    """A medication or supplement. No times = taken as needed (no reminders)."""
    name: str = Field(max_length=80)
    kind: Kind = "medication"
    dose: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=300)
    times: list[str] = Field(default_factory=list)

    @field_validator("name", "dose", "notes", mode="before")
    @classmethod
    def _strip(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator("name", mode="before")
    @classmethod
    def _name_required(cls, v):
        if not (isinstance(v, str) and v.strip()):
            raise ValueError("name is required")
        return v

    @field_validator("times", mode="before")
    @classmethod
    def _times(cls, v):
        out = sorted({t.strip()[:5] for t in (v or []) if t and t.strip()})
        bad = [t for t in out if not _HHMM.match(t)]
        if bad:
            raise ValueError(f"not a time: {bad[0]}")
        if len(out) > MAX_TIMES:
            raise ValueError(f"at most {MAX_TIMES} times a day")
        return out


class Med(BaseModel):
    id: int
    name: str
    kind: Kind = "medication"
    dose: str | None = None
    notes: str | None = None
    tz: str
    active: bool = True
    times: list[str] = []


class DoseIn(BaseModel):
    med_id: int
    status: str = Field("taken", pattern="^(taken|skipped)$")
    slot: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")  # None = as needed
