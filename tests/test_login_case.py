"""iPhone auto-capitalizes the first letter: usernames must be case-insensitive."""
import logging
import os
import tempfile

if "BP_DB" not in os.environ:
    os.environ["BP_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()


def test_login_ignores_case_and_spaces():
    TestClient(app).post("/register", data={"username": "casey", "password": "pw"})
    for typed in ("Casey", "CASEY", " casey ", "casey"):
        c = TestClient(app)
        r = c.post("/login", data={"username": typed, "password": "pw"}, follow_redirects=False)
        assert r.status_code == 303, typed
        assert 'aria-label="Account: casey"' in c.get("/").text


def test_register_normalizes_and_blocks_case_duplicates():
    c = TestClient(app)
    c.post("/register", data={"username": "Mixed", "password": "pw"})
    assert 'aria-label="Account: mixed"' in c.get("/").text
    dup = TestClient(app).post("/register", data={"username": "MIXED", "password": "x"})
    assert "That username is taken" in dup.text


def test_password_stays_case_sensitive():
    TestClient(app).post("/register", data={"username": "pwcase", "password": "Secret"})
    r = TestClient(app).post("/login", data={"username": "pwcase", "password": "secret"})
    assert "Wrong username or password" in r.text


def test_failed_login_logged_without_password(caplog):
    with caplog.at_level(logging.WARNING, logger="bp_tracker.service"):
        TestClient(app).post("/login", data={"username": "Nobody", "password": "hunter2"})
    text = caplog.text
    assert "failed login for 'nobody' (no such user)" in text and "hunter2" not in text


def test_username_inputs_disable_ios_autocap():
    html = TestClient(app).get("/login").text
    assert 'autocapitalize="none"' in html and 'autocorrect="off"' in html
