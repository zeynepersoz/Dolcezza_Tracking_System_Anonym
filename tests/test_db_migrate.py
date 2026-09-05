# -*- coding: utf-8 -*-
"""Paylasilan migration yardimcisi: ensure_columns + record_migration."""
import sqlite3

from db.migrate import ensure_columns, record_migration


def _old_schema_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE parcels (id INTEGER PRIMARY KEY, tracking_no TEXT)")
    return conn


def test_ensure_columns_adds_missing():
    conn = _old_schema_conn()
    ensure_columns(conn, "parcels", [("pod_path", "TEXT"), ("weight_kg", "REAL")])
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(parcels)")}
    assert {"pod_path", "weight_kg"} <= existing


def test_ensure_columns_idempotent():
    conn = _old_schema_conn()
    columns = [("pod_path", "TEXT")]
    ensure_columns(conn, "parcels", columns)
    # Ikinci cagri hata vermemeli (kolon zaten var).
    ensure_columns(conn, "parcels", columns)
    existing = [row["name"] for row in conn.execute("PRAGMA table_info(parcels)")]
    assert existing.count("pod_path") == 1


def test_ensure_columns_skips_existing():
    conn = _old_schema_conn()
    ensure_columns(conn, "parcels", [("tracking_no", "TEXT")])
    existing = [row["name"] for row in conn.execute("PRAGMA table_info(parcels)")]
    assert existing.count("tracking_no") == 1


def test_record_migration_creates_table_and_row():
    conn = _old_schema_conn()
    record_migration(conn, "test.migration.one")
    rows = conn.execute("SELECT id FROM schema_migrations").fetchall()
    assert [r["id"] for r in rows] == ["test.migration.one"]


def test_record_migration_idempotent():
    conn = _old_schema_conn()
    record_migration(conn, "test.migration.one")
    record_migration(conn, "test.migration.one")
    rows = conn.execute("SELECT id FROM schema_migrations").fetchall()
    assert len(rows) == 1


def test_record_migration_multiple_ids():
    conn = _old_schema_conn()
    record_migration(conn, "a")
    record_migration(conn, "b")
    rows = conn.execute("SELECT id FROM schema_migrations ORDER BY id").fetchall()
    assert [r["id"] for r in rows] == ["a", "b"]


# ---------------------------------------------------------------- auth.db gocu

def _pre_email_auth_db(path):
    """`email` kolonu EKLENMEDEN onceki auth.db — canlidaki dosyanin hali."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'operator',
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL,
            full_name     TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO users (username, password_hash, created_at)
        VALUES ('ayse', 'x', '2026-01-01T00:00:00');
    """)
    conn.commit()
    conn.close()


def test_existing_auth_db_gains_the_email_column(tmp_path):
    """Canlidaki auth.db elle silinmeden acilabilmeli — yoksa deploy sonrasi
    kimse giremez ve hesaplar geri getirilemez."""
    from auth.db import AuthDB

    path = tmp_path / "auth.db"
    _pre_email_auth_db(path)

    db = AuthDB(path=path)

    assert db.get_user_by_username("ayse")["email"] == ""


def test_the_password_reset_table_is_created_on_an_old_db(tmp_path):
    from auth.db import AuthDB

    path = tmp_path / "auth.db"
    _pre_email_auth_db(path)

    db = AuthDB(path=path)
    ids = {r[0] for r in db.conn.execute("SELECT id FROM schema_migrations")}

    assert {"auth.users.email", "auth.password_resets"} <= ids
