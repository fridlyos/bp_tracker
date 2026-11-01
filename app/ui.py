"""Template rendering shared by core and module pages."""
from pathlib import Path

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

APP_NAME = "My Apps"  # top bar + launcher title; each module has its own name

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["APP_NAME"] = APP_NAME


def module_for(path: str):
    """The module that owns `path` (/<key> or /<key>/...), or None for core pages."""
    from .modules import MODULES  # late: modules import this file
    return next((m for m in MODULES if path == f"/{m.key}" or path.startswith(f"/{m.key}/")), None)


def render(request, name, user, ctx):
    """Render a page. On a module's pages base.html shows that module's tab bar
    and its name in the top bar; core pages (launcher, users) show neither."""
    module = module_for(request.url.path)
    return templates.TemplateResponse(request, name, {
        "user": user, "module": module,
        "active_nav": module.active(request.url.path) if module else None, **ctx})


def redirect(path):
    return RedirectResponse(path, status_code=303)


def safe_next(path: str | None, default: str = "/") -> str:
    # only same-site relative paths, never //host or absolute URLs
    return path if path and path.startswith("/") and not path.startswith("//") else default
