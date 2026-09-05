# -*- coding: utf-8 -*-
"""FedEx kolileri icin "Koli Görseli" belgesi.

FedEx'in resmi POD saglayicisi YOK (bkz. `gls_api/providers.py:pod_provider_for`
— FEDEX icin bilincli olarak `None` doner, GLS/ShipIT'e sessizce dusulmez).
Operator bunun yerine kolinin fotografini elle yukluyor; bu modul o
fotograflari + ERP'den gelen koli icerigini TEK bir PDF'e gomer (bkz.
`web/routers/tracking.py:upload_box_images`, kullanici istegi 2026-08-31:
"box content'in altındaki butona bastığında ... koli içeriği takip numarası
vs. olacak sonra da bu yüklenen resimler pdfe gömülecek").

Belge musteriye degil KENDI ekibimize aittir (arsiv/kanit) ama FedEx grubuna
WhatsApp uzerinden de gidiyor (bkz. `notify/whatsapp.py:notify_fedex_created`)
— kullanici karari (2026-09-01, 2026-09-02): "fedexle alakali tum
bildirimlerin ingilizce olmasi lazim ... daha panelden olustururken de".
Dil bu yuzden `pod_document.py` (musteriye giden POD) ile AYNI: Ingilizce.
`_Doc` sayfa duzeni yardimcisi oradan ODUNC alinir; iki belge turu ayni
gorsel dili paylassin diye ayri bir kopyasi tutulmuyor.
"""
from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image

import timez
from web.pod_document import CONTENT_WIDTH, PAGE_WIDTH, _Doc, PodDocumentUnavailable

BoxImageDocumentUnavailable = PodDocumentUnavailable

log = logging.getLogger("box_image")

# Koli fotografi belgenin ESASIDIR (POD'daki kucuk imza gorseli degil) —
# `pod_document.MAX_IMAGE_*`den kasten daha buyuk: fotograf sayfaya yakin
# doluyor ki koli icindeki hasar/etiket gibi detaylar okunabilsin.
MAX_IMAGE_WIDTH = 170.0
MAX_IMAGE_HEIGHT = 230.0

# Belge WhatsApp'a EK olarak gidiyor (`notify/whatsapp.py:notify_fedex_created`);
# kullanici tavani 20 MB olarak verdi (2026-09-02). Canli ornek 876634105532:
# 3 fotograf, sikistirma yokken 46.8 MB — yani tavan teorik degil.
MAX_PDF_BYTES = 20 * 1024 * 1024
# Gorseller bu pikselden buyukse kucultulur (PDF boyutunu kontrol altinda tutar)
MAX_PIXEL_EDGE = 2000

# Tavanin ALTINA INENE KADAR denenen (kenar, JPEG kalitesi) basamaklari.
# Tek basina sikistirma tavani GARANTI ETMEZ: yeterince fotograf eklenirse
# 20 MB yine asilir, bu yuzden belge uretilip OLCULUR ve gerekirse daha sert
# ayarla yeniden kurulur (bkz. `build`).
_SIZE_STEPS = ((MAX_PIXEL_EDGE, 85), (1600, 75), (1200, 65), (900, 55))


def _text(value) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in ("", "nan", "none", "-") else text


def _compress_image(data: bytes, max_edge: int = MAX_PIXEL_EDGE,
                    quality: int = 85) -> tuple[bytes, int, int]:
    """Gorseli JPEG olarak sikistirir, buyukse kucultur. (baytlar, w, h) doner."""
    with Image.open(BytesIO(data)) as img:
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            canvas = Image.new("RGB", img.size, "white")
            canvas.paste(img, mask=img.split()[-1])
            img = canvas
        else:
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_edge:
            ratio = max_edge / max(w, h)
            w, h = int(w * ratio), int(h * ratio)
            img = img.resize((w, h), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), w, h


def _picture(doc: _Doc, data: bytes, caption: str,
             max_edge: int = MAX_PIXEL_EDGE, quality: int = 85) -> None:
    """Koli fotoğrafını temizce ortalar ve Y koordinatını doğru ilerletir."""
    if not data:
        return
    compressed, width, height = _compress_image(data, max_edge, quality)

    scale = min(MAX_IMAGE_WIDTH / width, MAX_IMAGE_HEIGHT / height, 1.0)
    draw_w, draw_h = width * scale, height * scale

    # Her fotoğraf temiz ve net görünmesi için ayrı sayfaya alınır
    doc.add_page()
    doc.section(caption)
    doc.ln(3)
    curr_y = doc.get_y()
    buf = BytesIO(compressed)
    doc.image(buf, x=(PAGE_WIDTH - draw_w) / 2, y=curr_y, w=draw_w, h=draw_h)
    doc.set_y(curr_y + draw_h + 4)


def _contents_widths(headers: list[str]) -> list[float]:
    """Model/Renk/Bedenler/Toplam sutunlarina CONTENT_WIDTH'i pay eder."""
    if len(headers) <= 3:
        return [CONTENT_WIDTH / len(headers)] * len(headers)
    size_cols = len(headers) - 3
    fixed = 32.0 + 32.0 + 22.0
    each = max((CONTENT_WIDTH - fixed) / size_cols, 9.0)
    return [32.0, 32.0] + [each] * size_cols + [22.0]


def build(parcel: dict, images: list[bytes],
          contents_headers: list[str] | None = None,
          contents_rows: list[list] | None = None) -> bytes:
    """(koli bilgisi + [icerik tablosu] + fotograflar) -> PDF baytlari.

    Sonuc `MAX_PDF_BYTES` tavaninin ALTINDA olacak sekilde uretilir: belge
    kurulup OLCULUR, buyukse `_SIZE_STEPS`teki daha sert (kenar, kalite)
    ayariyla yeniden kurulur. Tek basina sikistirma yetmez — tavan fotograf
    SAYISIYLA da asilir (canli ornek 876634105532: 3 fotograf, 46.8 MB).

    Basamaklarin sonunda hala buyukse elde edilen EN KUCUK belge dondurulur:
    yuklemeyi tumden reddetmek, operatorun elindeki tek kaniti kaybettirirdi.
    """
    smallest = None
    for max_edge, quality in _SIZE_STEPS:
        pdf = _build_once(parcel, images, contents_headers, contents_rows,
                          max_edge, quality)
        if len(pdf) <= MAX_PDF_BYTES:
            return pdf
        if smallest is None or len(pdf) < len(smallest):
            smallest = pdf
    log.warning("Koli gorseli PDF'i %s: %d fotografla tavanin (%d MB) altina "
                "inilemedi, en kucuk surum kullaniliyor (%.1f MB)",
                _text(parcel.get("tracking_no")), len(images),
                MAX_PDF_BYTES // (1024 * 1024), len(smallest) / (1024 * 1024))
    return smallest


def _build_once(parcel: dict, images: list[bytes],
                contents_headers: list[str] | None,
                contents_rows: list[list] | None,
                max_edge: int, quality: int) -> bytes:
    doc = _Doc()
    doc.heading("SHIPMENT DETAILS",
                f"{_text(parcel.get('tracking_no'))} · "
                f"created: {timez.now().strftime('%d-%m-%Y %H:%M')}")

    doc.section("Box Information")
    doc.row("Tracking No", parcel.get("tracking_no"))
    doc.row("Store", parcel.get("store_code"))
    doc.row("Invoice", parcel.get("invoice_number"))
    doc.row("Consignee", parcel.get("consignee_name"))
    doc.row("Country", parcel.get("country"))
    doc.row("Season", parcel.get("season"))
    doc.row("Shipment Method", parcel.get("shipment_method"))

    if contents_headers and contents_rows:
        doc.section("Box Contents")
        doc.grid(contents_headers, [[str(c) for c in row] for row in contents_rows],
                 _contents_widths(contents_headers))

    for i, image in enumerate(images, start=1):
        _picture(doc, image, f"Box Photo {i}/{len(images)}", max_edge, quality)

    doc.ln(3)
    if doc.get_y() + 25 > doc.page_break_trigger:
        doc.add_page()
    doc.note("This document was automatically generated from box photos manually "
             "uploaded for FedEx shipments. FedEx has no official POD (proof of "
             "delivery) provider like GLS/ShipIT.")
    return doc.render()
