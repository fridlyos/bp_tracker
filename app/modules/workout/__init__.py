"""Strength training: exercise types you define, live workouts with a set
timer and a rest timer, history. Pages under /workout, JSON API under /api/workout."""
from .. import Module, NavItem
from . import api, web

_DUMBBELL = ('<path d="M6.5 6.5v11M17.5 6.5v11M3.5 9v6M20.5 9v6M6.5 12h11"/>')

MODULE = Module(
    key="workout",
    name="Workout",
    description="Log strength workouts: sets, reps, weight, time under tension and rest.",
    icon=_DUMBBELL,
    nav=(
        NavItem("/workout", "Workout", _DUMBBELL),
        NavItem("/workout/history", "History", '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
        NavItem("/workout/exercises", "Exercises", '<path d="M8 6h12M8 12h12M8 18h12"/>'
                '<circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>'),
    ),
    routers=(web.router, api.router),
)
