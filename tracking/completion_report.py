# -*- coding: utf-8 -*-
"""Siparis tamamlanma raporu (`/reports/completion`).

İş biriminin istegi (2026-08-31): "80000 adet urun siparisi var 60000 tane
urun tr den cikti tamamlanma 6/8 ya bir de bunun aslinda musterinin eline
ulasmis olanlari var". Uc seviye:

    1. SIPARIS  OrderQty   — ERP (bkz. erp/orders.py)
    2. SEVK     InvQty     — ERP; "TR'den cikti" (picklenen/faturalanan)
    3. TESLIM              — BIZIM takip verimiz: teslim edilmis kolilerin
                             icindeki urun adedi (erp/contents.py)

KAPSAM FARKI (rapor bunu ekranda da soyler): 1. ve 2. seviye Onun
suzgecinden gelir (CA/US/RU/AU/NZ haric — yani IE, GB, TW, IL, SG, MX dahil),
3. seviye ise YALNIZCA bizim takip ettigimiz GLS NL kolilerini kapsar
(bkz. erp/sync.py:EXCLUDED_COUNTRIES — orada IE de haric). Olculdu
(2026-08-31, FA26): siparis 68.141, sevk 47.938, takipteki kutulanmis 37.216.
Aradaki fark eksik veri DEGIL, kapsam farkidir; teslim yuzdesi bu yuzden
SEVK EDILENE degil, takipteki kutulanmis adede gore de ayrica verilir.
"""
from __future__ import annotations

from collections import defaultdict

from gls_api import config
from tracking.db import ShipmentsDB


def _pct(part: int, whole: int) -> float | None:
    """Yuzde; payda 0 ise None (ekranda "—" gorunur, "%0" DEGIL — ikisi ayni
    sey degildir: biri "hic siparis yok", digeri "hicbiri sevk edilmedi")."""
    return round(part / whole * 100, 1) if whole else None


def _tracked_quantities(db: ShipmentsDB, season: str) -> tuple[dict, dict, bool]:
    """(ulke -> kutulanmis adet, ulke -> teslim adedi, ERP'ye ulasildi mi).

    MSSQL yoksa/hata verirse bos sozlukler ve False doner — rapor yine acilir,
    yalnizca 3. seviye bos kalir (bkz. tracking/delivery_report.py'deki ayni
    desen: urun adedi ERP'ye baglidir, rapor ona baglanmamalidir).
    """
    boxed: dict[str, int] = defaultdict(int)
    delivered: dict[str, int] = defaultdict(int)
    if not config.mssql_configured():
        return boxed, delivered, False

    rows = db.conn.execute(
        "SELECT tracking_no, country, status FROM parcels WHERE season = ?",
        (season,),
    ).fetchall()
    if not rows:
        return boxed, delivered, True

    try:
        from erp.client import ERPClient
        from erp.contents import total_quantities
        quantities = total_quantities(ERPClient(timeout=180),
                                      [r["tracking_no"] for r in rows])
    except Exception:
        return boxed, delivered, False

    for r in rows:
        qty = quantities.get(r["tracking_no"], 0)
        if not qty:
            continue
        country = (r["country"] or "—").strip().upper() or "—"
        boxed[country] += qty
        if r["status"] == "delivered":
            delivered[country] += qty
    return boxed, delivered, True


def build_completion_report(db: ShipmentsDB, season: str = "") -> dict:
    """`/reports/completion` sayfasinin tum baglamini kurar."""
    season = (season or config.season_label() or "").upper()
    empty = {
        "has_data": False, "season": season, "tracked": False,
        "order_qty": 0, "inv_qty": 0, "boxed_qty": 0, "delivered_qty": 0,
        "order_count": 0, "customer_count": 0,
        "pct_shipped": None, "pct_delivered": None, "pct_delivered_of_boxed": None,
        "by_country": [],
    }
    if not config.mssql_configured():
        return empty

    try:
        from erp.client import ERPClient
        from erp.orders import fetch_by_country, fetch_totals
        client = ERPClient(timeout=180)
        totals = fetch_totals(client, season)
        by_country = fetch_by_country(client, season)
    except Exception:
        # Gorunum henuz acilmamis olabilir (yeni sezon) ya da MSSQL kapali —
        # panel bir izleme araci, tek bir rapor onu 500'e dusurmemeli.
        return empty

    boxed, delivered, tracked = _tracked_quantities(db, season)

    rows = []
    for country, o in sorted(by_country.items(),
                             key=lambda kv: kv[1]["order_qty"], reverse=True):
        rows.append({
            "country": country,
            "order_count": o["order_count"],
            "order_qty": o["order_qty"],
            "inv_qty": o["inv_qty"],
            "boxed_qty": boxed.get(country, 0),
            "delivered_qty": delivered.get(country, 0),
            "pct_shipped": _pct(o["inv_qty"], o["order_qty"]),
            "pct_delivered": _pct(delivered.get(country, 0), o["order_qty"]),
            # Bu ulkeyi hic takip etmiyorsak (IE, GB, TW...) sutun bos kalmali;
            # "%0" yazmak "hic teslim edilmedi" demek olurdu, oysa olcmuyoruz.
            "is_tracked": boxed.get(country, 0) > 0,
        })

    boxed_total = sum(boxed.values())
    delivered_total = sum(delivered.values())
    return {
        "has_data": totals["order_qty"] > 0,
        "season": season,
        "tracked": tracked,
        "order_qty": totals["order_qty"],
        "inv_qty": totals["inv_qty"],
        "boxed_qty": boxed_total,
        "delivered_qty": delivered_total,
        "order_count": totals["order_count"],
        "customer_count": totals["customer_count"],
        "pct_shipped": _pct(totals["inv_qty"], totals["order_qty"]),
        "pct_delivered": _pct(delivered_total, totals["order_qty"]),
        "pct_delivered_of_boxed": _pct(delivered_total, boxed_total),
        "by_country": rows,
    }


COMPLETION_EXPORT_COLUMNS = [
    ("reports.col.country", "country"),
    ("completion.col.orders", "order_count"),
    ("completion.col.ordered", "order_qty"),
    ("completion.col.shipped", "inv_qty"),
    ("completion.col.boxed", "boxed_qty"),
    ("completion.col.delivered", "delivered_qty"),
]


def completion_export_table(report: dict) -> tuple[list[str], list[list]]:
    """`/reports/completion/export.*` — ekrandaki tabloyla AYNI kolonlar,
    artiya iki yuzde sutunu (ekranda cubuk olarak cizilenler)."""
    import i18n
    headers = ([i18n.t(key) for key, _ in COMPLETION_EXPORT_COLUMNS]
               + [i18n.t("completion.col.pct_shipped"),
                  i18n.t("completion.col.pct_delivered")])
    no_value = i18n.t("reports.no_value")
    rows = []
    for r in report["by_country"]:
        rows.append(
            [r[field] for _, field in COMPLETION_EXPORT_COLUMNS]
            + [r["pct_shipped"] if r["pct_shipped"] is not None else no_value,
               r["pct_delivered"] if r["is_tracked"] else no_value]
        )
    return headers, rows
