# -*- coding: utf-8 -*-
"""cli.py seed_admin() testleri — geçici bir auth.db'ye karşı çalışır,
gerçek ~/.gls_pod/auth.db'ye dokunmaz. Çalıştırma: pytest -q"""
import pytest

from auth.db import AuthDB
from auth.security import verify_password
from cli import seed_admin


@pytest.fixture
def db(tmp_path):
    return AuthDB(path=tmp_path / "auth.db")


def test_creates_new_admin(db):
    message = seed_admin(db, "admin", "S3cret!23", force=False)
    assert "oluşturuldu" in message
    user = db.get_user_by_username("admin")
    assert user["role"] == "admin"
    assert verify_password("S3cret!23", user["password_hash"])


def test_refuses_existing_without_force(db):
    seed_admin(db, "admin", "S3cret!23", force=False)
    with pytest.raises(FileExistsError):
        seed_admin(db, "admin", "Other!234", force=False)


def test_force_resets_password_and_revokes_sessions(db):
    seed_admin(db, "admin", "S3cret!23", force=False)
    user = db.get_user_by_username("admin")
    db.store_refresh_token(user["id"], "tokhash", "2099-01-01T00:00:00")

    message = seed_admin(db, "admin", "Other!234", force=True)
    assert "güncellendi" in message

    refreshed = db.get_user_by_username("admin")
    assert verify_password("Other!234", refreshed["password_hash"])
    assert not verify_password("S3cret!23", refreshed["password_hash"])
    token = db.get_refresh_token("tokhash")
    assert token["revoked"] == 1


def test_rejects_empty_password(db):
    with pytest.raises(ValueError):
        seed_admin(db, "admin", "", force=False)


# ---------------------------------------------------------------- ad soyad
def test_full_name_is_stored_and_listed(db):
    db.create_user("sertac.durgun", "hash", role="operator", full_name="Sertaç Durgun")
    assert db.list_users()[0]["full_name"] == "Sertaç Durgun"


def test_existing_database_gains_the_column(tmp_path):
    """full_name sonradan eklendi — eski auth.db'ler ALTER ile gecmeli."""
    import sqlite3

    path = tmp_path / "eski.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        INSERT INTO users (username, password_hash, role, created_at)
        VALUES ('zeynep', 'hash', 'admin', '2026-01-01');
    """)
    conn.commit()
    conn.close()

    db = AuthDB(path=path)
    assert db.get_user_by_username("zeynep")["full_name"] == ""
