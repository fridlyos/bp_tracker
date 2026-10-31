import sqlite3

from app import db


def test_init_db_creates_schema_and_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "bp.db"
    monkeypatch.setenv("BP_DB", str(path))

    db.init_db()
    db.init_db()  # second run: nothing to apply, no backup, no duplicate admin

    with sqlite3.connect(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        applied = {r[0] for r in conn.execute("SELECT migration_id FROM _yoyo_migration")}
        admins = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
        idx = {r[1] for r in conn.execute("PRAGMA index_list(readings)")}

    assert {"users", "readings"} <= tables
    assert "0001_initial" in applied
    assert admins == 1
    assert "idx_readings_user_taken" in idx
    assert not (tmp_path / "backups").exists()
