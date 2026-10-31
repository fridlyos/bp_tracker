"""Business logic shared by the JSON API and the web UI.

Each function takes an open sqlite connection; user-scoped functions also take
the acting user's id and must filter by it.
"""
import logging
import sqlite3

from . import auth
from .schemas import User

logger = logging.getLogger("bp_tracker.service")


# --- users

def _user(row) -> User:
    d = dict(row)
    d.pop("password_hash", None)
    return User(**d)


def register_user(conn, username: str, password: str) -> User:
    """Create an account. Raises ValueError if the username is taken."""
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, auth.hash_password(password)),
        )
    except sqlite3.IntegrityError:
        raise ValueError("username already taken")
    conn.commit()
    logger.info("registered user %s (id=%s)", username, cur.lastrowid)
    return get_user(conn, cur.lastrowid)


def authenticate(conn, username: str, password: str) -> User | None:
    """Return the user if username/password match, else None."""
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row and auth.verify_password(password, row["password_hash"]):
        return _user(row)
    return None


def get_user(conn, user_id: int) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user(row) if row else None


def list_users(conn) -> list[User]:
    return [_user(r) for r in conn.execute("SELECT * FROM users ORDER BY username")]


def set_password(conn, user_id: int, password: str) -> None:
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (auth.hash_password(password), user_id))
    conn.commit()


def delete_user(conn, user_id: int) -> None:
    """Admin action: remove a non-admin user; readings cascade."""
    conn.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
    conn.commit()
