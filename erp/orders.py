# -*- coding: utf-8 -*-
"""Siparis tamamlanma verisi: `BCH_{SEZON}_Dolcezza_Orders` (salt okuma).

İş biriminin istegi (2026-08-31):

    BCH_FA26_Dolcezza_Orders
    CustomerCountry not in ('CA','US','RU','AU','NZ')
    Order Qty toplam siparis adeti
    InvQty     picklenenler

"... ornegin 80000 adet urun siparisi var 60000 tane urun tr den cikti
tamamlanma 6/8 ya bir de bunun aslinda musterinin eline ulasmis olanlari var"

Yani UC seviye olculur:

    1. Siparis   -> OrderQty            (bu gorunum)
    2. Sevk      -> InvQty              (bu gorunum; "TR'den cikti")
    3. Teslim    -> bizim takip verimiz (tracking/completion_report.py)

Gorunum SATIR BAZLIDIR (her SKU bir satir: FA26'da 55.632 satir -> 743
siparis), bu yuzden her sey `SUM`/`COUNT(DISTINCT)` ile toplanir.

KAPSAM UYARISI: bu gorunumun ulke suzgeci bizim takip kapsamimizla AYNI
DEGILDIR — Onun listesi IE/GB/TW/IL/SG/MX gibi ulkeleri de icerir, oysa
takip yalnizca GLS NL kanalini kapsar (bkz. erp/sync.py:EXCLUDED_COUNTRIES).
Bu yuzden 3. seviye 1. ve 2. ile ayni evrende DEGILDIR ve rapor bunu acikca
soyler (bkz. `tracking/completion_report.py`).
"""
from __future__ import annotations

from erp.client import ERPClient, safe_identifier
from gls_api import config

# Onun verdigi suzgec. Kanada/ABD ayri bir hat (FedEx), digerleri kapsam
# disi. DIKKAT: `erp/sync.py:EXCLUDED_COUNTRIES` ile ayni liste DEGILDIR —
# orada IE de haric, burada degil (bkz. modul basligi).
EXCLUDED_ORDER_COUNTRIES = ("CA", "US", "RU", "AU", "NZ")

# `WITH (NOLOCK)`: `erp/contents.py` ile ayni gerekce — salt okunur bir panel
# sorgusu yuzunden ERP'nin yazma islemleri beklememeli.
TOTALS_QUERY = """
SELECT COUNT(DISTINCT OrderNumber)  AS order_count,
       COUNT(DISTINCT CustomerCode) AS customer_count,
       SUM(CAST(OrderQty AS BIGINT)) AS order_qty,
       SUM(CAST(InvQty AS BIGINT))   AS inv_qty
FROM {view} WITH (NOLOCK)
WHERE CustomerCountry NOT IN ({slots})
"""

BY_COUNTRY_QUERY = """
SELECT CustomerCountry AS country,
       COUNT(DISTINCT OrderNumber)  AS order_count,
       SUM(CAST(OrderQty AS BIGINT)) AS order_qty,
       SUM(CAST(InvQty AS BIGINT))   AS inv_qty
FROM {view} WITH (NOLOCK)
WHERE CustomerCountry NOT IN ({slots})
GROUP BY CustomerCountry
"""


def orders_view(season: str = "") -> str:
    """`FA26` -> `BCH_FA26_Dolcezza_Orders`.

    Sezon kodu AYRI bir ayar alani DEGIL: `config.season_label()` zaten
    `MSSQL_SEASON_VIEWS`ten turetiyor (bkz. oradaki aciklama), ayni bilgi
    ikinci kez yazilmasin.
    """
    return f"BCH_{safe_identifier(season or config.season_label())}_Dolcezza_Orders"


def _rows(client: ERPClient, query: str, season: str) -> list[dict]:
    slots = ", ".join(["%s"] * len(EXCLUDED_ORDER_COUNTRIES))
    sql = query.format(view=safe_identifier(orders_view(season)), slots=slots)
    return client.query(sql, EXCLUDED_ORDER_COUNTRIES)


def _int(value) -> int:
    return int(value or 0)


def fetch_totals(client: ERPClient, season: str = "") -> dict:
    """{order_count, customer_count, order_qty, inv_qty} — hic satir yoksa sifirlar."""
    rows = _rows(client, TOTALS_QUERY, season)
    row = rows[0] if rows else {}
    return {
        "order_count": _int(row.get("order_count")),
        "customer_count": _int(row.get("customer_count")),
        "order_qty": _int(row.get("order_qty")),
        "inv_qty": _int(row.get("inv_qty")),
    }


def fetch_by_country(client: ERPClient, season: str = "") -> dict[str, dict]:
    """Ulke kodu -> {order_count, order_qty, inv_qty}. Ulkesi bos satirlar '—'."""
    result: dict[str, dict] = {}
    for row in _rows(client, BY_COUNTRY_QUERY, season):
        country = (row.get("country") or "").strip().upper() or "—"
        result[country] = {
            "order_count": _int(row.get("order_count")),
            "order_qty": _int(row.get("order_qty")),
            "inv_qty": _int(row.get("inv_qty")),
        }
    return result
