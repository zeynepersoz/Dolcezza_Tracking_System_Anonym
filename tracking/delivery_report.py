# -*- coding: utf-8 -*-
"""Sevkiyat & teslimat sureleri analiz raporu (`/reports`).

İş biriminin "Dolcezza Data Analyzer" mailindeki bicimin (kullanici istegi,
2026-08-31) panelde ayni sekilde gosterilmesi icin. Uc bacak olculur:

    Paketleme -> GLS'e teslim ("EMC -> GLS")
    GLS'e teslim -> alicya teslim ("GLS -> Butik", normal/gecikmeli ayri)
    Paketleme -> alicya teslim (uctan uca "Ort. Teslimat")

HAFTA SONU (Cumartesi/Pazar) SURELERDEN CIKARILIR: GLS o gunler dagitim
yapmiyor, sayilirsa "3 gun" gibi bir teslimat araya hafta sonu girince
"5 gun" gibi yapay uzun gorunur (kullanici istegi).

"Lokal gecikme" isareti `exception_ever_tracking_nos` ile ayni kaynaktan
gelir (`UD_Problemli` ile ayni tanim) — Onun raporundaki "alicinin
magazada bulunmamasi, ileri tarihli teslimat randevusu" gibi nedenler zaten
bizim EXCEPTION_PHRASES/BLOCKING_PHRASES kumesiyle ortusuyor.

Urun adedi ERP'den (`erp/contents.py:total_quantities`) gelir; MSSQL
yapilandirilmamissa (yerel/mock gelistirme) sessizce 0 sayilir — rapor yine
de acilir, sadece adet sutunu bos gorunur.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean

import i18n
import timez
from gls_api import config
from tracking.db import ShipmentsDB

# Geriye donuk uyum: eski cagiranlar bu onegi bekliyordu. Yeni kod KANAL
# (tasima firmasi) ile suzuyor.
REPORT_TRACKING_PREFIX = "38120177"

# Raporun hazirlanabilecegi tasima firmalari (kullanici istegi 2026-09-03:
# "raporların gls ie ve fedex için de hazırlanması lazım"). Sira sekmelerin
# gorunum sirasi; ilki varsayilan.
REPORT_CHANNELS = ("NL", "IE", "FEDEX")
REPORT_CHANNEL_LABELS = {"NL": "GLS-NL", "IE": "GLS-IE", "FEDEX": "FEDEX"}


def normalize_report_channel(channel: str) -> str:
    """Gecersiz/bos deger -> ilk kanal. URL'den geleni guvene almak icin."""
    ch = (channel or "").strip().upper()
    return ch if ch in REPORT_CHANNELS else REPORT_CHANNELS[0]


def business_days_between(start: datetime, end: datetime) -> float:
    """Iki zaman damgasi arasindaki gun farki — Cumartesi/Pazar SAYILMAZ.

    Gun ici kesirli kismi korunur (11.8 gun gibi): her takvim gunune ayri
    girilir, hafta sonuna denk gelen dilimin SANIYESI bile toplama eklenmez.
    `end <= start` ise 0.0 (bozuk/ters veri sessizce disarida kalir).
    """
    if not start or not end or end <= start:
        return 0.0
    total = timedelta()
    cur = start
    while cur < end:
        day_start = datetime(cur.year, cur.month, cur.day, tzinfo=cur.tzinfo)
        day_end = min(day_start + timedelta(days=1), end)
        if cur.weekday() < 5:  # 0=Pazartesi ... 4=Cuma
            total += day_end - cur
        cur = day_end
    return round(total.total_seconds() / 86400, 2)


def _shipment_midnight(day: str | None):
    """`shipment_date` yalnizca GUNDUR (saat yok) — o gunun YEREL gece
    yarisi paketleme anini temsil eder."""
    if not day:
        return None
    try:
        y, m, d = (int(p) for p in day[:10].split("-"))
        return datetime(y, m, d, tzinfo=timez.TZ)
    except (ValueError, TypeError):
        return None


@dataclass
class _Row:
    tracking_no: str
    country: str
    shipment_method: str
    delayed: bool
    emc_to_gls: float | None
    gls_to_store: float | None
    total: float | None
    items: int = 0


def _load_rows(db: ShipmentsDB, channel: str = "NL") -> list[_Row]:
    """Teslim edilmis (ve tarihleri TAM olan) kolilerin bacak sureleri.

    `channel` = tasima firmasi (NL/IE/FEDEX). Onceden `tracking_no LIKE
    '38120177%'` ile yalnizca NL suzuluyordu.
    """
    delayed_set = db.exception_ever_tracking_nos(channel)
    raw = db.conn.execute(
        "SELECT tracking_no, country, shipment_method, shipment_date, "
        "handed_over_at, delivered_date FROM parcels "
        "WHERE status = 'delivered' AND channel = ? "
        "AND shipment_date IS NOT NULL AND shipment_date != '' "
        "AND delivered_date IS NOT NULL AND delivered_date != ''",
        (channel,),
    ).fetchall()

    rows = []
    for r in raw:
        packed = _shipment_midnight(r["shipment_date"])
        handed = timez.local(r["handed_over_at"])
        delivered = timez.local(r["delivered_date"])
        if not packed or not delivered:
            continue
        rows.append(_Row(
            tracking_no=r["tracking_no"],
            country=(r["country"] or "—").strip().upper() or "—",
            shipment_method=(r["shipment_method"] or "").strip() or "—",
            delayed=r["tracking_no"] in delayed_set,
            emc_to_gls=business_days_between(packed, handed) if handed else None,
            gls_to_store=business_days_between(handed, delivered) if handed else None,
            total=business_days_between(packed, delivered),
        ))
    return rows


def _avg(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(mean(values), 1) if values else None


def _group_stats(rows: list[_Row]) -> dict:
    """Bir grubun (ulke/sevkiyat yontemi/genel) ozet satiri."""
    normal = [r for r in rows if not r.delayed]
    delayed = [r for r in rows if r.delayed]
    return {
        "box_count": len(rows),
        "item_count": sum(r.items for r in rows),
        "avg_emc_to_gls": _avg([r.emc_to_gls for r in rows]),
        "avg_gls_normal": _avg([r.gls_to_store for r in normal]),
        "avg_gls_delayed": _avg([r.gls_to_store for r in delayed]),
        "avg_total_normal": _avg([r.total for r in normal]),
        "avg_total_overall": _avg([r.total for r in rows]),
        "delayed_count": len(delayed),
        "delayed_items": sum(r.items for r in delayed),
    }


def _breakdown(rows: list[_Row], key: str) -> list[dict]:
    groups: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        groups[getattr(r, key)].append(r)
    result = []
    for name, group_rows in groups.items():
        stats = _group_stats(group_rows)
        stats["name"] = name
        result.append(stats)
    result.sort(key=lambda s: s["box_count"], reverse=True)
    return result


def _attach_quantities(rows: list[_Row]) -> None:
    """ERP'den urun adedini cekip satirlara isler. MSSQL yoksa/hataliysa
    sessizce 0 birakilir — rapor urun adedi olmadan da acilmali."""
    if not rows or not config.mssql_configured():
        return
    try:
        from erp.client import ERPClient
        from erp.contents import total_quantities
        quantities = total_quantities(ERPClient(), [r.tracking_no for r in rows])
    except Exception:
        return
    for r in rows:
        r.items = quantities.get(r.tracking_no, 0)


def build_chart_data(db: ShipmentsDB, limit: int = 8, channel: str = "NL") -> dict:
    """Panelin (Dashboard) altindaki mini grafik icin hafif surum.

    `/reports`in aksine urun adedi icin ERP'ye GITMEZ — dashboard her
    acildiginda calisir, o sayfanin aksine birkac saniyelik ERP gecikmesini
    goze alamaz (kullanici istegi, 2026-08-31: "dashbordun altinda grafik").
    """
    rows = _load_rows(db, channel)
    countries = _breakdown(rows, "country")[:limit]
    return {
        "has_data": bool(rows),
        "by_country": [
            {
                "name": c["name"],
                "emc_to_gls": c["avg_emc_to_gls"] or 0,
                "gls_to_store": c["avg_gls_normal"] or 0,
                "box_count": c["box_count"],
            }
            for c in countries
        ],
    }


def build_parcel_shop_report(db: ShipmentsDB, channel: str = "NL") -> dict:
    """ParcelShop'a UGRAMIS kolilerin ozeti — KENDI sayfasi (`/reports/parcel-shop`).

    Kullanici istegi (2026-08-31): "parcel shopa teslim edilen ancak
    musterinin almadigi gonderi de iadeye donebiliyor, returned durumu da
    yanlis oluyor" — Once `/reports/delivery-times`in icine gomulmustu,
    kullanici bunu ayri, kendi menu ogesi olan bir sayfa istedi.

    `channel` = tasima firmasi (NL/IE/FEDEX).
    """
    channel = normalize_report_channel(channel)
    rows = db.parcel_shop_parcels(channel)
    return {
        "rows": rows,
        "total": len(rows),
        "at_risk": sum(1 for r in rows if r["at_risk"]),
        "timed_out": sum(1 for r in rows if r["timed_out"]),
        "returned": sum(1 for r in rows if r["returned"]),
        "collected": sum(1 for r in rows if r["status"] == "delivered"),
        "channel": channel,
        "channels": [(c, REPORT_CHANNEL_LABELS[c]) for c in REPORT_CHANNELS],
    }


PARCEL_SHOP_EXPORT_COLUMNS = [
    ("field.tracking_no", "tracking_no"),
    ("field.store_code", "store_code"),
    ("field.invoice_number", "invoice_number"),
    ("field.country", "country"),
    ("reports.parcel_shop.col.first_at", "first_at"),
    ("field.status", "status"),
]


def parcel_shop_export_table(ps: dict) -> tuple[list[str], list[list]]:
    """`/reports/parcel-shop/export.*` icin (basliklar, satirlar) — ekrandaki
    tabloyla AYNI kolonlar, artiya "Zaman Aşımı" duz metin olarak (rozet degil)."""
    headers = [i18n.t(key) for key, _ in PARCEL_SHOP_EXPORT_COLUMNS] + [i18n.t("reports.parcel_shop.col.timeout")]
    rows = [
        [row.get(field, "") for _, field in PARCEL_SHOP_EXPORT_COLUMNS]
        + [i18n.t("reports.parcel_shop.timed_out") if row["timed_out"] else ""]
        for row in ps["rows"]
    ]
    return headers, rows


def overall_export_table(overall: dict, has_items: bool) -> tuple[list[str], list[list]]:
    """`/reports/delivery-times` genel ozetin duz (baslik, deger) tablosu."""
    pairs = [
        (i18n.t("reports.card.total_title"), overall["box_count"]),
        (i18n.t("reports.col.shipment"), overall["item_count"] if has_items else i18n.t("reports.no_value")),
        (i18n.t("reports.card.normal_title"), overall["avg_total_normal"]),
        (i18n.t("reports.card.courier_title"), overall["avg_gls_normal"]),
        (i18n.t("reports.card.delayed_title"), overall["delayed_count"]),
    ]
    headers = [i18n.t("reports.col.metric"), i18n.t("reports.col.value")]
    return headers, [[label, value if value is not None else i18n.t("reports.no_value")] for label, value in pairs]


def breakdown_export_table(rows: list[dict], name_label_key: str) -> tuple[list[str], list[list]]:
    """`by_country`/`by_method` icin (baslik, satir) — ekrandaki tabloyla AYNI kolonlar."""
    headers = [i18n.t(name_label_key), i18n.t("reports.col.shipment"), i18n.t("reports.col.emc_gls"),
               i18n.t("reports.col.gls_store"), i18n.t("reports.col.avg_delivery"), i18n.t("reports.col.local_delay")]
    no_value = i18n.t("reports.no_value")

    def _leg(normal, delayed):
        parts = []
        if normal is not None:
            parts.append(f"{normal} ({i18n.t('reports.normal')})")
        if delayed is not None:
            parts.append(f"{delayed} ({i18n.t('reports.delayed')})")
        return " / ".join(parts) or no_value

    return headers, [
        [
            row["name"], row["box_count"],
            row["avg_emc_to_gls"] if row["avg_emc_to_gls"] is not None else no_value,
            _leg(row["avg_gls_normal"], row["avg_gls_delayed"]),
            row["avg_total_normal"] if row["avg_total_normal"] is not None else no_value,
            row["delayed_count"] or "",
        ]
        for row in rows
    ]


def build_report(db: ShipmentsDB, channel: str = "NL") -> dict:
    """`/reports/delivery-times` sayfasinin tum baglamini kurar.

    `channel` = tasima firmasi (NL/IE/FEDEX). `channels`/`channel` alanlari
    sablonun sekmeleri cizmesi icin.
    """
    channel = normalize_report_channel(channel)
    rows = _load_rows(db, channel)
    _attach_quantities(rows)

    overall = _group_stats(rows)
    countries = _breakdown(rows, "country")
    methods = _breakdown(rows, "shipment_method")

    return {
        "has_data": bool(rows),
        "has_items": config.mssql_configured(),
        "overall": overall,
        "country_count": len({r.country for r in rows}),
        "by_country": countries,
        "by_method": methods,
        "channel": channel,
        "channels": [(c, REPORT_CHANNEL_LABELS[c]) for c in REPORT_CHANNELS],
    }
