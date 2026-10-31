"""SQLite storage and yoyo migrations."""
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from yoyo import get_backend, read_migrations

from .auth import hash_password

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT = _ROOT / "bp.db"
_MIGRATIONS = _ROOT / "migrations"

logger = logging.getLogger("bp_tracker.db")


def _path() -> str:
    # env override lets tests point at a throwaway file
    return os.getenv("BP_DB") or str(_DEFAULT)


def connect() -> sqlite3.Connection:
    # check_same_thread=False: FastAPI runs sync routes in a threadpool, so the
    # get_db generator's open and close can happen on different worker threads.
    # Safe here — each request owns its connection; none is shared concurrently.
    conn = sqlite3.connect(_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    _apply_migrations()
    with connect() as conn:
        _seed_admin(conn)


def _backup_database(path: Path) -> None:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"{path.stem}-{stamp}{path.suffix}"
    shutil.copy2(path, backup)
    logger.info("Backed up SQLite database before migrations: %s", backup)


def _apply_migrations() -> None:
    path = Path(_path()).resolve()
    if not _MIGRATIONS.is_dir():
        # Fail loudly: a missing migrations dir (e.g. not COPYed into the
        # Docker image) would otherwise silently skip schema changes.
        raise RuntimeError(f"migrations directory missing: {_MIGRATIONS}")
    migrations = read_migrations(str(_MIGRATIONS))
    existed = path.exists() and path.stat().st_size > 0
    with get_backend(f"sqlite:///{path}") as backend:
        to_apply = backend.to_apply(migrations)
        if not to_apply:
            return
        if existed:
            _backup_database(path)
        logger.info("Applying %d SQLite migration(s): %s",
                    len(to_apply), ", ".join(m.id for m in to_apply))
        backend.apply_migrations(to_apply)


def _seed_admin(conn) -> None:
    """Ensure an admin account exists. Initial password from ADMIN_PASSWORD (default 'admin')."""
    if conn.execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone():
        return
    conn.execute(
        "INSERT INTO users (username, password_hash, is_admin) VALUES ('admin', ?, 1)",
        (hash_password(os.getenv("ADMIN_PASSWORD") or "admin"),),
    )


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
