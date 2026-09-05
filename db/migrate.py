# -*- coding: utf-8 -*-
"""Paylasilan SQLite goc yardimcisi — 4 DB dosyasinda (tracking, address_book,
auth, settings) birebir tekrarlanan `PRAGMA table_info` + `ALTER TABLE`
deseni burada tek yerde toplanir.

Alembic/SQLAlchemy BILINCLI olarak kullanilmadi: proje hicbir yerde ORM
kullanmiyor (stdlib `sqlite3`, 4 dosya, ~30 kolon toplam) — yeni bir
bagimlilik + farkli bir baglanti modeli, orantisiz risk olurdu.

Bu modul hicbir ust modulu import ETMEZ (paylasilan, bagimsiz katman).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def ensure_columns(conn: sqlite3.Connection, table: str,
                   columns: list[tuple[str, str]]) -> None:
    """Eksik kolonlari ekler (SQLite: ALTER TABLE ADD COLUMN).

    `columns`: [(kolon_adi, kolon_tanimi), ...] — kolon_tanimi tip + varsayilan
    icerebilir ("TEXT", "INTEGER DEFAULT 0" gibi). Zaten var olan kolonlar
    sessizce atlanir (idempotent).
    """
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, coldef in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coldef}")


def record_migration(conn: sqlite3.Connection, migration_id: str) -> None:
    """`schema_migrations` tablosuna (yoksa olusturup) bir denetim satiri ekler.

    Idempotent: ayni `migration_id` ikinci kez cagrilirsa yoksayilir. Bu bir
    tetikleyici degil, yalnizca "hangi goc ne zaman uygulandi" izidir —
    `ensure_columns` zaten kendi idempotent kontrolunu yapar.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id         TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (id, applied_at) VALUES (?, ?)",
        (migration_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
