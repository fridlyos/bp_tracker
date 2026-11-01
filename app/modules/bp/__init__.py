"""Blood pressure + heart rate log. Pages under /bp, JSON API under /api/bp."""
from .. import Module, NavItem
from . import api, service, web

MODULE = Module(
    key="bp",
    name="Blood pressure",
    description="Log blood pressure and pulse, see trends and reports.",
    icon='<path d="M12 20s-7-4.4-7-10a4 4 0 017-2.6A4 4 0 0119 10c0 5.6-7 10-7 10z"/>'
         '<path d="M3 12h4l2-3 3 6 2-3h7"/>',
    nav=(
        NavItem("/bp", "Today", '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>'),
        NavItem("/bp/trends", "Trends", '<path d="M3 17l5-6 4 3 6-8 3 3"/><path d="M3 21h18"/>'),
        NavItem("/bp/reports", "Reports", '<rect x="4" y="3" width="16" height="18" rx="2"/>'
                '<path d="M8 17v-3M12 17v-6M16 17V9"/>'),
        NavItem("/bp/history", "History", '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/>'
                '<circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>'),
        NavItem("/bp/settings", "Settings", '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/>'
                '<circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>'),
    ),
    routers=(web.router, api.router),
    summary=service.tile_summary,
    jobs=(service.send_reminders,),
)
