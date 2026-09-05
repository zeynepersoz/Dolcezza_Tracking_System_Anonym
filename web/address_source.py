"""Alici adres kaynagi — CANLIDA MSSQL (Erp_Address), MOCK/TEST'te yerel defter.

Kullanici karari (2026-08-20): *"sen test icin kullan yerelden ama canlida
mssql"*. Iki uc, tek arayuz; cagiran (labels/dispatch) hangi kaynagin verdigini
bilmez. Gelistirme makinesi (GLS_MODE=mock) MSSQL'e HIC gitmez.

Etiket akisi "duzenlenebilir on-doldurma"dir: kaynak yalnizca secim listesini
besler, kullanici formda posta kodu/sokak/sehri tamamlar (bkz. erp/addresses.py).
"""
from __future__ import annotations

import time

from gls_api import config

# ERP sorgusu her sayfa acilisinda MSSQL'e gitmesin diye kisa omurlu onbellek.
# 2308 satir tek sorguda geliyor; be dakikada bir tazelemek yeterince taze,
# canliya yuk bindirmez.
_ERP_TTL_SECONDS = 300


def _duplicate_names(rows: list[dict]) -> set[str]:
    """Birden fazla magazasi olan ad kumesi (kullanici karari: ajans = ad).

    Ayni `name` (Explanation) birden cok kayitta geciyorsa o bir ajansin birden
    fazla magazasidir; secim ekrani bunlari 'birden fazla magaza' diye isaretler
    ki kullanici yanlisini secmesin.
    """
    seen: dict[str, int] = {}
    for r in rows:
        name = (r.get("name") or "").strip().lower()
        if name:
            seen[name] = seen.get(name, 0) + 1
    return {name for name, count in seen.items() if count > 1}


class ERPAddressSource:
    """Canli: Erp_Address'ten okur, kisa sureli onbellekler."""

    def __init__(self, account_id: str | None = None):
        from erp.addresses import DEFAULT_ACCOUNT_ID
        self.account_id = account_id or DEFAULT_ACCOUNT_ID
        self._cache: list[dict] | None = None
        self._fetched_at = 0.0

    def _rows(self) -> list[dict]:
        now = time.monotonic()
        if self._cache is None or (now - self._fetched_at) > _ERP_TTL_SECONDS:
            from erp.client import ERPClient
            from erp.addresses import fetch_addresses
            self._cache = fetch_addresses(ERPClient(), self.account_id)
            self._fetched_at = now
        return self._cache

    def list(self, limit: int = 5000) -> list[dict]:
        return self._rows()[:limit]

    def get(self, address_id) -> dict | None:
        key = str(address_id)
        return next((r for r in self._rows() if str(r.get("id")) == key), None)

    def find_by_store_code(self, code: str) -> list[dict]:
        code = (code or "").strip().upper()
        if not code:
            return []
        return [r for r in self._rows()
                if (r.get("name2") or "").strip().upper() == code]

    def duplicate_names(self) -> set[str]:
        return _duplicate_names(self._rows())

    def invalidate(self) -> None:
        self._cache = None


class LocalAddressSource:
    """Mock/test: yerel adres defterini (address_book) sarar.

    Gonderici disindaki adresler alici olarak listelenir — eski labels_page
    davranisi birebir korunur, test tohumlari bozulmaz.
    """

    @property
    def _book(self):
        # Defter her cagrida `deps`ten TAZE alinir (kimlik cache'lenmez): kaynak
        # tek sefer kurulan singleton'dir; testler `deps._address_book`i yeni bir
        # gecici DB ile degistirdiginde eski deftere yapisip kalmamali.
        from web import deps
        return deps.get_address_book()

    def list(self, limit: int = 5000) -> list[dict]:
        return [a for a in self._book.list(limit=limit)
                if not a.get("is_default_shipper")]

    def get(self, address_id) -> dict | None:
        try:
            return self._book.get(int(address_id))
        except (TypeError, ValueError):
            return None

    def find_by_store_code(self, code: str) -> list[dict]:
        return self._book.find_by_store_code(code)

    def duplicate_names(self) -> set[str]:
        return _duplicate_names(self.list())

    def invalidate(self) -> None:  # yerel defter zaten canli okunur
        pass


def build_address_source():
    """GLS_MODE + ADDRESS_SOURCE'a gore dogru kaynagi kurar.

    Mock/test her zaman yerel defter (gelistirme MSSQL istemez). Canlida
    varsayilan olarak MSSQL yapilandirildiysa dogrudan ERP (Erp_Address) kullanilir.
    MSSQL yapilandirilmamissa yerel deftere duser.
    """
    if config.IS_MOCK:
        return LocalAddressSource()
    if (config.get("ADDRESS_SOURCE") or "erp") == "erp" and config.mssql_configured():
        return ERPAddressSource()
    return LocalAddressSource()
