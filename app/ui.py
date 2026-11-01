"""Template rendering shared by core and module pages."""
import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

APP_NAME = "My Apps"  # top bar + launcher title; each module has its own name

_STATIC = Path(__file__).parent / "static"


@lru_cache(maxsize=None)
def static_url(name: str) -> str:
    """'/static/<name>?v=<content hash>': a deploy that changes the file changes
    the URL, so iPhone Safari can't keep serving a stale cached copy."""
    try:
        digest = hashlib.sha256((_STATIC / name).read_bytes()).hexdigest()[:10]
    except OSError:
        return f"/static/{name}"
    return f"/static/{name}?v={digest}"


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["APP_NAME"] = APP_NAME
templates.env.globals["static_url"] = static_url


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
