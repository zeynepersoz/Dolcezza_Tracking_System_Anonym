# -*- coding: utf-8 -*-
"""Kısmi teslimatlar (Partial Deliveries) için günlük WhatsApp bildirim modülü.

Bir faturaya bağlı birden fazla koli varken; bazılarının teslim edilip
bazılarının halen teslim beklediği (dağıtımda, yolda veya sorunlu) durumları
tespit eder ve gün sonunda (saat 18:00) WhatsApp bildirim mesajı olarak gönderir.
"""
from __future__ import annotations

import logging
from typing import Optional

from gls_api import config
from notify import whatsapp
from tracking.db import ShipmentsDB, status_label

log = logging.getLogger("notify.partial_delivery")


def _format_date(val: Optional[str]) -> str:
    """ISO veya UTC tarihini 'DD.MM.YYYY HH:MM' veya 'DD.MM.YYYY' formatına çevirir."""
    if not val:
        return "—"
    s = str(val).strip().replace("Z", "")
    day = s[:10]
    parts = day.split("-")
    date_str = f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 else day
    time_str = s[11:16]
    return f"{date_str} {time_str}".strip() if time_str else date_str


def find_partial_deliveries(db: ShipmentsDB) -> list[dict]:
    """Sistemdeki tüm faturaları tarar; kısmi teslim edilmiş olanları gruplayarak döndürür."""
    rows = db.conn.execute("""
        SELECT invoice_number, store_code,
               COUNT(*) as total_count,
               SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) as delivered_count,
               SUM(CASE WHEN status != 'delivered' AND status != 'cancelled' THEN 1 ELSE 0 END) as pending_count
        FROM parcels
        WHERE invoice_number IS NOT NULL AND invoice_number != ''
        GROUP BY invoice_number, store_code
        HAVING delivered_count > 0 AND pending_count > 0
        ORDER BY store_code, invoice_number
    """).fetchall()

    partial_list = []
    for r in rows:
        inv_no = r["invoice_number"]
        store = r["store_code"]

        # Bu faturaya ait tüm kolileri getir
        parcels = db.conn.execute("""
            SELECT * FROM parcels
            WHERE invoice_number = ? AND store_code = ? AND status != 'cancelled'
            ORDER BY status = 'delivered' DESC, tracking_no
        """, (inv_no, store)).fetchall()

        if not parcels:
            continue

        p0 = dict(parcels[0])
        delivered_parcels = [dict(p) for p in parcels if p["status"] == "delivered"]
        pending_parcels = [dict(p) for p in parcels if p["status"] != "delivered"]

        partial_list.append({
            "invoice_number": inv_no,
            "store_code": store,
            "consignee_name": p0.get("consignee_name") or "",
            "country": p0.get("country") or "",
            "sales_rep": p0.get("sales_rep") or "",
            "shipment_date": p0.get("shipment_date") or "",
            "total_count": len(parcels),
            "delivered_count": len(delivered_parcels),
            "pending_count": len(pending_parcels),
            "delivered_parcels": delivered_parcels,
            "pending_parcels": pending_parcels,
        })

    return partial_list


def format_partial_delivery_message(item: dict) -> str:
    """Tek bir kısmi teslimat faturası için zengin formatlı WhatsApp mesaj metni üretir."""
    store = item.get("store_code") or "—"
    consignee = item.get("consignee_name") or ""
    country = item.get("country") or ""
    inv_no = item.get("invoice_number") or "—"
    sales_rep = item.get("sales_rep") or "—"
    total = item.get("total_count", 0)
    del_count = item.get("delivered_count", 0)
    pend_count = item.get("pending_count", 0)

    shipped = _format_date(item.get("shipment_date"))

    store_line = f"{store}"
    if consignee:
        store_line += f" - {consignee}"
    if country:
        store_line += f" ({country})"

    delivered_lines = []
    for p in item.get("delivered_parcels", []):
        tn = p.get("tracking_no", "")
        d_date = _format_date(p.get("delivered_date") or p.get("last_event_at"))
        delivered_lines.append(f"• {tn} - Teslim Edildi ({d_date})")

    pending_lines = []
    for p in item.get("pending_parcels", []):
        tn = p.get("tracking_no", "")
        st_label = status_label(p.get("status", "transit"))
        evt_text = p.get("last_event_text") or p.get("explain") or ""
        note = f" - *{st_label}*"
        if evt_text:
            note += f"\n  └ Durum: {evt_text}"
        pending_lines.append(f"• {tn}{note}")

    delivered_block = "\n".join(delivered_lines) if delivered_lines else "• —"
    pending_block = "\n".join(pending_lines) if pending_lines else "• —"

    text = (
        f"⚠️ *Kısmi Teslimat Bildirimi (Partial Delivery)* ⚠️\n\n"
        f"📦 *Mağaza:* {store_line}\n"
        f"📄 *Fatura No:* {inv_no}\n"
        f"🗓️ *Sevk Tarihi:* {shipped}\n"
        f"👔 *Ajans / Temsilci:* {sales_rep}\n"
        f"📊 *Koli Durumu:* Toplam {total} koliden *{del_count}* tanesi teslim edildi, *{pend_count}* koli teslim bekliyor.\n\n"
        f"✅ *Teslim Edilen Koliler ({del_count}):*\n"
        f"{delivered_block}\n\n"
        f"⏳ *Teslim Bekleyen Koliler ({pend_count}):*\n"
        f"{pending_block}\n\n"
        f"🌐 _Dolcezza Kargo Takip Sistemi_"
    )
    return text


def send_partial_delivery_notification(item: dict, chat_id: str = "") -> tuple[bool, str]:
    """Tek bir fatura için kısmi teslimat WhatsApp bildirimini gönderir."""
    msg = format_partial_delivery_message(item)
    return whatsapp.send_text(msg, chat_id=chat_id)


def run_partial_deliveries_daily(db: ShipmentsDB, chat_id: str = "") -> tuple[int, list[str]]:
    """Günün kısmi teslimatlarını tarar ve her fatura için WhatsApp bildirimi gönderir.

    Hafta sonu (Cumartesi/Pazar) TAMAMEN atlanır — `notify.whatsapp.handle_tracker_event`
    sorun/iade bildirimlerinde zaten bu kurala uyuyordu, bu is günlük cron
    (`notify/scheduler.py`, saat 18:00) uzerinden gittigi icin o kontrolun
    disinda kalmisti. Canli olcum (2026-08-29/30, kullanici bildirdi): ayni
    icerikli mesaj hem Cumartesi hem Pazar 18:00'de tekrar gitti.
    """
    if str(config.OPENWA_ENABLED).strip() != "1":
        log.info("WhatsApp bildirimleri kapalı (OPENWA_ENABLED!=1), kısmi teslimat atlandı.")
        return 0, []
    if whatsapp._is_weekend():
        log.info("Hafta sonu — kısmi teslimat bildirimi atlandı.")
        return 0, []

    items = find_partial_deliveries(db)
    sent_count = 0
    errors = []

    for item in items:
        ok, res = send_partial_delivery_notification(item, chat_id=chat_id)
        if ok:
            sent_count += 1
        else:
            errors.append(f"{item.get('invoice_number')}: {res}")

    log.info("Kısmi teslimat bildirimleri tamamlandı: %d fatura gönderildi, %d hata",
             sent_count, len(errors))
    return sent_count, errors
