"""Filling in doses on earlier days from /meds?day=."""
from datetime import date, timedelta

from tests.test_meds import _add, _user


def test_fill_in_past_day():
    c = _user()
    m = _add(c, "Lisinopril", ["08:00"])
    prn = _add(c, "Ibuprofen")
    day = (date.today() - timedelta(days=3)).isoformat()
    html = c.get(f"/meds?day={day}").text
    assert "Not marked" in html and "Filling in a past day" in html
    r = c.post("/meds/doses", data={"med_id": m["id"], "slot": f"{day}T08:00", "status": "taken",
                                    "day": day}, follow_redirects=False)
    assert r.headers["location"] == f"/meds?day={day}"
    c.post("/meds/doses", data={"med_id": prn["id"], "day": day, "at": "21:15"})
    html = c.get(f"/meds?day={day}").text
    assert "✓ Taken 08:00" in html and "this day at 21:15" in html
    # it shows up in history, logged at the filled-in times, and today is untouched
    hist = c.get("/meds/history").text
    assert f"/meds?day={day}" in hist and "21:15" in hist
    assert c.get("/api/meds/today").json()["scheduled"][0]["status"] in ("due", "upcoming")


def test_future_or_bad_day_falls_back_to_today():
    c = _user()
    _add(c, "Vitamin D", ["09:00"])
    future = (date.today() + timedelta(days=5)).isoformat()
    for d in (future, "nope"):
        assert "<h1>Today</h1>" in c.get(f"/meds?day={d}").text


def test_day_switcher_is_big_and_labelled():
    from datetime import datetime, timezone
    c = _user()  # tz cookie UTC
    today = datetime.now(timezone.utc).date()
    html = c.get("/meds").text
    y = today - timedelta(days=1)
    assert f'class="day-step" href="/meds?day={y.isoformat()}"' in html
    assert f'{y.strftime("%b")} {y.day}</span>' in html            # labelled with the date
    assert 'day-step is-disabled' in html and "Back to today" not in html  # no future
    past = c.get(f"/meds?day={y.isoformat()}").text
    assert f'href="/meds?day={today.isoformat()}"' in past and "Back to today" in past
    assert 'onchange="this.form.submit()"' in past                  # picking a date jumps
