"""Calories: a launcher tile that links to the separate calorie counter app,
with today's workout burn and latest weight alongside. Page under /calories."""
from .. import Module, NavItem
from . import web

_APPLE = ('<path d="M12 7.5c-1.6-1.6-4.6-1.8-6.2.3-1.7 2.3-.9 6.4 1.2 9.2 1.6 2.1 3 2.6 5 1.6'
          ' 2 1 3.4.5 5-1.6 2.1-2.8 2.9-6.9 1.2-9.2-1.6-2.1-4.6-1.9-6.2-.3z"/><path d="M12 7.5c0-2 1-3.6 3-4.3"/>')

MODULE = Module(
    key="calories",
    name="Calories",
    description="Open your calorie counter, with today's workout burn and weight at hand.",
    icon=_APPLE,
    nav=(NavItem("/calories", "Calories", _APPLE),),
    routers=(web.router,),
)
