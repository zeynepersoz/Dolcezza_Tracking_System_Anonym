# -*- coding: utf-8 -*-
"""POD görüntüsünü insan-okur bir teslimat belgesine sarar.

GLS'in kendi POD belgesi (logo + gönderen/alıcı + teslim tarihi/saati + teslim
alan + paket tablosu + fotoğraf) yalnızca **e-postayla** gönderilir; portalda
indirme düğmesi yoktur ve indirilebilir bir uç noktası da bulunamadı (denenen
tüm `/pod/pdf`, `/pod/download`, `/proofofdelivery` yolları 404). Bu yüzden
aynı bilgilerle belgeyi kendimiz üretiyoruz.

İki çıktı vardır — ikisi de aynı veriden, farklı kitleye:

    customer_document()  müşteriye/alıcıya gönderilecek sade belge
    internal_document()  arşiv/muhasebe için; fatura no, mağaza kodu, ölçüler

BELGE DİLİ İNGİLİZCEDİR (etiketler, tarih/ağırlık biçimi). Alıcılar Avrupa'daki
mağazalar ve ajanslar; GLS'in kendi POD belgesi de İngilizce. Kod yorumları ve
panel arayüzü Türkçe kalır — burada dil seçimi belgenin kitlesine göredir.

Veri iki kaynaktan birleştirilir:
    GLS  (gls_api.nl_track_client.delivery_details) -> alıcı ünvanı, açık adres,
         ürün, servis, teslim alan, ölçülen ağırlık, ölçüler
    yerel DB satırı -> fatura no, mağaza kodu, sevkiyat yöntemi, kanal

GLS verisi yoksa (oturum açılamadı, ShipIT/T&T kanalı) belge yine üretilir;
yalnızca o satırlar boş kalır. POD görüntüsü esas çıktıdır, bilgiler ek.

FONT: fpdf2'nin gömülü fontları Latin-1'dir; "ş/ğ/İ" basılamaz. Bu yüzden
sistemden Unicode bir TTF aranır (Docker'da fonts-dejavu-core, macOS'ta
Verdana). Hiçbiri yoksa belge üretilmez — bozuk karakterli belge basmaktansa
çağıranın ham görüntüye düşmesi doğrudur (bkz. web/routers/pod.py).
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from fpdf import FPDF

import timez

# (duz, kalin) ciftleri — ilk bulunan kullanilir. Kalin yoksa duz'u kullanilir.
FONT_CANDIDATES = (
    # Docker imaji: fonts-dejavu-core (bkz. Dockerfile)
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    # macOS gelistirme makinesi
    ("/System/Library/Fonts/Supplemental/Verdana.ttf",
     "/System/Library/Fonts/Supplemental/Verdana Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", ""),
)

PAGE_WIDTH = 210.0        # A4 dikey, mm
MARGIN = 18.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
LABEL_WIDTH = 48.0

# Iki sutunlu blok (GLS'in kendi belgesindeki duzen): solda gonderi/teslimat
# alanlari, sagda alici adresi.
COLUMN_GAP = 6.0
COLUMN_WIDTH = (CONTENT_WIDTH - COLUMN_GAP) / 2
COLUMN_LABEL_WIDTH = 34.0

# POD gorüntüsü icin ust sinirlar. GLS teslimat fotografi 200x100, imza ise
# 1591x562 gelir; ayni kutuya sigdirilinca ikisi de okunur boyutta kalir.
MAX_IMAGE_WIDTH = 110.0
MAX_IMAGE_HEIGHT = 75.0

INK = (30, 41, 59)        # slate-800
MUTED = (100, 116, 139)   # slate-500
RULE = (203, 213, 225)    # slate-300


class PodDocumentUnavailable(RuntimeError):
    """Unicode font bulunamadı — belge üretilemez."""


def font_pair() -> tuple[str, str]:
    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists():
            return regular, (bold if bold and Path(bold).exists() else regular)
    raise PodDocumentUnavailable(
        "POD belgesi icin Unicode TTF bulunamadi; aranan yollar: "
        + ", ".join(r for r, _ in FONT_CANDIDATES))


# ----------------------------------------------------------------------
def _text(value) -> str:
    """Bos/None/"nan" degerleri tek bir bosluga indirger."""
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in ("", "nan", "none", "-") else text


def _date_time(value) -> tuple[str, str]:
    """ISO damgasini ("20-07-2026", "18:35") ciftine cevirir — Europe/Istanbul.

    Belgeyi okuyan kullanici bizim saatimizle calisiyor; GLS'in UTC damgasi
    oldugu gibi basilirsa POD 3 saat geride gorunur (bkz. `timez`).
    """
    text = _text(value)
    if not text:
        return "", ""
    stamp = timez.local(text)
    if stamp is None:
        return text, ""
    return stamp.strftime("%d-%m-%Y"), stamp.strftime("%H:%M")


def _weight(value) -> str:
    """5.2 -> "5.2 kg". Belge Ingilizce oldugu icin ondalik ayraci NOKTA."""
    try:
        return f"{float(value):.1f} kg"
    except (TypeError, ValueError):
        return ""


def _address_lines(address: dict) -> list[str]:
    """GLS adres nesnesini yazdirilabilir satirlara cevirir.

    `name3` GLS'te MAGAZA KODUdur (ornek "ZBAES5N") — unvanla ayni satira
    konmaz, sondaki parantezde gosterilir.
    """
    street = " ".join(filter(None, [_text(address.get("street")),
                                    _text(address.get("houseNo")),
                                    _text(address.get("houseNoAdd"))]))
    city = " ".join(filter(None, [_text(address.get("zipcode")),
                                  _text(address.get("city"))]))
    lines = [_text(address.get("name")), _text(address.get("name2")),
             street, city, _text(address.get("country"))]
    return [line for line in lines if line]


def facts(parcel: dict, info: dict | None = None) -> dict:
    """DB satiri + GLS teslimat bilgilerini tek sozluge indirger.

    GLS alanlari onceliklidir: DB'deki `consignee_name` cogu kayitta magaza
    kodudur ("ZBAES5N"), GLS ise gercek unvani verir ("Monsierra Gestion, S.L").
    """
    info = info or {}
    recipient = (info.get("addressInfo") or {}).get("recipient") or {}
    scan = info.get("deliveryScanInfo") or {}

    delivered_date, delivered_time = _date_time(
        scan.get("dateTime") or parcel.get("delivered_date"))

    return {
        "tracking_no": _text(parcel.get("tracking_no")),
        "unique_no": _text(info.get("uniqueNo")),
        "customer": _text(info.get("custName")),
        "reference": _text(info.get("reference")) or _text(parcel.get("reference")),
        "product": _text(info.get("product")),
        "services": _text(info.get("services")),
        "recipient": _address_lines(recipient) or [
            line for line in (_text(parcel.get("consignee_name")),
                              _text(parcel.get("zip_code")),
                              _text(parcel.get("country"))) if line],
        "store_code": _text(recipient.get("name3")) or _text(parcel.get("store_code")),
        "delivered_date": delivered_date,
        "delivered_time": delivered_time,
        "signed_by": _text(scan.get("signedBy")),
        "delivery_note": _text(parcel.get("issue_text")),
        "weight": _weight(info.get("suppliedWeight") or parcel.get("weight_kg")),
        "weighed": _weight(info.get("weighedWeight")),
        "dimensions": " x ".join(
            str(int(info[k])) for k in ("length", "width", "height")
            if isinstance(info.get(k), (int, float))),
        "channel": _text(parcel.get("channel")),
        "invoice_number": _text(parcel.get("invoice_number")),
        "shipment_method": _text(parcel.get("shipment_method")),
        "parcels": [p for p in (info.get("parcels") or []) if isinstance(p, dict)],
    }


# ----------------------------------------------------------------------
class _Doc(FPDF):
    """Sayfa duzeni yardimcilari — fpdf2 uzerine ince bir katman.

    Yardimci adlari FPDF'inkilerle CAKISMAMALI: `title` FPDF'te bir metadata
    OZELLIGIDIR (None'a esitlenir ve ayni adli metodu golgeler), `table` ise
    fpdf2'nin kendi baglam yoneticisidir. Bu yuzden `heading` / `grid` adlari.
    """

    def __init__(self):
        super().__init__(format="A4")
        regular, bold = font_pair()
        self.add_font("body", "", regular)
        self.add_font("body", "B", bold)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(True, margin=MARGIN)
        self.add_page()

    def heading(self, text: str, subtitle: str = ""):
        self.set_font("body", "B", 16)
        self.set_text_color(*INK)
        self.cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
        if subtitle:
            self.set_font("body", "", 9)
            self.set_text_color(*MUTED)
            self.cell(0, 5, subtitle, new_x="LMARGIN", new_y="NEXT")
        self.ln(4)

    def section(self, text: str):
        self.ln(3)
        self.set_font("body", "B", 10)
        self.set_text_color(*INK)
        self.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*RULE)
        self.line(MARGIN, self.get_y(), PAGE_WIDTH - MARGIN, self.get_y())
        self.ln(2)

    def row(self, label: str, value):
        """Etiket + deger. Deger liste ise cok satirli blok olarak yazilir.

        Bos degerler ATLANIR — "Teslim alan: —" gibi anlamsiz satirlar yerine
        belgede hic gorunmemesi daha temiz.
        """
        lines = [v for v in (value if isinstance(value, list) else [value]) if _text(v)]
        if not lines:
            return
        self.set_font("body", "", 9.5)
        for i, line in enumerate(lines):
            self.set_text_color(*MUTED)
            self.cell(LABEL_WIDTH, 5.5, label if i == 0 else "")
            self.set_text_color(*INK)
            self.cell(0, 5.5, str(line), new_x="LMARGIN", new_y="NEXT")

    def columns(self, left: list[tuple[str, object]], right: list[str],
                right_label: str = ""):
        """Solda etiket/deger ciftleri, sagda serbest satir blogu (alici adresi).

        GLS'in kendi POD belgesi bu iki sutunlu duzeni kullanir; alanlarin ayni
        yerde durmasi belgeyi GLS'inkiyle karsilastirmayi kolaylastirir.
        """
        rows: list[tuple[str, str]] = []
        for label, value in left:
            lines = [v for v in (value if isinstance(value, list) else [value]) if _text(v)]
            rows += [(label if i == 0 else "", str(v)) for i, v in enumerate(lines)]
        rights = [r for r in right if _text(r)]

        self.set_font("body", "", 9.5)
        for i in range(max(len(rows), len(rights))):
            y = self.get_y()
            if i < len(rows):
                self.set_xy(MARGIN, y)
                self.set_text_color(*MUTED)
                self.cell(COLUMN_LABEL_WIDTH, 5.5, rows[i][0])
                self.set_text_color(*INK)
                self.cell(COLUMN_WIDTH - COLUMN_LABEL_WIDTH, 5.5, rows[i][1])
            if i < len(rights):
                self.set_xy(MARGIN + COLUMN_WIDTH + COLUMN_GAP, y)
                if right_label:
                    self.set_text_color(*MUTED)
                    self.cell(COLUMN_LABEL_WIDTH, 5.5, right_label if i == 0 else "")
                self.set_text_color(*INK)
                self.cell(0, 5.5, rights[i])
            self.set_y(y)
            self.ln(5.5)

    def grid(self, headers: list[str], rows: list[list[str]], widths: list[float]):
        self.ln(1)
        self.set_font("body", "B", 8.5)
        self.set_text_color(*MUTED)
        for head, width in zip(headers, widths):
            self.cell(width, 6, head)
        self.ln(6)
        self.set_font("body", "", 9)
        self.set_text_color(*INK)
        for row in rows:
            for cell, width in zip(row, widths):
                self.cell(width, 5.5, str(cell))
            self.ln(5.5)

    def note_box(self, title: str, text: str):
        if not text:
            return
        self.section(title)
        self.ln(1)
        self.set_font("body", "", 9.5)
        self.set_fill_color(240, 249, 255)
        self.set_draw_color(186, 230, 253)
        self.set_text_color(*INK)
        self.multi_cell(CONTENT_WIDTH, 6, f"{text}", border=1, fill=True)
        self.ln(2)

    def picture(self, data: bytes, caption: str):
        """POD gorüntüsünü sayfaya ortalar.

        fpdf2 BMP'yi dogrudan almaz; ayrica GLS'in imza PNG'si 1-bit ve saydam
        olabildigi icin Pillow ile beyaz zeminli PNG'ye normalize ediliyor.
        """
        if not data:
            return
        with Image.open(BytesIO(data)) as img:
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                canvas = Image.new("RGB", img.size, "white")
                canvas.paste(img, mask=img.split()[-1])
                img = canvas
            else:
                img = img.convert("RGB")
            width, height = img.size
            buf = BytesIO()
            img.save(buf, format="PNG")

        scale = min(MAX_IMAGE_WIDTH / width, MAX_IMAGE_HEIGHT / height)
        draw_w, draw_h = width * scale, height * scale

        # Baslik + resim AYNI sayfada kalsin (bkz. web/box_image_document.py:
        # _picture, ayni fpdf2 "flowing mode" sayfa-atlama sorunu — burada
        # MAX_IMAGE_HEIGHT kucuk oldugu icin pratikte pek tetiklenmez ama
        # kural aynidir).
        if self.get_y() + 11 + draw_h > self.page_break_trigger:
            self.add_page()

        self.section(caption)
        self.ln(2)
        curr_y = self.get_y()
        buf.seek(0)
        self.image(buf, x=(PAGE_WIDTH - draw_w) / 2, y=curr_y, w=draw_w, h=draw_h)
        self.set_y(curr_y + draw_h + 4)

    def note(self, text: str):
        self.ln(4)
        self.set_font("body", "", 7.5)
        self.set_text_color(*MUTED)
        self.multi_cell(0, 4, text)

    def render(self) -> bytes:
        return bytes(self.output())


# ----------------------------------------------------------------------
def _parcel_rows(data: dict) -> list[list[str]]:
    """Paket tablosu. GLS kardes parcalari da veriyorsa hepsi listelenir."""
    rows = [[data["tracking_no"], data["unique_no"], data["reference"], data["weight"]]]
    for sibling in data["parcels"]:
        number = _text(sibling.get("parcelNo"))
        if number and number != data["tracking_no"]:
            rows.append([number, _text(sibling.get("uniqueNo")),
                         _text(sibling.get("reference")) or data["reference"],
                         _weight(sibling.get("suppliedWeight"))])
    return rows


def customer_document(parcel: dict, image: bytes = b"", info: dict | None = None) -> bytes:
    """Müşteriye/alıcıya gönderilebilecek sade teslimat kanıtı.

    Bölüm sırası ve iki sütunlu yerleşim GLS Hollanda'nın kendi POD belgesini
    izler (Verzendgegevens / Aflevergegevens / Pakketgegevens): müşteri iki
    belgeyi yan yana koyup aynı alanı aynı yerde bulabilsin.
    """
    data = facts(parcel, info)
    doc = _Doc()
    doc.heading("PROOF OF DELIVERY",
                "The requested proof of delivery is provided below.")

    doc.section("Shipment details")
    doc.columns([("Sender", data["customer"]),
                 ("Reference", data["reference"]),
                 ("Product", data["product"]),
                 ("Service(s)", data["services"])],
                data["recipient"], "Consignee")

    doc.section("Delivery details")
    doc.columns([("Delivery date", data["delivered_date"]),
                 ("Delivery time", data["delivered_time"]),
                 ("Signed by", data["signed_by"] or (data["delivery_note"] if not image else ""))],
                data["recipient"])

    doc.section("Parcel details")
    doc.grid(["Parcel number", "Track ID", "Reference", "Weight"],
              _parcel_rows(data), [45, 30, 60, 25])

    if data["delivery_note"]:
        doc.note_box("Delivery Note / PIN Code Confirmation", data["delivery_note"])

    if image:
        doc.picture(image, "Proof of delivery")

    doc.note("This document was generated automatically from GLS tracking data. "
             + ("The image is the photo or signature captured by the GLS driver at the moment of delivery."
                if image else "The delivery confirmation was recorded based on GLS carrier notification."))
    return doc.render()


def internal_document(parcel: dict, image: bytes = b"", info: dict | None = None) -> bytes:
    """Arşiv/muhasebe için ayrıntılı belge — fatura no, mağaza kodu, ölçüler."""
    data = facts(parcel, info)
    doc = _Doc()
    doc.heading("PROOF OF DELIVERY — INTERNAL RECORD",
              f"Parcel {data['tracking_no']} · "
              f"generated {timez.now().strftime('%d-%m-%Y %H:%M')}")

    doc.section("Record")
    doc.row("Parcel number", data["tracking_no"])
    doc.row("Track ID", data["unique_no"])
    doc.row("Channel", data["channel"])
    doc.row("Invoice number", data["invoice_number"])
    doc.row("Store code", data["store_code"])
    doc.row("Shipment method", data["shipment_method"])
    doc.row("Reference", data["reference"])

    doc.section("Consignee")
    doc.row("Name / address", data["recipient"])

    doc.section("Delivery")
    doc.row("Delivered", " ".join(filter(None, [data["delivered_date"],
                                                data["delivered_time"]])))
    doc.row("Signed by", data["signed_by"])
    doc.row("Product / service", " · ".join(filter(None, [data["product"],
                                                          data["services"]])))

    doc.section("Measurements")
    doc.row("Declared weight", data["weight"])
    doc.row("Weighed weight", data["weighed"])
    doc.row("Dimensions (L x W x H)",
            f"{data['dimensions']} cm" if data["dimensions"] else "")

    if data["delivery_note"]:
        doc.note_box("Delivery / PIN Code Note", data["delivery_note"])

    if image:
        doc.picture(image, "Proof of delivery")
    return doc.render()
