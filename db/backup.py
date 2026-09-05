# -*- coding: utf-8 -*-
"""SQLite veritabanlarinin gunluk yedegi.

Kullanici karari (guvenlik denetimi, 2026-08-24): `data/` klasorundeki 3
SQLite dosyasi (shipments/addressbook/auth) TEK VM'de, TEK diskte — otomatik
yedek YOKTU, disk arizasi butun gecmisi goturur. Elle alinan tek seferlik
yedek (bkz. sohbet gecmisi) bunun yerine gecmez.

Bu modul hicbir ust modulu import ETMEZ (bkz. `db/migrate.py`nin ayni kurali):
hangi dosyalarin yedeklenecegi CAGIRAN taraftan (web/main.py) verilir.

Duz dosya kopyasi DEGIL `sqlite3.Connection.backup()` kullanilir: uygulama
calisirken (WAL/journal aktifken) bir DB dosyasini `shutil.copy` ile kopyalamak
yarim yazilmis/tutarsiz bir kopya uretebilir. `.backup()` SQLite'in kendi API'si,
canli baglantidan tutarli bir goruntu alir.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("db.backup")


def backup_one(src: Path, dest_dir: Path, stamp: str) -> Path | None:
    """Tek bir SQLite dosyasini `dest_dir/{ad}-{stamp}.db` olarak yedekler.

    Kaynak yoksa (henuz olusmamis DB) sessizce atlanir — ilk acilista
    `addressbook.db` gibi dosyalar olmayabilir.
    """
    if not src.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{src.stem}-{stamp}.db"
    source_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    return dest


def _prune(dest_dir: Path, keep_days: int) -> int:
    """`keep_days`den eski yedekleri siler. Silinen dosya sayisini dondurur.

    Dosya adindaki YYYYMMDD damgasina gore karar verilir (mtime degil): sunucu
    saati/saat dilimi degisse de yedegin GERCEKTEN hangi gune ait oldugu isim-
    den okunur.
    """
    if not dest_dir.exists():
        return 0
    cutoff = datetime.now(timezone.utc).date()
    removed = 0
    for f in dest_dir.glob("*-*.db"):
        try:
            stamp = f.stem.rsplit("-", 1)[-1]
            day = datetime.strptime(stamp, "%Y%m%d").date()
        except ValueError:
            continue
        if (cutoff - day).days > keep_days:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


def run_backup(sources: list[Path], dest_dir: Path, keep_days: int = 14) -> dict:
    """Verilen DB dosyalarini yedekler, `keep_days`den eskileri siler.

    Doner: {"backed_up": [...], "pruned": N, "errors": [...]} — cagiran
    (zamanlanmis is) bunu loglar, hicbir hata uygulamayi dusurmez.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    backed_up: list[str] = []
    errors: list[str] = []
    for src in sources:
        try:
            dest = backup_one(src, dest_dir, stamp)
            if dest:
                backed_up.append(dest.name)
        except Exception as exc:                # noqa: BLE001 — biri patlasa da digerleri denensin
            log.exception("Yedekleme basarisiz: %s", src)
            errors.append(f"{src.name}: {exc}")

    pruned = _prune(dest_dir, keep_days)
    if backed_up:
        log.info("Yedek alindi: %s -> %s (silinen eski yedek: %d)",
                 ", ".join(backed_up), dest_dir, pruned)
    if errors:
        log.error("Yedekleme hatalari: %s", "; ".join(errors))
    return {"backed_up": backed_up, "pruned": pruned, "errors": errors}
