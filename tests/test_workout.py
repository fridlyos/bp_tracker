"""Workout module: exercise types, live session, set upsert, derived numbers, backup."""
import io
import itertools
import json
import os
import tempfile
import uuid

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.workout.schemas import tempo_seconds  # noqa: E402

db.init_db()
_n = itertools.count()


def _user(tz="UTC"):
    c = TestClient(app)
    c.cookies.set("tz", tz)
    c.username = f"wo{next(_n)}-{os.getpid()}"
    c.post("/register", data={"username": c.username, "password": "pw"})
    return c


def _exercise(c, name="Back squat", **kw):
    r = c.post("/api/workout/exercises", json={"name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _set(se_id, start, end, reps=8, weight=135, uid=None, **kw):
    return {"uid": uid or str(uuid.uuid4()), "session_exercise_id": se_id, "started_at": start,
            "ended_at": end, "reps": reps, "weight": weight, **kw}


def _session_with_squat(c):
    ex = _exercise(c, default_rest_sec=120)
    s = c.post("/api/workout/sessions").json()
    se = c.post(f"/api/workout/sessions/{s['id']}/exercises", json={"exercise_id": ex["id"]}).json()
    return ex, s, se["session_exercise_id"]


def test_exercise_crud_and_validation():
    c = _user()
    ex = _exercise(c, "  Plank ", track_reps=False, track_weight=False, default_rest_sec=60, category="Core")
    assert ex["name"] == "Plank" and ex["track_reps"] is False and ex["unit"] == "lb"
    assert c.post("/api/workout/exercises", json={"name": "plank"}).status_code == 409  # case-insensitive
    assert c.post("/api/workout/exercises", json={"name": " "}).status_code == 422
    r = c.put(f"/api/workout/exercises/{ex['id']}", json={"name": "Side plank", "track_reps": False,
                                                           "track_weight": False})
    assert r.json()["name"] == "Side plank"
    # web form: checkboxes, error re-render
    r = c.post("/workout/exercises", data={"name": "Goblet squat", "track_reps": "1", "track_weight": "1",
                                           "unit": "kg", "default_rest_sec": "75"}, follow_redirects=False)
    assert r.status_code == 303
    gs = next(e for e in c.get("/api/workout/exercises").json() if e["name"] == "Goblet squat")
    assert gs["unit"] == "kg" and gs["default_rest_sec"] == 75
    r = c.post("/workout/exercises", data={"name": "goblet SQUAT", "track_reps": "1"})
    assert r.status_code == 422 and "already have an exercise" in r.text
    assert "Goblet squat" in c.get("/workout/exercises").text
    c.post(f"/workout/exercises/{gs['id']}/active", data={"active": 0})
    assert "Archived" in c.get("/workout/exercises").text


def test_live_session_sets_derived_numbers():
    c = _user()
    ex, s, se = _session_with_squat(c)
    assert s["in_progress"] and c.post("/api/workout/sessions").json()["id"] == s["id"]  # resumes
    sets = [_set(se, "2026-11-17T18:00:00", "2026-11-17T18:00:40", tempo="3-1-1-0"),
            _set(se, "2026-11-17T18:02:40", "2026-11-17T18:03:15", reps=7),
            _set(se, "2026-11-17T18:05:00", "2026-11-17T18:05:30", reps=6, weight=145)]
    r = c.put("/api/workout/sets", json=sets)
    assert r.json() == {"saved": [x["uid"] for x in sets], "rejected": []}
    d = c.get(f"/api/workout/sessions/{s['id']}").json()
    got = d["exercises"][0]["sets"]
    assert [x["duration_sec"] for x in got] == [40, 35, 30]
    assert [x["rest_before_sec"] for x in got] == [None, 120, 105]
    assert got[0]["est_tut_sec"] == 40  # 8 reps x 5 s
    summ = d["exercises"][0]["summary"]
    assert summ == {"sets": 3, "reps": 21, "volume": 8 * 135 + 7 * 135 + 6 * 145, "tut_sec": 105,
                    "avg_rest_sec": 112, "top_weight": 145}
    # resending (offline queue) is idempotent; an edit replaces
    c.put("/api/workout/sets", json=[sets[1] | {"reps": 5}])
    d = c.get(f"/api/workout/sessions/{s['id']}").json()
    assert len(d["exercises"][0]["sets"]) == 3 and d["exercises"][0]["sets"][1]["reps"] == 5
    # finish: end = last set's end
    d = c.post(f"/api/workout/sessions/{s['id']}/finish").json()
    assert d["ended_at"] == "2026-11-17T18:05:30" and not d["in_progress"]
    # next session pre-fills from this one
    s2 = c.post("/api/workout/sessions").json()
    assert s2["id"] != s["id"]
    se2 = c.post(f"/api/workout/sessions/{s2['id']}/exercises", json={"exercise_id": ex["id"]}).json()
    prev = se2["session"]["exercises"][0]["previous"]
    assert [p["reps"] for p in prev] == [8, 5, 6] and prev[2]["weight"] == 145
    assert se2["session"]["exercises"][0]["target_rest_sec"] == 120
    hist = c.get(f"/api/workout/exercises/{ex['id']}/history").json()
    assert len(hist) == 1 and hist[0]["summary"]["top_weight"] == 145


def test_set_validation():
    c = _user()
    _, _, se = _session_with_squat(c)
    bad = [_set(se, "2026-11-17T18:01:00", "2026-11-17T18:00:00"),       # ends before start
           _set(se, "2026-11-17T18:00:00", "2026-11-17T19:30:00"),       # > 1 hour
           _set(se, "2026-11-17T18:00:00", "2026-11-17T18:00:30", tempo="slow"),
           _set(se, "2026-11-17T18:00:00", "2026-11-17T18:00:30", uid="<script>")]
    for b in bad:
        assert c.put("/api/workout/sets", json=[b]).status_code == 422, b


def test_isolation_between_users():
    a, b = _user(), _user()
    _, s, se = _session_with_squat(a)
    st = _set(se, "2026-11-17T18:00:00", "2026-11-17T18:00:30")
    assert a.put("/api/workout/sets", json=[st]).json()["saved"] == [st["uid"]]
    # b can't write into a's session, overwrite a's set, read or delete it
    assert b.put("/api/workout/sets", json=[st]).json()["rejected"] == [st["uid"]]
    assert b.get(f"/api/workout/sessions/{s['id']}").status_code == 404
    b.delete(f"/api/workout/sets/{st['uid']}")
    b.delete(f"/api/workout/sessions/{s['id']}")
    assert len(a.get(f"/api/workout/sessions/{s['id']}").json()["exercises"][0]["sets"]) == 1
    assert b.get(f"/workout/session/{s['id']}", follow_redirects=False).headers["location"] == "/workout"


def test_pages_render():
    c = _user()
    assert "Add the exercises you do first" in c.get("/workout").text
    _, s, se = _session_with_squat(c)
    c.put("/api/workout/sets", json=[_set(se, "2026-11-17T18:00:00", "2026-11-17T18:00:45")])
    home = c.get("/workout").text
    assert "Continue workout" in home and 'class="tabbar" aria-label="Workout"' in home
    page = c.get(f"/workout/session/{s['id']}").text
    assert 'id="wo-dock"' in page and 'id="wo-data"' in page and "workout.js" in page
    data = json.loads(page.split('id="wo-data">')[1].split("</script>")[0])
    assert data["exercises"][0]["sets"][0]["duration_sec"] == 45
    assert c.post(f"/workout/session/{s['id']}/finish", follow_redirects=False).status_code == 303
    page = c.get(f"/workout/session/{s['id']}").text
    assert 'id="wo-dock"' not in page and "Delete workout" in page
    assert "Back squat" in c.get("/workout/history").text
    assert "Recent sessions" in c.get(f"/workout/exercises/{data['exercises'][0]['exercise_id']}").text
    anon = TestClient(app)
    for p in ("/workout", "/workout/history", "/workout/exercises"):
        assert anon.get(p, follow_redirects=False).headers["location"] == "/login"
    assert anon.get("/api/workout/sessions").status_code == 401


def test_user_text_is_escaped_in_json_blob():
    c = _user()
    ex = _exercise(c, "</script><b>x")
    s = c.post("/api/workout/sessions").json()
    c.post(f"/api/workout/sessions/{s['id']}/exercises", json={"exercise_id": ex["id"]})
    page = c.get(f"/workout/session/{s['id']}").text
    assert "</script><b>x" not in page


def test_tempo_seconds():
    assert tempo_seconds("3-1-1-0") == 5 and tempo_seconds("3110") == 5 and tempo_seconds(None) is None


def test_backup_roundtrip_into_other_account():
    a = _user()
    ex, s, se = _session_with_squat(a)
    _exercise(a, "Plank", track_reps=False, track_weight=False)
    a.put("/api/workout/sets", json=[_set(se, "2026-11-17T18:00:00", "2026-11-17T18:00:40"),
                                     _set(se, "2026-11-17T18:02:00", "2026-11-17T18:02:35", reps=7)])
    a.post(f"/api/workout/sessions/{s['id']}/finish")
    data = a.get("/backup/download").json()
    assert {e["name"] for e in data["workout_exercises"]} == {"Back squat", "Plank"}
    assert len(data["workout_sessions"][0]["exercises"][0]["sets"]) == 2

    def upload(c):
        return c.post("/backup/restore", files={"file": ("b.json", io.BytesIO(json.dumps(data).encode()),
                                                         "application/json")})
    b = _user()
    assert upload(b).status_code == 200
    got = b.get("/api/workout/sessions").json()
    assert len(got) == 1 and got[0]["summary"]["sets"] == 2 and got[0]["exercises"][0]["sets"][1]["rest_before_sec"] == 80
    assert "Added 0 items" in upload(b).text
    # the original account is untouched
    assert a.get("/api/workout/sessions").json()[0]["summary"]["sets"] == 2


# --- phase 2: progress, PRs, weekly

def _log_session(c, ex_id, day, sets):
    """One finished session on `day` with sets [(reps, weight[, unit])], 2 min apart."""
    s = c.post("/api/workout/sessions").json()
    with db.connect() as conn:  # backdate it
        conn.execute("UPDATE workout_sessions SET started_at = ? WHERE id = ?", (f"{day}T07:00:00", s["id"]))
        conn.commit()
    se = c.post(f"/api/workout/sessions/{s['id']}/exercises", json={"exercise_id": ex_id}).json()["session_exercise_id"]
    batch = []
    for i, x in enumerate(sets):
        reps, weight, unit = (x + ("lb",))[:3]
        m = 2 * i
        batch.append(_set(se, f"{day}T07:{m:02d}:00", f"{day}T07:{m:02d}:40", reps=reps, weight=weight, unit=unit))
    assert c.put("/api/workout/sets", json=batch).json()["rejected"] == []
    c.post(f"/api/workout/sessions/{s['id']}/finish")
    return s["id"]


def test_prs_progress_and_records():
    c = _user()
    ex = _exercise(c, "Bench")
    s1 = _log_session(c, ex["id"], "2026-10-01", [(8, 135), (8, 135), (6, 145)])
    s2 = _log_session(c, ex["id"], "2026-10-08", [(8, 140), (5, 150), (3, 155)])
    d1 = c.get(f"/api/workout/sessions/{s1}").json()["exercises"][0]
    assert all(st["prs"] == [] for st in d1["sets"])  # first session: nothing to beat
    d2 = c.get(f"/api/workout/sessions/{s2}").json()["exercises"][0]
    # e1RM: 145x6 = 174 is the bar; 140x8 = 177.3 beats it; 150x5 = 175 doesn't; 155x3 = 170.5 doesn't
    assert [st["prs"] for st in d2["sets"]] == [["e1rm"], ["weight"], ["weight"]]
    assert d2["best_before"] == {"weight": 145.0, "e1rm": 174.0}
    assert d2["pr_kinds"] == ["weight", "e1rm"]

    p = c.get(f"/api/workout/exercises/{ex['id']}/progress").json()
    assert [x["date"] for x in p["points"]] == ["2026-10-01", "2026-10-08"]
    assert p["points"][1]["top_weight"] == 155 and p["points"][1]["top_reps"] == 3
    assert p["points"][1]["e1rm"] == 177.3 and p["points"][0]["volume"] == 8 * 135 * 2 + 6 * 145
    assert p["points"][0]["avg_rest"] == 80
    assert p["records"]["weight"]["value"] == 155 and p["records"]["weight"]["reps"] == 3
    assert p["records"]["e1rm"]["date"] == "2026-10-08"
    assert p["records"]["volume"]["value"] == 8 * 135 * 2 + 6 * 145  # s1 had more volume
    since = c.get(f"/api/workout/exercises/{ex['id']}/progress?since=2026-10-05").json()
    assert len(since["points"]) == 1 and since["records"]["volume"]["date"] == "2026-10-01"
    assert c.get(f"/api/workout/exercises/{ex['id']}/progress?since=nope").status_code == 422

    page = c.get(f"/workout/progress/{ex['id']}").text
    assert "Personal records" in page and "155 lb × 3" in page and "strength-chart" in page
    assert 'href="/workout/progress" class="active"' in page
    assert c.get("/workout/progress", follow_redirects=False).headers["location"] == f"/workout/progress/{ex['id']}"


def test_pr_kinds_by_tracking_and_unit_conversion():
    c = _user()
    pushup = _exercise(c, "Push-up", track_weight=False)
    _log_session(c, pushup["id"], "2026-10-01", [(20, None)])
    s = _log_session(c, pushup["id"], "2026-10-03", [(18, None), (25, None)])
    sets = c.get(f"/api/workout/sessions/{s}").json()["exercises"][0]["sets"]
    assert [x["prs"] for x in sets] == [[], ["reps"]]
    plank = _exercise(c, "Plank", track_reps=False, track_weight=False)
    p = c.get(f"/api/workout/exercises/{plank['id']}/progress").json()
    assert p["kinds"] == ["time"] and p["points"] == []
    # a kg set on an lb exercise is compared in lb
    dl = _exercise(c, "Deadlift")
    _log_session(c, dl["id"], "2026-10-01", [(5, 225)])
    s = _log_session(c, dl["id"], "2026-10-05", [(5, 110, "kg")])  # 242.5 lb
    assert c.get(f"/api/workout/sessions/{s}").json()["exercises"][0]["sets"][0]["prs"] == ["weight", "e1rm"]


def test_weekly_summary_and_home():
    c = _user()
    ex = _exercise(c, "Row")
    _log_session(c, ex["id"], "2026-11-16", [(10, 95), (10, 95)])  # a Monday
    _log_session(c, ex["id"], "2026-11-17", [(10, 100)])
    from datetime import date
    from app.modules.workout import service
    with db.connect() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (c.username,)).fetchone()[0]
        weeks = service.weekly(conn, uid, date(2026, 11, 19), 3)
        prs = service.recent_prs(conn, uid, "2026-11-01")
    assert weeks[0] == {"week": "2026-11-16", "workouts": 2, "sets": 3, "time_sec": 160 + 40}
    assert weeks[1]["workouts"] == 0 and len(weeks) == 3
    assert [(p["exercise"], p["kind"], p["value"]) for p in prs] == [("Row", "weight", 100.0), ("Row", "e1rm", 133.3)]
    assert c.get("/api/workout/summary").status_code == 200
    assert "By week" in c.get("/workout/history").text
