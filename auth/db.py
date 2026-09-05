# -*- coding: utf-8 -*-
"""
Kullanici + refresh token deposu — SQLite, ORM YOK (diger db modulleriyle ayni stil).
Ham refresh token asla saklanmaz; sadece sha256 hash'i tutulur.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from db.migrate import ensure_columns, record_migration

DB_DIR = Path.home() / ".gls_pod"
DB_DIR.mkdir(exist_ok=True)
DEFAULT_DB = DB_DIR / "auth.db"

# `viewer` salt okuma: takip/POD/sorunlu koli/POD arsivi gorur, yazamaz.
# Hangi sayfanin hangi role acik oldugu `auth/access.py`dedir.
ROLES = ("admin", "operator", "viewer")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'operator',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_hash ON refresh_tokens(token_hash);

-- Giris denemeleri. BASARISIZ olanlar da yazilir: bir saldiri ancak "ayni IP'den
-- 200 hatali deneme" gibi bir izden anlasilir, basarili girisler onu gostermez.
-- Kullanici adi serbest metindir (var olmayan bir ad da denenebilir), bu yuzden
-- users tablosuna YABANCI ANAHTAR YOK.
CREATE TABLE IF NOT EXISTS login_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    ip         TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    success    INTEGER NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_events_at ON login_events(at);
CREATE INDEX IF NOT EXISTS idx_login_events_ip ON login_events(ip);

CREATE TABLE IF NOT EXISTS blocked_ips (
    ip         TEXT PRIMARY KEY,
    note       TEXT NOT NULL DEFAULT '',
    blocked_by TEXT NOT NULL DEFAULT '',
    at         TEXT NOT NULL
);

-- Sifre sifirlama baglantilari. Ham belirtec ASLA saklanmaz (refresh_tokens ile
-- ayni gerekce): DB sizarsa elindeki hash'le kimse baglanti uretemez.
-- `used_at` tek kullanimlik yapar — bagalanti mail kutusunda kalir, ikinci kez
-- tiklanmasi (ya da mail kutusuna sonradan erisen birinin tiklamasi) ise yaramaz.
CREATE TABLE IF NOT EXISTS password_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at    TEXT NOT NULL DEFAULT '',
    created_ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reset_hash ON password_resets(token_hash);
CREATE INDEX IF NOT EXISTS idx_reset_user ON password_resets(user_id);
"""

# Giris kaydi yalnizca "kim, nereden, ne zaman" sorusu icindir; suresiz
# saklamanin degeri yok, ama bir bot saniyede onlarca satir yazabilir. Her
# yazmada eskiler silinir — tablo kendi kendini sinirlar.
LOGIN_EVENT_RETENTION_DAYS = 90

# Kullanilmis/suresi gecmis sifirlama kayitlari bir hafta sonra ise yaramaz;
# `login_events` gibi her yazmada temizlenir.
PASSWORD_RESET_RETENTION_DAYS = 7

# Sonradan eklenen kolonlar (mevcut auth.db'ler ALTER ile guncellenir).
# `email` UNIQUE DEGIL: SQLite `ADD COLUMN` UNIQUE kabul etmiyor, ayrica ortak
# posta kutusu (depo@...) mesru bir kullanim — sifirlama baglantisi o kutudaki
# TUM aktif hesaplara ayri ayri gider (bkz. users_by_email).
EXTRA_COLUMNS = [
    ("full_name", "TEXT NOT NULL DEFAULT ''"),
    ("email", "TEXT NOT NULL DEFAULT ''"),
]


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


class AuthDB:
    def __init__(self, path: Path = DEFAULT_DB):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        ensure_columns(self.conn, "users", EXTRA_COLUMNS)
        record_migration(self.conn, "auth.users.extra_columns")
        record_migration(self.conn, "auth.users.email")
        record_migration(self.conn, "auth.password_resets")

    # ---------- users ----------

    def create_user(self, username: str, password_hash: str, role: str = "operator",
                    full_name: str = "", email: str = "") -> int:
        if role not in ROLES:
            raise ValueError(f"Gecersiz rol: {role}")
        cur = self.conn.execute(
            "INSERT INTO users (username, password_hash, role, full_name, email, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, password_hash, role, full_name, email.strip().lower(), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_user(self, user_id: int, *, full_name: str, email: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"Gecersiz rol: {role}")
        self.conn.execute(
            "UPDATE users SET full_name = ?, email = ?, role = ? WHERE id = ?",
            (full_name, email.strip().lower(), role, user_id),
        )
        self.conn.commit()

    def delete_user(self, user_id: int) -> None:
        """Kaydi TAMAMEN siler. `login_events` kalir — denetim izi kullanici
        adina baglidir, yabanci anahtari yoktur (bkz. sema). Refresh token'lar
        ve sifirlama kayitlari CASCADE ile gider."""
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()

    def count_active_admins(self) -> int:
        """Son yoneticinin dusurulmesini/silinmesini engellemek icin."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()[0]

    def get_user_by_username(self, username: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None

    def get_user(self, user_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def users_by_email(self, email: str) -> list[dict]:
        """Sifre sifirlama icin: o adrese bagli AKTIF hesaplar.

        Bos adres BOS LISTE doner — yoksa e-postasi girilmemis butun eski
        hesaplar tek bir bos dizeyle eslesir ve birinin sifresi bir baskasinin
        istegiyle sifirlanabilirdi.
        """
        clean = email.strip().lower()
        if not clean:
            return []
        rows = self.conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = ? AND is_active = 1", (clean,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_users(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, username, full_name, email, role, is_active, created_at "
            "FROM users ORDER BY username"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_active(self, user_id: int, active: bool) -> None:
        self.conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id)
        )
        self.conn.commit()

    def update_password(self, user_id: int, password_hash: str) -> None:
        self.conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
        )
        self.conn.commit()

    def count_users(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ---------- refresh tokens ----------

    def store_refresh_token(self, user_id: int, token_hash: str, expires_at: str) -> None:
        self.conn.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, token_hash, expires_at, _now()),
        )
        self.conn.commit()

    def get_refresh_token(self, token_hash: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return dict(row) if row else None

    def revoke_refresh_token(self, token_hash: str) -> None:
        self.conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?", (token_hash,)
        )
        self.conn.commit()

    def revoke_all_for_user(self, user_id: int) -> None:
        """Sifre degisikligi / supheli kullanim sonrasi tum oturumlari duser."""
        self.conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,)
        )
        self.conn.commit()

    # ---------- sifre sifirlama ----------

    def create_password_reset(self, user_id: int, token_hash: str, expires_at: str,
                              ip: str = "") -> None:
        self.conn.execute(
            "INSERT INTO password_resets (user_id, token_hash, expires_at, created_ip, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, token_hash, expires_at, ip, _now()),
        )
        cutoff = (datetime.utcnow() - timedelta(days=PASSWORD_RESET_RETENTION_DAYS)) \
            .isoformat(timespec="seconds")
        # `_now()` "T" ayracli yazar, `datetime('now')` bosluklu doner; duz metin
        # karsilastirmasinda 'T'(0x54) > ' '(0x20) olurdu. Kolonu datetime() ile
        # sararak iki tarafi da ayni bicime getiriyoruz.
        self.conn.execute("DELETE FROM password_resets WHERE datetime(created_at) < datetime(?)",
                          (cutoff,))
        self.conn.commit()

    def get_password_reset(self, token_hash: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM password_resets WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return dict(row) if row else None

    def use_password_reset(self, token_hash: str) -> None:
        self.conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE token_hash = ?", (_now(), token_hash)
        )
        self.conn.commit()

    def invalidate_password_resets(self, user_id: int) -> None:
        """Sifre baska bir yoldan degistiginde bekleyen baglantilari oldurur."""
        self.conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
        self.conn.commit()

    # ---------- giris kaydi ----------

    def record_login(self, username: str, *, ip: str = "", user_agent: str = "",
                     success: bool, reason: str = "") -> None:
        self.conn.execute(
            "INSERT INTO login_events (username, ip, user_agent, success, reason, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, ip, user_agent[:200], 1 if success else 0, reason, _now()),
        )
        cutoff = (datetime.utcnow() - timedelta(days=LOGIN_EVENT_RETENTION_DAYS)) \
            .isoformat(timespec="seconds")
        self.conn.execute("DELETE FROM login_events WHERE at < ?", (cutoff,))
        self.conn.commit()

    @staticmethod
    def _events_where(*, ip: str, username: str,
                      only_failed: bool) -> tuple[str, list]:
        """WHERE tek yerde: sayac ile sayfa AYNI suzgecten gecsin.

        Iki ayri kurucu olsaydi "1243 kayit" derken sayfalar baska bir kumede
        biterdi (`tracking/db.py:_list_where` ayni gerekceyle ayrilmisti).
        """
        sql = " WHERE 1=1"
        params: list = []
        if ip:
            sql += " AND ip = ?"
            params.append(ip)
        if username:
            sql += " AND username LIKE ?"
            params.append(f"%{username}%")
        if only_failed:
            sql += " AND success = 0"
        return sql, params

    def count_login_events(self, *, ip: str = "", username: str = "",
                           only_failed: bool = False) -> int:
        where, params = self._events_where(ip=ip, username=username,
                                           only_failed=only_failed)
        return self.conn.execute(
            "SELECT COUNT(*) FROM login_events" + where, params).fetchone()[0]

    def login_events(self, limit: int = 200, *, ip: str = "", username: str = "",
                     only_failed: bool = False, offset: int = 0) -> list[dict]:
        where, params = self._events_where(ip=ip, username=username,
                                           only_failed=only_failed)
        # `id DESC` ikinci anahtar: ayni saniyede yazilan denemeler sirasiz
        # kalirsa ikinci sayfa birincinin satirlarini tekrarlar.
        sql = ("SELECT * FROM login_events" + where
               + " ORDER BY at DESC, id DESC LIMIT ? OFFSET ?")
        return [dict(r) for r in
                self.conn.execute(sql, [*params, limit, offset]).fetchall()]

    def login_ip_summary(self, limit: int = 50) -> list[dict]:
        """IP basina ozet — "hangi IP kac kez patladi" tek bakista gorunsun."""
        rows = self.conn.execute(
            "SELECT ip, COUNT(*) AS total, SUM(success) AS ok, MAX(at) AS last_at, "
            "       GROUP_CONCAT(DISTINCT username) AS usernames "
            "FROM login_events WHERE ip <> '' GROUP BY ip "
            "ORDER BY last_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- IP engelleme ----------

    def block_ip(self, ip: str, note: str = "", blocked_by: str = "") -> None:
        self.conn.execute(
            "INSERT INTO blocked_ips (ip, note, blocked_by, at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(ip) DO UPDATE SET note = excluded.note, "
            "blocked_by = excluded.blocked_by, at = excluded.at",
            (ip, note, blocked_by, _now()),
        )
        self.conn.commit()

    def unblock_ip(self, ip: str) -> None:
        self.conn.execute("DELETE FROM blocked_ips WHERE ip = ?", (ip,))
        self.conn.commit()

    def is_ip_blocked(self, ip: str) -> bool:
        if not ip:
            return False
        return self.conn.execute(
            "SELECT 1 FROM blocked_ips WHERE ip = ?", (ip,)
        ).fetchone() is not None

    def blocked_ips(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM blocked_ips ORDER BY at DESC").fetchall()]

    def close(self):
        self.conn.close()
