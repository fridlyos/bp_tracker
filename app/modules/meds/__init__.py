"""Medications: what to take when, a daily checklist, history and reminders.
Pages under /meds, JSON API under /api/meds."""
from .. import Module, NavItem
from . import api, service, web

MODULE = Module(
    key="meds",
    name="Medications",
    description="Track your medications and get reminded to take them.",
    # capsule pill
    icon='<rect x="2.5" y="8" width="19" height="8" rx="4" transform="rotate(-45 12 12)"/>'
         '<path d="M9 9l6 6"/>',
    nav=(
        NavItem("/meds", "Today", '<path d="M4 12.5l4.5 4.5L20 6"/>'),
        NavItem("/meds/history", "History", '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/>'
                '<circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>'),
        NavItem("/meds/manage", "Medications", '<rect x="2.5" y="8" width="19" height="8" rx="4"'
                ' transform="rotate(-45 12 12)"/><path d="M9 9l6 6"/>'),
    ),
    routers=(web.router, api.router),
    jobs=(service.send_reminders,),
)
