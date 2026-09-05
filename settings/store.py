# -*- coding: utf-8 -*-
"""Panelden girilen ayarların SQLite deposu.

Neden ayrı bir DB: `.env` konteynere mount EDİLMİYOR (docker-compose `env_file:`
kullanıyor, `volumes:` yalnızca `./data` ve `./output`), yani uygulama `.env`'i
fiziksel olarak yazamaz. Ayarlar bu yüzden mount'lu `data/` altında durur.

`shipments.db` yerine ayrı dosya: takip verisi bir sıfırlamada (`erp.sync --reset`)
silinebilir; ayarların onunla birlikte gitmemesi gerekir.

Sırlar DÜZ METİN durur, dosya izni 0600'dür. Bilinçli: şifrelenecek bir anahtar yok
(`AUTH_SECRET_KEY` boşsa her açılışta yeniden üretiliyor), o yüzden şifreleme
tiyatro olurdu. Bugünkü `.env` de aynı derecede açıktır.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import i18n

from db.migrate import ensure_columns, record_migration

from .schema import ALL_FIELDS, BY_SLUG, Field

DB_DIR = Path.home() / ".gls_pod"
DB_DIR.mkdir(exist_ok=True)
DEFAULT_DB = DB_DIR / "settings.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
-- Denetim izi. DEGER YAZILMAZ: sirlar log'a da tabloya da dusmemeli.
CREATE TABLE IF NOT EXISTS settings_audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    key    TEXT NOT NULL,
    action TEXT NOT NULL,          -- 'set' | 'clear'
    at     TEXT NOT NULL,
    by     TEXT
);
"""

# Su an bos: settings/settings_audit semasi degismedi. Ileride kolon eklenirse
# diger DB dosyalariyla ayni yardimciyla buraya eklenir (bkz. db/migrate.py).
EXTRA_COLUMNS: list[tuple[str, str]] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SettingsStore:
    def __init__(self, path: Path | str = DEFAULT_DB):
        path = Path(path)
        # check_same_thread=False: web/deps.py bunu singleton olarak paylasiyor,
        # FastAPI senkron uclari thread havuzunda calistiriyor.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        ensure_columns(self.conn, "settings", EXTRA_COLUMNS)
        record_migration(self.conn, "settings.settings.extra_columns")
        self.conn.commit()
        # Sirlar duz metin — dosya izni tek at-rest savunmasi.
        os.chmod(path, 0o600)

    # ------------------------------------------------------------------ okuma
    def raw(self) -> dict[str, str]:
        """Ham anahtar->deger. YALNIZCA sunucu ici — sir degerleri icerir."""
        return {r["key"]: r["value"]
                for r in self.conn.execute("SELECT key, value FROM settings")}

    def typed(self) -> dict[str, object]:
        """Şemaya göre tipi çevrilmiş değerler — `config.apply_overrides` bunu alır.

        Bilinmeyen veya bozuk kayıtlar sessizce atlanır: eski bir sürümden kalmış
        bir satır yüzünden uygulamanın açılmaması kabul edilemez.
        """
        out: dict[str, object] = {}
        for key, value in self.raw().items():
            field = ALL_FIELDS.get(key)
            if field is None:
                continue
            if field.type == "int":
                try:
                    out[key] = int(value)
                except ValueError:
                    continue
            else:
                out[key] = value
        return out

    def for_display(self, slug: str) -> list[dict]:
        """Şablona verilecek alan listesi.

        Sır alanlarında `value` HER ZAMAN boştur — sızıntı burada, veri katmanında
        engellenir; şablonun doğru davranmasına güvenilmez.

        Etiketler burada çevrilir: şablonun görebildiği tek yer burası, şemadaki
        Türkçe metin de çeviri yoksa yedek kalır.
        """
        stored = self.raw()
        rows = []
        for f in BY_SLUG[slug].fields:
            has = bool(stored.get(f.key, "").strip())
            rows.append({
                "key": f.key,
                "label": i18n.t_or(f"settings.field.{f.key}", f.label),
                "type": f.type,
                "secret": f.secret,
                "help": i18n.t_or(f"settings.help.{f.key}", f.help),
                "choices": tuple((value, i18n.t_or(f"settings.choice.{f.key}.{value}", label))
                                 for value, label in f.choices),
                "minimum": f.minimum,
                "maximum": f.maximum,
                "has_value": has,
                "value": "" if f.secret else stored.get(f.key, ""),
            })
        return rows

    # ------------------------------------------------------------------ yazma
    def save_section(self, slug: str, form: dict, actor: str | None = None) -> list[str]:
        """Bir bölümü kaydeder; Türkçe doğrulama hatalarını döner.

        Hata varsa HİÇBİR ŞEY yazılmaz — yarım kaydedilmiş bir bölüm, kullanıcının
        gördüğü ekranla sistemin durumunu ayrıştırır.

        Kurallar:
          - Yalnızca `slug` bölümünün şemasındaki anahtarlar kabul edilir (beyaz
            liste). Düzmece bir POST `AUTH_SECRET_KEY` enjekte edemez.
          - Sır alanı boş gönderilirse ATLANIR (mevcut korunur). Silmek icin
            `clear_<KEY>` kutusu.
        """
        section = BY_SLUG[slug]
        errors: list[str] = []
        writes: list[tuple[str, str]] = []
        clears: list[str] = []

        for f in section.fields:
            if form.get(f"clear_{f.key}"):
                clears.append(f.key)
                continue
            if f.key not in form:
                continue
            value = str(form[f.key]).strip()

            if f.secret and not value:
                continue                       # bos = koru

            if f.type == "int":
                error = _validate_int(f, value)
                if error:
                    errors.append(error)
                    continue
            elif f.type == "csv":
                value = ",".join(v.strip() for v in value.split(",") if v.strip())
                # Sayi araligi verilmisse her parca dogrulanir (bkz. NOTIFY_HOURS):
                # "9,25" sessizce kabul edilse ayar kaydedilmis gorunur ama o saat
                # hic tetiklenmezdi.
                if f.minimum is not None or f.maximum is not None:
                    bad = [_validate_int(f, v) for v in value.split(",") if v]
                    bad = [e for e in bad if e]
                    if bad:
                        errors.extend(bad)
                        continue
            elif f.type == "select" and f.choices:
                if value not in {c[0] for c in f.choices}:
                    errors.append(f"{f.label}: geçersiz seçim")
                    continue

            writes.append((f.key, value))

        if errors:
            return errors

        now, cur = _now(), self.conn
        for key, value in writes:
            cur.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (key, value, now, actor),
            )
            cur.execute("INSERT INTO settings_audit (key, action, at, by) VALUES (?,?,?,?)",
                        (key, "set", now, actor))
        for key in clears:
            cur.execute("DELETE FROM settings WHERE key = ?", (key,))
            cur.execute("INSERT INTO settings_audit (key, action, at, by) VALUES (?,?,?,?)",
                        (key, "clear", now, actor))
        self.conn.commit()
        return []


def _validate_int(f: Field, value: str) -> str | None:
    # Hata metnindeki etiket de cevrilir; semadaki Turkce metin yedek kalir.
    label = i18n.t_or(f"settings.field.{f.key}", f.label)
    if not value:
        return i18n.t("validate.required", label=label)
    try:
        n = int(value)
    except ValueError:
        return i18n.t("validate.not_number", label=label, value=value)
    if f.minimum is not None and n < f.minimum:
        return i18n.t("validate.min", label=label, minimum=f.minimum)
    if f.maximum is not None and n > f.maximum:
        return i18n.t("validate.max", label=label, maximum=f.maximum)
    return None
