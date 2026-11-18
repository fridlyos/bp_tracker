"""Body weight log (lb or kg). Pages under /weight, JSON API under /api/weight."""
from .. import Module, NavItem
from . import api, service, web

_SCALE = ('<rect x="3" y="3" width="18" height="18" rx="4.5"/>'
          '<path d="M6.5 11a7 7 0 0 1 11 0"/><path d="M12 11l2.2-3.2"/><path d="M9 16.5h6"/>')

MODULE = Module(
    key="weight",
    name="Weight",
    description="Log your weight in pounds or kilograms, see the trend and your goal.",
    icon=_SCALE,
    nav=(
        NavItem("/weight", "Today", '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>'),
        NavItem("/weight/trends", "Trends", '<path d="M3 17l5-6 4 3 6-8 3 3"/><path d="M3 21h18"/>'),
        NavItem("/weight/history", "History", '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/>'
                '<circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>'),
        NavItem("/weight/settings", "Settings", '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/>'
                '<circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>'),
    ),
    routers=(web.router, api.router),
    jobs=(service.send_reminders,),
    tz_tables=("weight_reminders",),
)
