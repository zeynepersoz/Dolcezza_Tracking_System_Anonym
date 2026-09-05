# -*- coding: utf-8 -*-
"""Teslim edilip POD'u gelmeyen kargolar icin GLS'e yazilan mektup.

Metin kullanicinin GLS'e ELLE yazdigi mektubun aynisidir (2026-08-06, konu
"Request for Official Proof of Delivery (POD) / Missing Signatures") — makine
farkli bir dil kurmasin, GLS tarafinda ayni is ayni sekilde acilsin.

Iki tuketici, TEK kaynak:
  * `/problems/gls-inquiry`  — metni gosterir, kullanici kopyalar (duz metin)
  * `notify/scheduler.py`    — teslimattan `wait_days` gun sonra kendisi yollar

Duz metin ve HTML ayni satir verisinden uretilir; ikisi ayri elle yazilsaydi
zamanla birbirinden ayrisirdi.

Metin panelin dilinden BAGIMSIZ olarak Ingilizcedir (`notify.DOC_LANG` ile ayni
gerekce), bu yuzden `i18n` sozlugune girmez: sozluk panel arayuzu icindir.
"""
from __future__ import annotations

import html
import logging

from gls_api import config
from notify import mail

log = logging.getLogger("notify.pod_chase")

RECIPIENT_FALLBACK = "klantenservice@gls-netherlands.com"

SUBJECT = ("Request for Official Proof of Delivery (POD) / Missing Signatures "
           "- Customer No: {customer_no}")

OPENING = (
    'We are reviewing our recent deliveries and noticed that several shipments '
    'are marked as "Delivered" in the portal, but their official Proof of '
    'Delivery (POD) documents—including recipient names and signature '
    'images—are missing. As a result, we are currently unable to archive these '
    'delivery confirmations in our system.'
)

OPENING_HTML = (
    'We are reviewing our recent deliveries and noticed that several shipments '
    'are marked as <strong>"Delivered"</strong> in the portal, but their official '
    '<strong>Proof of Delivery (POD)</strong> documents—including recipient names '
    'and signature images—are missing. As a result, we are currently unable to '
    'archive these delivery confirmations in our system.'
)
LIST_INTRO = "Below is the list of affected tracking numbers and parcel details:"
CLOSING = (
    "Could you please provide the official POD documents (including recipient "
    "signature/name or drop-off location proof) for these shipments as soon as "
    "possible so we can update our archives?"
)
THANKS = "Thank you for your quick assistance."
SIGNATURE = "EMC Logistic / Dolcezza Europe"


def _date(value: str) -> str:
    """'2026-07-30T09:12:00Z' -> '30.07.2026'. Mektupta Avrupa bicimi bekleniyor."""
    day = (value or "")[:10]
    parts = day.split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 else day


def _fields(parcel: dict) -> list[tuple[str, str]]:
    """Bir parcanin mektuba giren (etiket, deger) ciftleri. Bos alan yazilmaz."""
    ref = "-".join(x for x in (parcel.get("store_code"),
                               parcel.get("invoice_number")) if x)
    recipient = parcel.get("consignee_name") or ""
    if parcel.get("country"):
        recipient = f"{recipient} ({parcel['country']})".strip()
    out = [("Tracking No", parcel.get("tracking_no", ""))]
    if ref:
        out.append(("Ref", ref))
    if recipient:
        out.append(("Recipient", recipient))
    if parcel.get("delivered_date"):
        out.append(("Delivered Date", _date(parcel["delivered_date"])))
    return out


def draft(parcels: list[dict]) -> tuple[str, str, str]:
    """(konu, duz metin, html) dondurur. Hicbir sey GONDERMEZ.

    Musteri numarasi mektubun basinda ayri satir: GLS musteri hizmetleri kaydi
    once ona gore aciyor.
    """
    customer_no = config.NL_CUSTOMER_NO or "—"
    subject = SUBJECT.format(customer_no=customer_no)

    rows = [_fields(p) for p in parcels]
    text = "\n".join([
        "Hello,",
        "",
        f"Customer Number: {customer_no}",
        "",
        OPENING,
        "",
        LIST_INTRO,
        "",
        *["• " + " | ".join(f"{label}: {value}" for label, value in row) for row in rows],
        "",
        CLOSING,
        "",
        THANKS,
        "",
        "Best regards,",
        SIGNATURE,
    ])

    def _recip(p):
        name = p.get("consignee_name") or ""
        c = p.get("country")
        return f"{name} ({c})" if c and name else (name or c or "—")

    list_items = "".join(
        f"<li style=\"margin-bottom: 10px; line-height: 1.6;\">"
        f"<strong>Tracking No:</strong> <span style=\"background-color: #fee2e2; color: #e11d48; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-weight: 600;\">{html.escape(p.get('tracking_no', ''))}</span> | "
        f"<strong>Ref:</strong> <span style=\"background-color: #f1f5f9; color: #475569; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-weight: 600;\">{html.escape('-'.join(x for x in (p.get('store_code'), p.get('invoice_number')) if x) or '—')}</span> | "
        f"<strong>Recipient:</strong> {html.escape(_recip(p))} | "
        f"<strong>Delivered Date:</strong> {html.escape(_date(p.get('delivered_date', '')) or '—')}"
        f"</li>"
        for p in parcels
    )

    html_body = (
        f"<div style=\"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 14px; color: #1e293b; line-height: 1.6;\">"
        f"<p>Hello,</p>"
        f"<p><strong>Customer Number:</strong> {html.escape(customer_no)}</p>"
        f"<p>{OPENING_HTML}</p>"
        f"<p>{LIST_INTRO}</p>"
        f"<ul style=\"list-style-type: disc; margin: 16px 0; padding-left: 24px;\">"
        f"{list_items}"
        f"</ul>"
        f"<p>{CLOSING}</p>"
        f"<p>{THANKS}</p>"
        f"<p>Best regards,<br><strong>{SIGNATURE}</strong></p>"
        f"</div>"
    )
    return subject, text, html_body


def run(db, wait_days: int = 7, recipients=(), cc=(), limit: int = 200
        ) -> tuple[bool, str]:
    """Suresi dolmus POD'lar icin GLS'e mail atar; gonderilen parca sayisini doner.

    Ayni parca IKINCI KEZ sorulmaz (`pod_chased_at`): gunluk calisan bir isin
    her sabah ayni 40 numarayi yeniden yollamasi GLS tarafinda spam olur.
    Isaretleme yalnizca gonderim BASARILIYSA yapilir — aksi halde bir Graph
    hatasi parcalari sessizce yutardi.
    """
    to = [a.strip() for a in recipients if a and a.strip()]
    if not to:
        return False, "GLS adresi tanımlı değil"
    parcels = db.pod_missing_since(wait_days=wait_days, limit=limit)
    if not parcels:
        return True, "sorulacak parça yok"

    subject, _text, html_body = draft(parcels)
    ok, detail = mail.send(to, subject, html_body, cc=[a.strip() for a in cc if a.strip()])
    if ok:
        db.mark_pod_chased([p["tracking_no"] for p in parcels])
    log.info("GLS POD sorgusu: %d parça, %d alıcı — %s",
             len(parcels), len(to), "gönderildi" if ok else detail)
    return ok, f"{len(parcels)} parça — {detail}"
