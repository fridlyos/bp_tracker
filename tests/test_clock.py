from starlette.requests import Request

from app import clock


def _req(cookie: str) -> Request:
    return Request({"type": "http", "headers": [(b"cookie", cookie.encode())]})


def test_tz_cookie_plain_and_percent_encoded():
    # older base.html stored encodeURIComponent(tz); Starlette doesn't decode it
    assert clock.user_tz(_req("tz=America/New_York")).key == "America/New_York"
    assert clock.user_tz(_req("tz=America%2FNew_York")).key == "America/New_York"


def test_tz_cookie_missing_or_bad_is_utc():
    assert clock.user_tz(_req("")).key == "UTC"
    assert clock.user_tz(_req("tz=Not%2FAZone")).key == "UTC"
