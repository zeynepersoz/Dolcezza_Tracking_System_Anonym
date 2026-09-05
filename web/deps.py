"""Paylasilan bagimliklar (singleton'lar)."""
from __future__ import annotations

from pathlib import Path

import timez
from gls_api import config, providers
from address_book.db import AddressBook
from tracking.db import ShipmentsDB, explain_text, status_label, STATUS_COLOR
from tracking.scheduler import hours_since
from auth.db import AuthDB
from settings.store import SettingsStore

# GLS istemcileri gls_api/providers.py'de tutulur — hangi saglayicinin
# kullanilacagi orada secilir; burada sadece yeniden disa aktariliyorlar.
get_shipit = providers.get_shipit
get_tt = providers.get_tt
get_nl = providers.get_nl

# Singleton'lar (uygulama omru boyunca yasar)
_shipments_db: ShipmentsDB | None = None
_address_book: AddressBook | None = None
_address_source = None
_auth_db: AuthDB | None = None
_settings_store: SettingsStore | None = None


def get_settings_store() -> SettingsStore:
    """Panelden girilen ayarlar (settings.db). Diger DB'lerden AYRI dosya."""
    global _settings_store
    if _settings_store is None:
        _settings_store = SettingsStore()
    return _settings_store


def get_auth_db() -> AuthDB:
    global _auth_db
    if _auth_db is None:
        _auth_db = AuthDB()
    return _auth_db


def get_shipments_db() -> ShipmentsDB:
    global _shipments_db
    if _shipments_db is None:
        _shipments_db = ShipmentsDB()
    return _shipments_db


def get_address_book() -> AddressBook:
    global _address_book
    if _address_book is None:
        _address_book = AddressBook()
    return _address_book


def get_address_source():
    """Alici adres kaynagi — canlida MSSQL (Erp_Address), mock/test'te yerel defter.

    Etiket ve dispatch alici listesini besler. Gonderici (get_default_shipper)
    ayri kalir; o hala yerel defterdedir.
    """
    global _address_source
    if _address_source is None:
        from web.address_source import build_address_source
        _address_source = build_address_source()
    return _address_source


def tracker_alarm() -> dict | None:
    """Tarama uzun suredir basarisizsa serit icin bilgi; saglikliysa None.

    HER sayfaya konuyor (bkz. `template_context`), yalnizca panele degil:
    kullanici gunun buyuk kismini /tracking ve /pod'da geciriyor, tarama
    olduysa dashboard'daki karti gormeden calismaya devam ederdi.

    Esik uyari maili ve `/health` ile AYNI (`TRACKER_ALERT_HOURS`); ucunun
    farkli sey soylemesi teshisi zorlastirirdi. Maliyet tek satirlik SELECT.
    """
    limit = config.TRACKER_ALERT_HOURS
    if limit <= 0:
        return None
    info = get_shipments_db().health()
    # Hic tur donmemisse `last_run_at` da bostur — acilisin ilk dakikalari
    # ariza degildir, serit gosterilmez.
    stale = hours_since(info.get("last_success_at") or info.get("last_run_at"))
    if stale is None or stale < limit:
        return None
    return {
        "hours": round(stale, 1),
        "last_success_at": info.get("last_success_at"),
        "last_error": info.get("last_error"),
    }


def whatsapp_alarm() -> dict | None:
    """OpenWA oturumu bagli DEGILSE serit icin bilgi; saglikliysa None.

    Neden var: WhatsApp Web oturumu telefondan koptugunda OpenWA sunucusu
    ayakta kalir, panel de hicbir hata gormez — mesajlar sessizce kaybolur.
    Canli olcum (2026-09-01): oturum 29 Agustos'ta dusmus, UC GUN kimse fark
    etmemis, 31 Agustos'taki 23 teslimat bildirimi hic gitmemisti.

    Maliyet: 2 dakikalik onbellekli tek bir HTTP cagrisi (bkz. session_status).
    """
    from notify.whatsapp import session_status
    try:
        status = session_status()
    except Exception:                                   # noqa: BLE001
        return None                                     # kontrol paneli dusurmez
    if status.get("ok"):
        return None
    return {"status": status.get("status", ""), "detail": status.get("detail", "")}


def template_context() -> dict:
    """Tum sablonlara injecte edilen ortak degiskenler."""
    return {
        "mode": config.MODE,
        # Kullaniciya gorunen saat -> yerel. `datetime.now()` konteynerde UTC
        # dondugu icin ust barda 3 saat geride yaziyordu.
        "now": timez.stamp(timez.now()),
        "status_label": status_label,
        "explain_text": explain_text,
        "status_color": STATUS_COLOR.get,
        # MSSQL yapilandirilmamissa "MSSQL'den Yenile" dugmesi hic gosterilmez.
        "mssql_ready": config.mssql_configured(),
        "tracker_alarm": tracker_alarm(),
        # WhatsApp oturumu koptuysa serit (yalnizca admin gorur — bkz. base.html).
        "wa_alarm": whatsapp_alarm(),
    }


# Cikti dizini (POD PDF'leri)
OUTPUT_DIR = Path(config.OUTPUT_DIR).expanduser() if hasattr(config, "OUTPUT_DIR") else Path.home() / "gls_pod_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
