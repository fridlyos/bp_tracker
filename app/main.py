"""FastAPI application entry point: app wiring.

The web UI lives in ``app/web.py``; the JSON API (``/api/...``, Phase 2) will
authenticate with the same session cookie. Business logic lives in
``app/service.py``.
"""
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load local secrets from .env before anything reads os.getenv (Fly injects
# real env vars in prod; load_dotenv never overrides those).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from . import db  # noqa: E402
from .web import router as web_router  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("bp_tracker")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Migrate the schema and seed the admin at startup (not at import)."""
    db.init_db()
    if not os.getenv("SECRET_KEY"):
        logger.warning("SECRET_KEY not set — sessions will not survive a restart")
    yield


app = FastAPI(title="BP Tracker", version="0.1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def log_unhandled(request: Request, exc: Exception):
    """Log unhandled errors with request context; return a generic 500."""
    logger.exception("Unhandled error on %s %s (uid=%s)",
                     request.method, request.url.path,
                     request.scope.get("session", {}).get("uid"))
    return PlainTextResponse("Something went wrong. Please try again.", status_code=500)


# Signed session cookie holds the logged-in user id. Set SECRET_KEY in prod;
# dev gets an ephemeral one. One-year max_age = "remember me". https_only in
# prod via COOKIE_SECURE=true; off in dev so plain-http localhost works.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY") or secrets.token_hex(32),
    max_age=365 * 24 * 60 * 60,
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "").lower() == "true",
)

app.include_router(web_router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Serve the service worker from the root so its scope covers the whole site."""
    return FileResponse(Path(__file__).parent / "static" / "sw.js",
                        media_type="application/javascript")
