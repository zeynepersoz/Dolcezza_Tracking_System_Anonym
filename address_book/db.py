# -*- coding: utf-8 -*-
"""
Adres Defteri — SQLite tabanlı, ORM YOK.
Kullanıcı ev dizini altında saklanır: ~/.gls_pod/addressbook.db
Yedeklenebilir, taşınabilir, hafif.
"""
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

import timez
from db.migrate import ensure_columns, record_migration

DEFAULT_DB_PATH = Path(os.path.expanduser("~")) / ".gls_pod" / "addressbook.db"

# Alan adları — GUI, importers ve şablonlar bu adları paylaşır.
# name2/name3, house_number, addition, address_type, consignee_id:
# GLS NL (printship.gls.nl) ve GLS Group ShipIT web portallarının gerçek adres
# formlarındaki alan yapısına uyumlu (bkz. docs/URUNLESME_YOL_HARITASI.md §6.1).
FIELDS = [
    "name", "name2", "name3", "company", "address_type",
    "street", "house_number", "addition", "postal_code", "city", "region",
    "country", "consignee_id", "contact", "phone", "mobile", "email",
    "tax_no", "notes", "tags",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS addresses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    name2        TEXT    DEFAULT '',
    name3        TEXT    DEFAULT '',
    company      TEXT    DEFAULT '',
    address_type TEXT    DEFAULT 'business',  -- 'business' | 'private'
    street       TEXT    DEFAULT '',
    house_number TEXT    DEFAULT '',
    addition     TEXT    DEFAULT '',
    postal_code  TEXT    DEFAULT '',
    city         TEXT    DEFAULT '',
    region       TEXT    DEFAULT '',
    country      TEXT    DEFAULT '',
    consignee_id TEXT    DEFAULT '',          -- GLS'te kayitli alici referans kodu
    contact      TEXT    DEFAULT '',
    phone        TEXT    DEFAULT '',
    mobile       TEXT    DEFAULT '',
    email        TEXT    DEFAULT '',
    tax_no       TEXT    DEFAULT '',
    notes        TEXT    DEFAULT '',
    tags         TEXT    DEFAULT '',      -- virgülle ayrılmış
    is_default_shipper INTEGER DEFAULT 0, -- gonderici olarak sabitlenen tek adres
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_name    ON addresses(name);
CREATE INDEX IF NOT EXISTS idx_country ON addresses(country);
"""

# Eski DB'lerde olmayan yeni kolonlar (SQLite: ALTER TABLE ADD COLUMN ile migrate edilir).
EXTRA_COLUMNS = [
    ("name2", "TEXT DEFAULT ''"),
    ("name3", "TEXT DEFAULT ''"),
    ("address_type", "TEXT DEFAULT 'business'"),
    ("house_number", "TEXT DEFAULT ''"),
    ("addition", "TEXT DEFAULT ''"),
    ("consignee_id", "TEXT DEFAULT ''"),
    ("mobile", "TEXT DEFAULT ''"),
    ("is_default_shipper", "INTEGER DEFAULT 0"),
]


class AddressBook:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # web/deps.py bunu tek bir singleton olarak paylasiyor; FastAPI ise senkron
        # endpoint'leri thread havuzunda calistiriyor (bkz. tracking/db.py, dispatch/db.py).
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        ensure_columns(self.conn, "addresses", EXTRA_COLUMNS)
        record_migration(self.conn, "address_book.addresses.extra_columns")

    # ------------------------------------------------ CRUD
    def add(self, **kwargs) -> int:
        row = self._sanitize(kwargs)
        now = timez.utc_iso()
        cols = ", ".join(FIELDS) + ", created_at, updated_at"
        placeholders = ", ".join(["?"] * (len(FIELDS) + 2))
        values = [row.get(f, "") for f in FIELDS] + [now, now]
        cur = self.conn.execute(
            f"INSERT INTO addresses ({cols}) VALUES ({placeholders})", values)
        self.conn.commit()
        return cur.lastrowid

    def update(self, address_id: int, **kwargs) -> None:
        row = self._sanitize(kwargs)
        if not row:
            return
        row["updated_at"] = timez.utc_iso()
        cols = ", ".join(f"{k}=?" for k in row.keys())
        self.conn.execute(f"UPDATE addresses SET {cols} WHERE id=?",
                          list(row.values()) + [address_id])
        self.conn.commit()

    def delete(self, address_id: int) -> None:
        self.conn.execute("DELETE FROM addresses WHERE id=?", (address_id,))
        self.conn.commit()

    def get(self, address_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM addresses WHERE id=?",
                                (address_id,)).fetchone()
        return dict(row) if row else None

    def list(self, search: str = "", country: str = "", address_type: str = "",
             limit: int = 500) -> list:
        query = "SELECT * FROM addresses WHERE 1=1"
        params: list = []
        if search:
            like = f"%{search.strip()}%"
            query += (" AND (name LIKE ? OR company LIKE ? OR city LIKE ?"
                      " OR email LIKE ? OR tags LIKE ?)")
            params += [like, like, like, like, like]
        if country:
            query += " AND UPPER(country) = UPPER(?)"
            params.append(country)
        if address_type:
            query += " AND address_type = ?"
            params.append(address_type)
        query += " ORDER BY name COLLATE NOCASE LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM addresses").fetchone()[0]

    def find_by_store_code(self, code: str) -> list:
        """Magaza koduyla adres arar — toplu etiketin tek eslesme yolu.

        Kod `name2`de durur (GLS etiketi de bu alani basiyor); eski kayitlarda
        `name3`e girilmis olabilir. ISIM BENZERLIGINE BAKILMAZ: denendi, dort
        "eslesme"nin ikisi yanlisti ("Boutique Nora" -> "boutique norda"),
        yanlis adrese etiket basmak koliyi kaybettirir.
        """
        code = (code or "").strip().upper()
        if not code:
            return []
        rows = self.conn.execute(
            "SELECT * FROM addresses WHERE UPPER(name2) = ? OR UPPER(name3) = ?"
            " ORDER BY id", (code, code)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------ Gonderici (sabit sirket adresi)
    def set_default_shipper(self, address_id: int) -> None:
        """Tek bir adresi 'bizim' gonderici adresimiz olarak isaretler (digerlerini temizler)."""
        self.conn.execute("UPDATE addresses SET is_default_shipper = 0")
        self.conn.execute(
            "UPDATE addresses SET is_default_shipper = 1 WHERE id = ?", (address_id,)
        )
        self.conn.commit()

    def get_default_shipper(self) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM addresses WHERE is_default_shipper = 1 LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _dedupe_key(row: dict) -> tuple:
        """Magaza kodu varsa TEK anahtar odur; yoksa isim+sehir'e duselir.

        Isim+sehir tek basina yetmiyor: GLS disa aktariminda ayni ad ve sehirde
        farkli kodlu iki kayit var (ANQDE1N, WNQHU1N) — kodsuz ikizleri onlari
        sessizce yutuyordu.
        """
        code = (row.get("name2") or row.get("name3") or "").strip().upper()
        if code:
            return ("code", code)
        return ("name", (row.get("name") or "").lower(), (row.get("city") or "").lower())

    def bulk_add(self, rows: Iterable[dict]) -> int:
        """Toplu ekleme (CSV/JSON import için). Yinelenen kayıtlar atlanır."""
        added = 0
        existing = {self._dedupe_key(r) for r in self.list(limit=10000)}
        for row in rows:
            row = self._sanitize(row)
            if not row.get("name"):
                continue
            key = self._dedupe_key(row)
            if key in existing:
                continue
            self.add(**row)
            existing.add(key)
            added += 1
        return added

    # ------------------------------------------------ Util
    @staticmethod
    def _sanitize(data: dict) -> dict:
        """Sadece bilinen alanları geçir; None → ''."""
        return {k: ("" if v is None else str(v)).strip()
                for k, v in data.items() if k in FIELDS}

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
