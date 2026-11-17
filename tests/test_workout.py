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
    assert weeks[0] == {"week": "2026-11-16", "workouts": 2, "sets": 3, "time_sec": 160 + 40, "kcal": None}
    assert weeks[1]["workouts"] == 0 and len(weeks) == 3
    assert [(p["exercise"], p["kind"], p["value"]) for p in prs] == [("Row", "weight", 100.0), ("Row", "e1rm", 133.3)]
    assert c.get("/api/workout/summary").status_code == 200
    assert "By week" in c.get("/workout/history").text


# --- phase 3: routines, supersets, RPE/tempo, calories

def test_routine_crud_start_and_superset():
    c = _user()
    bench = _exercise(c, "Bench", default_rest_sec=120)
    row = _exercise(c, "Row", default_rest_sec=60)
    plank = _exercise(c, "Plank", track_reps=False, track_weight=False)
    body = {"name": "Push pull", "items": [
        {"exercise_id": bench["id"], "target_sets": 3, "target_reps": 8, "target_weight": 135},
        {"exercise_id": row["id"], "target_sets": 3, "target_reps": 10, "superset_with_previous": True},
        {"exercise_id": plank["id"], "target_sets": 2, "rest_sec": 30, "superset_with_previous": False}]}
    rt = c.post("/api/workout/routines", json=body).json()
    assert [i["superset_with_previous"] for i in rt["items"]] == [False, True, False]
    assert rt["items"][0]["group_id"] == rt["items"][1]["group_id"] is not None and rt["items"][2]["group_id"] is None
    assert [i["rest"] for i in rt["items"]] == [120, 60, 30]
    assert c.post("/api/workout/routines", json=body | {"name": "push PULL"}).status_code == 409
    assert c.post("/api/workout/routines", json={"name": "x", "items": []}).status_code == 422
    other = _user()
    assert other.post("/api/workout/routines", json=body).status_code == 422  # not their exercises

    s = c.post(f"/api/workout/routines/{rt['id']}/start").json()
    assert s["routine"] == "Push pull"
    ex = s["exercises"]
    assert [e["name"] for e in ex] == ["Bench", "Row", "Plank"]
    assert ex[0]["group_id"] == ex[1]["group_id"] == ex[0]["id"] and ex[2]["group_id"] is None
    assert (ex[0]["target_sets"], ex[0]["target_reps"], ex[0]["target_weight"]) == (3, 8, 135)
    assert ex[2]["target_rest_sec"] == 30
    # starting again while one is in progress resumes it
    assert c.post(f"/api/workout/routines/{rt['id']}/start").json()["id"] == s["id"]

    # unlink / relink supersets in the live session
    d = c.patch(f"/api/workout/session-exercises/{ex[1]['id']}", json={"superset_with_previous": False}).json()
    assert all(e["group_id"] is None for e in d["exercises"])
    d = c.patch(f"/api/workout/session-exercises/{ex[2]['id']}", json={"superset_with_previous": True}).json()
    assert [e["group_id"] for e in d["exercises"]] == [None, ex[1]["id"], ex[1]["id"]]
    assert other.patch(f"/api/workout/session-exercises/{ex[2]['id']}",
                       json={"superset_with_previous": False}).status_code == 404

    # web: list, start button, edit form, save from a finished session
    assert "Push pull" in c.get("/workout/routines").text
    c.put("/api/workout/sets", json=[_set(ex[0]["id"], "2026-11-17T18:00:00", "2026-11-17T18:00:40", reps=8,
                                          weight=140, rpe=8, tempo="3-1-1-0"),
                                     _set(ex[0]["id"], "2026-11-17T18:03:00", "2026-11-17T18:03:40", reps=7,
                                          weight=140)])
    got = c.get(f"/api/workout/sessions/{s['id']}").json()["exercises"][0]["sets"][0]
    assert got["rpe"] == 8 and got["tempo"] == "3-1-1-0" and got["est_tut_sec"] == 40
    c.post(f"/workout/session/{s['id']}/finish")
    r = c.post(f"/workout/session/{s['id']}/save-routine", data={"name": "Bench day"}, follow_redirects=False)
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    saved = next(x for x in c.get("/api/workout/routines").json() if x["id"] == rid)
    assert saved["items"][0] | {} and (saved["items"][0]["target_sets"], saved["items"][0]["target_reps"],
                                       saved["items"][0]["target_weight"]) == (2, 8, 140)
    assert [i["superset_with_previous"] for i in saved["items"]] == [False, False, True]
    assert "Bench day" in c.get(f"/workout/routines/{rid}").text


def test_routine_web_form():
    c = _user()
    a, b = _exercise(c, "Squat"), _exercise(c, "Lunge")
    r = c.post("/workout/routines", data={
        "name": "Legs", "notes": "",
        "item_exercise": [str(a["id"]), str(b["id"]), ""], "item_sets": ["4", "3", "3"],
        "item_reps": ["5", "12", ""], "item_weight": ["225", "", ""], "item_rest": ["180", "", ""],
        "item_superset": ["1"]}, follow_redirects=False)
    assert r.status_code == 303, r.text
    rt = c.get("/api/workout/routines").json()[0]
    assert [(i["name"], i["target_sets"], i["target_reps"], i["superset_with_previous"]) for i in rt["items"]] == \
        [("Squat", 4, 5, False), ("Lunge", 3, 12, True)]
    assert rt["items"][0]["target_weight"] == 225 and rt["items"][0]["rest_sec"] == 180
    r = c.post("/workout/routines", data={"name": "Empty", "item_exercise": [""]})
    assert r.status_code == 422 and "at least one exercise" in r.text
    home = c.get("/workout").text
    assert "Start a routine" in home and "Legs" in home
    r = c.post(f"/workout/routines/{rt['id']}/start", follow_redirects=False)
    assert r.headers["location"].startswith("/workout/session/")


def test_calories_estimate_and_override():
    c = _user()
    _, s, se = _session_with_squat(c)
    c.put("/api/workout/sets", json=[_set(se, "2026-11-17T18:00:00", "2026-11-17T18:00:40")])
    with db.connect() as conn:  # a 60-minute workout
        conn.execute("UPDATE workout_sessions SET started_at = '2026-11-17T17:01:00' WHERE id = ?", (s["id"],))
        conn.commit()
    c.post(f"/api/workout/sessions/{s['id']}/finish")
    d = c.get(f"/api/workout/sessions/{s['id']}").json()
    assert d["duration_sec"] == 3600 - 20 and d["calories_estimate"] is None and d["kcal"] is None
    assert "Set your body weight" in c.get(f"/workout/session/{s['id']}").text

    r = c.post("/workout/settings", data={"body_weight": "198.4", "unit": "lb", "intensity": "moderate"},
               follow_redirects=False)
    assert r.status_code == 303
    d = c.get(f"/api/workout/sessions/{s['id']}").json()
    # 5.0 MET x 90 kg x 0.9944 h = 447.5
    assert d["calories_estimate"] == round(5.0 * 198.4 / 2.20462 * (3580 / 3600)) and d["kcal"] == d["calories_estimate"]
    c.put("/api/workout/settings", json={"body_weight": 90, "unit": "kg", "intensity": "vigorous"})
    assert c.get(f"/api/workout/sessions/{s['id']}").json()["calories_estimate"] == round(6.0 * 90 * 3580 / 3600)

    c.post(f"/workout/session/{s['id']}/calories", data={"calories": "380"})
    d = c.get(f"/api/workout/sessions/{s['id']}").json()
    assert d["calories"] == 380 and d["kcal"] == 380
    assert "380 kcal" in c.get("/workout/history").text
    assert c.post(f"/workout/session/{s['id']}/calories", data={"calories": "-5"},
                  follow_redirects=False).headers["location"].endswith("error=kcal")
    c.post(f"/workout/session/{s['id']}/calories", data={"calories": ""})  # back to the estimate
    assert c.get(f"/api/workout/sessions/{s['id']}").json()["calories"] is None
    assert c.post("/workout/settings", data={"body_weight": "5", "unit": "lb"}).status_code == 422


def test_backup_includes_phase3():
    a = _user()
    ex = _exercise(a, "Bench")
    rt = a.post("/api/workout/routines", json={"name": "Push", "items": [
        {"exercise_id": ex["id"], "target_sets": 3, "target_reps": 8, "target_weight": 135}]}).json()
    s = a.post(f"/api/workout/routines/{rt['id']}/start").json()
    se = s["exercises"][0]["id"]
    a.put("/api/workout/sets", json=[_set(se, "2026-11-17T18:00:00", "2026-11-17T18:00:40", rpe=9)])
    a.post(f"/api/workout/sessions/{s['id']}/finish")
    a.put(f"/api/workout/sessions/{s['id']}/calories", json={"calories": 250})
    a.put("/api/workout/settings", json={"body_weight": 180, "unit": "lb", "intensity": "light"})
    data = a.get("/backup/download").json()
    assert data["workout_routines"][0]["items"][0]["exercise"] == "Bench"
    assert data["workout_sessions"][0]["calories"] == 250 and data["workout_sessions"][0]["routine"] == "Push"
    assert data["workout_settings"] == {"body_weight": 180, "unit": "lb", "intensity": "light"}

    b = _user()
    up = lambda: b.post("/backup/restore", files={"file": ("b.json", io.BytesIO(json.dumps(data).encode()),  # noqa
                                                           "application/json")})
    assert up().status_code == 200
    got = b.get("/api/workout/sessions").json()[0]
    assert got["calories"] == 250 and got["routine"] == "Push" and got["exercises"][0]["target_reps"] == 8
    assert got["exercises"][0]["sets"][0]["rpe"] == 9
    assert b.get("/api/workout/settings").json()["intensity"] == "light"
    assert "Added 0 items" in up().text


# --- reminders

def test_reminders_schedule_skip_and_goal(monkeypatch):
    from datetime import datetime, timezone
    from app.modules.workout import service
    from app import push
    c = _user("America/New_York")
    ws = _exercise(c, "Wall sit", track_reps=False, track_weight=False, default_rest_sec=60)
    r = c.post("/workout/reminders", data={"target": f"e:{ws['id']}", "days": ["0", "1", "2", "3", "4"],
                                           "at_time": "19:00", "weekly_goal": "2"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    with db.connect() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (c.username,)).fetchone()[0]
        conn.execute("UPDATE workout_reminders SET active = 0 WHERE user_id != ?", (uid,))  # isolate
        conn.commit()
    sent = []
    monkeypatch.setattr(push, "send_to_user", lambda conn, u, title, body, url: sent.append((u, title, body, url)) or 1)

    def run(utc):  # one scheduler tick
        with db.connect() as conn:
            service.send_reminders(conn, utc)

    # Mon 2026-11-16 19:00 New York = 00:00 UTC Tuesday
    run(datetime(2026, 11, 16, 23, 59, tzinfo=timezone.utc))        # 18:59: not yet
    assert sent == []
    run(datetime(2026, 11, 17, 0, 5, tzinfo=timezone.utc))          # 19:05: fires
    run(datetime(2026, 11, 17, 0, 6, tzinfo=timezone.utc))          # once a day
    assert len(sent) == 1 and sent[0][1] == "Time for Wall sit" and "0 of 2 days" in sent[0][2]
    rid = int(sent[0][3].split("remind=")[1])

    # Tuesday: done before 19:00 -> skipped
    sid = c.post(f"/workout/reminders/{rid}/start", follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    se = c.get(f"/api/workout/sessions/{sid}").json()["exercises"][0]["id"]
    c.put("/api/workout/sets", json=[_set(se, "2026-11-17T12:00:00", "2026-11-17T12:01:30", reps=None, weight=None)])
    run(datetime(2026, 11, 18, 0, 1, tzinfo=timezone.utc))          # Tue 19:01
    assert len(sent) == 1
    # Wednesday: not done, 1/2 -> fires
    run(datetime(2026, 11, 19, 0, 1, tzinfo=timezone.utc))
    assert len(sent) == 2 and "1 of 2" in sent[1][2]
    # Thursday: logged Wed late -> goal met (2/2) -> no more this week
    c.put("/api/workout/sets", json=[_set(se, "2026-11-18T21:00:00", "2026-11-18T21:01:00", reps=None, weight=None)])
    run(datetime(2026, 11, 20, 0, 1, tzinfo=timezone.utc))
    run(datetime(2026, 11, 21, 0, 1, tzinfo=timezone.utc))          # Fri
    assert len(sent) == 2
    # Saturday isn't a reminder day; next Monday starts a new week
    run(datetime(2026, 11, 22, 0, 1, tzinfo=timezone.utc))
    run(datetime(2026, 11, 24, 0, 1, tzinfo=timezone.utc))          # Mon 23rd 19:01
    assert len(sent) == 3 and "0 of 2" in sent[2][2]

    page = c.get("/workout/reminders").text
    assert "Wall sit" in page and "Weekdays" in page and "goal 2 days/week" in page and "data-push-enable" in page
    # paused reminders don't fire
    c.post(f"/workout/reminders/{rid}/active", data={"active": 0})
    run(datetime(2026, 11, 25, 0, 1, tzinfo=timezone.utc))
    assert len(sent) == 3


def test_reminder_validation_home_and_backup():
    c = _user()
    ws = _exercise(c, "Wall sit", track_reps=False, track_weight=False)
    for data, msg in [({"target": "", "days": ["0"], "at_time": "07:00"}, "Pick an exercise or a routine"),
                      ({"target": f"e:{ws['id']}", "at_time": "07:00"}, "Pick at least one day"),
                      ({"target": f"e:{ws['id']}", "days": ["0"], "at_time": "7pm"}, "Pick a time"),
                      ({"target": f"e:{ws['id']}", "days": ["0", "1"], "at_time": "07:00", "weekly_goal": "3"},
                       "more days than the 2"),
                      ({"target": "e:999999", "days": ["0"], "at_time": "07:00"}, "Unknown exercise")]:
        r = c.post("/workout/reminders", data=data)
        assert r.status_code == 422 and msg in r.text, (data, r.text[-500:])
    c.post("/workout/reminders", data={"target": f"e:{ws['id']}", "days": [str(d) for d in range(7)],
                                       "at_time": "07:00", "weekly_goal": "5"})
    home = c.get("/workout").text
    assert "Today" in home and "Due at 07:00" in home and "0/5 this week" in home
    data = c.get("/backup/download").json()
    assert data["workout_reminders"][0] | {} and data["workout_reminders"][0]["exercise"] == "Wall sit"
    b = _user()
    up = lambda: b.post("/backup/restore", files={"file": ("b.json", io.BytesIO(json.dumps(data).encode()),  # noqa
                                                           "application/json")})
    up()
    assert "Added 0 items" in up().text
    assert "Every day" in b.get("/workout/reminders").text
