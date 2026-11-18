"""Calories: a link out to the separate calorie counter app (lose_weight,
its own login and data), plus today's numbers from this app that help when
logging food: workout calories burned and the latest weigh-in."""
import re
from datetime import date
from urllib.parse import urlparse

DEFAULT_URL = "https://lose-weight-fridl.fly.dev"


def clean_url(url: str) -> str:
    """http(s) URL with a host, or ValueError. Adds https:// if missing."""
    bad = ValueError("Enter a web address like https://my-calories.fly.dev")
    url = (url or "").strip()
    if url and "://" not in url:
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:(?!\d)", url):  # another scheme: javascript:, mailto:, ...
            raise bad
        url = "https://" + url
    p = urlparse(url)
    try:
        p.port  # raises on a non-numeric port
    except ValueError:
        raise bad
    if (p.scheme not in ("http", "https") or not p.hostname or len(url) > 300 or any(c.isspace() for c in url)
            or not re.fullmatch(r"[A-Za-z0-9.-]+", p.hostname)):
        raise bad
    return url


def get_url(conn, user_id: int) -> str:
    row = conn.execute("SELECT url FROM calories_settings WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else DEFAULT_URL


def set_url(conn, user_id: int, url: str | None) -> None:
    """Save a link (validated), or None to go back to the default."""
    if url is None:
        conn.execute("DELETE FROM calories_settings WHERE user_id = ?", (user_id,))
    else:
        conn.execute("INSERT INTO calories_settings (user_id, url) VALUES (?, ?)"
                     " ON CONFLICT(user_id) DO UPDATE SET url = excluded.url", (user_id, clean_url(url)))
    conn.commit()


def today_numbers(conn, user_id: int, today: date) -> dict:
    """Workout kcal burned today (estimate or your own number) and the latest weigh-in."""
    from ..weight import service as weight
    from ..workout import service as workout
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM workout_sessions WHERE user_id = ? AND substr(started_at, 1, 10) = ?",
        (user_id, today.isoformat()))]
    sessions = [d for d in (workout.session_detail(conn, user_id, i) for i in ids) if d and d["summary"]["sets"]]
    kcals = [d["kcal"] for d in sessions if d["kcal"] is not None]
    kg = weight.latest_kg(conn, user_id)
    unit = weight.get_settings(conn, user_id)["unit"]
    last = conn.execute("SELECT substr(taken_at, 1, 10) FROM weight_entries WHERE user_id = ?"
                        " ORDER BY taken_at DESC LIMIT 1", (user_id,)).fetchone()
    return {"workouts": len(sessions), "burned": sum(kcals) if kcals else None,
            "weight": weight.disp(kg, unit), "weight_unit": unit, "weight_day": last[0] if last else None}
