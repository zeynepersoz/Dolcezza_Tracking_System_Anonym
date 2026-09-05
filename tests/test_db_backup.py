# -*- coding: utf-8 -*-
"""Gunluk DB yedegi: backup_one / _prune / run_backup."""
import sqlite3
from datetime import datetime, timedelta, timezone

from db.backup import backup_one, run_backup


def _make_db(path, value="hello"):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()


def test_backup_one_copies_data(tmp_path):
    src = tmp_path / "shipments.db"
    _make_db(src, "abc")
    dest_dir = tmp_path / "backups"

    dest = backup_one(src, dest_dir, "20260824")

    assert dest is not None
    assert dest.exists()
    conn = sqlite3.connect(str(dest))
    assert conn.execute("SELECT v FROM t").fetchone() == ("abc",)


def test_backup_one_missing_source_is_skipped(tmp_path):
    dest = backup_one(tmp_path / "does-not-exist.db", tmp_path / "backups", "20260824")
    assert dest is None


def test_run_backup_skips_missing_and_reports_errors(tmp_path):
    src = tmp_path / "auth.db"
    _make_db(src)
    missing = tmp_path / "addressbook.db"
    dest_dir = tmp_path / "backups"

    result = run_backup([src, missing], dest_dir)

    assert result["backed_up"] == [f"auth-{datetime.now(timezone.utc):%Y%m%d}.db"]
    assert result["errors"] == []


def test_run_backup_prunes_old_files_by_name_not_mtime(tmp_path):
    src = tmp_path / "shipments.db"
    _make_db(src)
    dest_dir = tmp_path / "backups"
    dest_dir.mkdir()

    old_stamp = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y%m%d")
    old_file = dest_dir / f"shipments-{old_stamp}.db"
    old_file.write_bytes(b"old")
    # mtime'i BUGUN olsa da dosya adindaki tarih eski — silinmesi gerekir.

    result = run_backup([src], dest_dir, keep_days=14)

    assert not old_file.exists()
    assert result["pruned"] == 1
