"""EMC tracking-list Excel dosyalarini shipments DB'ye aktarir.

Desteklenen dosya formatlari (gercek kullanici dosyalari):
- SP26 TRACKING LIST — sutunlar: PARCEL NUMBER, SHIPMENT AGENCY , STORE CODE ,
  ZIP CODE , COUNTRY, INVOICE NUMBER  , EMC INVOICES, DELIVERED DATE, EXPLAIN...
- FA26 TRACKING LIST — SP26 ile ayni ana kolonlar (daha az sutun).
- SP27 SAMPLE   — sutunlar: Tracking Number, Shipment Agency, Zip Code, Country,
  Sales Rep, Explain, Delivered Date, Shipment Method.

Ortak nokta: sutun adlari birbirinden farkli, bosluk/buyuk-kucuk harf tutarsizligi
var. Bu modul TUM varyantlari 'normalized' bir sozluge indirger.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

import i18n

from tracking.db import ShipmentsDB


# ---------------------------------------------------------------------------
# Kolon adi normalize edici
# ---------------------------------------------------------------------------

# Standart anahtarlar -> muhtemel varyant listesi (hepsi lowercased ve strip'li karsilasitirilir).
COLUMN_ALIASES: dict[str, list[str]] = {
    "tracking_no":     ["parcel number", "tracking number", "tracking no", "trackid", "track id"],
    "agency":          ["shipment agency", "agency", "carrier"],
    "store_code":      ["store code", "store"],
    "zip_code":        ["zip code", "postal code", "postcode", "zip"],
    "country":         ["country"],
    "invoice_number":  ["invoice number", "invoice no", "invoice"],
    "emc_invoices":    ["emc invoices"],
    "sales_rep":       ["sales rep", "salesrep", "sales representative"],
    "shipment_date":   ["shipment date from turkey", "shipment date", "ship date"],
    "arrival_date":    ["ireland arrival date", "arrival date"],
    "delivered_date":  ["delivered date", "delivery date"],
    "explain":         ["explain", "status note", "note"],
    "shipment_method": ["shipment method", "method", "mode"],
}


def _normalize_key(col: str) -> str:
    return " ".join(str(col).strip().lower().split())


def build_column_map(columns: list[str]) -> dict[str, str]:
    """Excel'deki gercek sutun adi -> standart anahtar eslesmesi."""
    reverse: dict[str, str] = {}
    for standard, variants in COLUMN_ALIASES.items():
        for v in variants:
            reverse[_normalize_key(v)] = standard

    mapping: dict[str, str] = {}
    for col in columns:
        key = _normalize_key(col)
        if key in reverse:
            mapping[col] = reverse[key]
    return mapping


# ---------------------------------------------------------------------------
# Kanal / statu tespiti
# ---------------------------------------------------------------------------

def detect_channel(agency: str, country: str, tracking_no: str) -> str:
    """'NL', 'IE' veya 'FEDEX' dondurur.

    Onceligi shipment_agency alanina verir; olmazsa ulke; en son takip no uzunlugu.
    FedEx (Kanada/ara sira Irlanda, kullanici karari 2026-08) ulke/uzunluk
    tahminiyle KARISTIRILMAZ — yalnizca acikca "fedex" yazan ajans metninden
    taninir, aksi halde eski NL/IE tahminine dusulur.
    """
    a = (agency or "").lower().strip()
    if "fedex" in a:
        return "FEDEX"
    if "netherlands" in a or a in {"gls nl", "nl"}:
        return "NL"
    if "ireland" in a or a in {"gls ie", "ie"}:
        return "IE"

    c = (country or "").lower().strip()
    if c in {"ireland", "united kingdom", "uk", "great britain", "gb", "ie"}:
        return "IE"
    if c:
        # Kalanlar NL kanalindan Avrupa'ya dagilir (Austria, Germany, Netherlands, Spain...)
        return "NL"

    # Son care: takip numarasi uzunlugu (ShipIT 11 hane, NL 14 hane)
    tn = str(tracking_no or "").strip()
    if len(tn) >= 13:
        return "NL"
    return "IE"


# Sayfa adi tek basina statu bilgisi tasir (kullanicinin dosyalari boyle
# duzenlenmis): sorunlu ve iade sayfalarindaki her kayit sorunludur.
SHEET_STATUS_HINTS = {
    "problematic": "exception",
    "return to sender": "returned",
    "iade": "returned",
    "teslimat kaniti alinacaklar": "delivered",
}


def sheet_status_hint(sheet_name: str) -> str | None:
    name = (sheet_name or "").strip().lower().replace("i̇", "i").replace("İ", "i")
    for key, status in SHEET_STATUS_HINTS.items():
        if key in name:
            return status
    return None


def detect_status(explain: Any, delivered_date: Any, sheet_hint: str | None = None) -> str:
    """EMC dosyasindaki 'EXPLAIN' + 'DELIVERED DATE' alanlarindan statu tahmini.

    `delivered_date` COZUMLENMIS tarih olmali (bkz. _fmt_date): ham hucrede
    "Return to Sender" gibi tarih olmayan metinler var ve bunlar teslim edilmis
    sanilmamali.
    """
    e = str(explain or "").strip().lower()
    d_present = bool(str(delivered_date or "").strip())

    if "return" in e or "iade" in e:
        return "returned"
    if "problem" in e or "exception" in e or "hold" in e or "damaged" in e:
        return "exception"
    if not e and sheet_hint and not d_present:
        return sheet_hint

    if "deliver" in e or d_present:
        return "delivered"
    if "out for delivery" in e or "out-for-delivery" in e or "distribution" in e:
        return "out_for_delivery"
    if "transit" in e or "shipped" in e or "in transit" in e or "eta " in e or "in tra" in e:
        return "in_transit"
    return "in_transit"  # varsayilan: yolda


# ---------------------------------------------------------------------------
# Tekil satir donusumu
# ---------------------------------------------------------------------------

def _s(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "nat", "none"} else s


# Excel'in seri tarih baslangici: 1 = 1900-01-01 ama Excel 1900'u artik yil
# sandigi icin gercek referans 1899-12-30'dur.
_EXCEL_EPOCH = datetime(1899, 12, 30)
# Makul aralik: 1990-01-01 .. 2079-12-31. Disindaki sayilar tarih degildir
# (adet, tutar, referans no vb.).
_EXCEL_SERIAL_MIN, _EXCEL_SERIAL_MAX = 32874, 65380

# Denenecek metin bicimleri. EMC dosyalari ABD duzenini kullanir
# ("3.30.2026" = 30 Mart 2026); elle girilen satirlarda TR duzeni de cikiyor.
_DATE_FORMATS = ("%m.%d.%Y", "%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
                 "%Y/%m/%d", "%d-%m-%Y", "%m-%d-%Y")


def _fmt_date(v: Any) -> str:
    """Excel tarih hucresini 'YYYY-MM-DD' formatina indirger.

    Gercek EMC dosyalarinda ayni sutunda uc tur bir arada bulunuyor:
      * datetime hucresi
      * Excel seri numarasi (ornek: 46026)
      * metin (ornek: "3.30.2026")

    Ayrica tarih olmayan serbest metinler de var ("Return to Sender", "A call
    request was opened on March 2"); bunlar bos string'e dusurulur — aksi halde
    detect_status onlari "teslim edildi" sanir.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""

    if isinstance(v, (pd.Timestamp, datetime)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()

    text = _s(v)
    if not text:
        return ""

    serial = None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        serial = int(v)
    elif text.isdigit():
        serial = int(text)
    if serial is not None:
        if _EXCEL_SERIAL_MIN <= serial <= _EXCEL_SERIAL_MAX:
            return (_EXCEL_EPOCH + timedelta(days=serial)).strftime("%Y-%m-%d")
        return ""

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def estimate_freight(weight_kg: float | None, channel: str, country: str) -> float | None:
    """GLS tarife yaklasik hesaplama (mock/estimate — gercek fatura ile uyusmayabilir)."""
    if not weight_kg or weight_kg <= 0:
        return None
    base_nl = 8.50  # BusinessParcel NL yerel
    base_ie = 9.90  # BusinessParcel IE yerel
    base = base_ie if channel == "IE" else base_nl
    # 2 kg uzerinde her ek kg + 1.2 EUR
    extra = max(0.0, weight_kg - 2.0) * 1.20
    # Yurt disi (TR -> Avrupa) icin +12 EUR
    intl = 12.0 if country and country.upper() not in {"IE", "IRELAND", "NL", "NETHERLANDS"} else 0.0
    return round(base + extra + intl, 2)


def row_to_parcel(row: dict, colmap: dict[str, str], sheet_name: str = "") -> dict | None:
    """Excel satirini shipments schema'sina donustur; gecerli degilse None."""
    def val(std: str) -> Any:
        for orig, s in colmap.items():
            if s == std:
                return row.get(orig)
        return None

    raw_tn = val("tracking_no")
    if raw_tn is None or (isinstance(raw_tn, float) and pd.isna(raw_tn)):
        return None

    if isinstance(raw_tn, float):
        tn = f"{int(raw_tn)}"
    else:
        tn = str(raw_tn).strip()
        if tn.endswith(".0"):
            tn = tn[:-2]

    if not tn or tn.lower() in {"nan", "none"}:
        return None

    # Guvenlik: takip numarasi sadece alfanumerik olmali (gercek GLS takip
    # numaralari her zaman boyle — bkz. gls_api/schemas.py). Kullanicidan gelen
    # Excel dosyasindaki bu deger sonradan HTML/JS'e (POD sayfasi) gomuluyor;
    # kirli bir deger XSS'e yol acmasin diye burada, giris noktasinda temizliyoruz.
    tn = re.sub(r"[^A-Za-z0-9]", "", tn)
    if not tn:
        return None

    agency = _s(val("agency"))
    country = _s(val("country"))
    channel = detect_channel(agency, country, tn)

    invoice_number = _s(val("invoice_number"))
    emc_invoices = _s(val("emc_invoices"))
    store_code = _s(val("store_code"))
    zip_code = _s(val("zip_code"))
    sales_rep = _s(val("sales_rep"))
    shipment_method = _s(val("shipment_method"))
    shipment_date = _fmt_date(val("shipment_date"))
    delivered_date = _fmt_date(val("delivered_date"))

    ref_parts = [invoice_number, store_code]
    reference = " / ".join([p for p in ref_parts if p])

    consignee = store_code or sales_rep

    explain = _s(val("explain"))
    status = detect_status(explain, delivered_date, sheet_status_hint(sheet_name))

    # Excel'de weight/freight sutunu yok — tahmin (ortalama 3 kg/paket)
    weight_kg = 3.0
    freight_cost = estimate_freight(weight_kg, channel, country)

    return {
        "explain": explain,
        "source_sheet": _s(sheet_name),
        "tracking_no": tn,
        "channel": channel,
        "country": country,
        "reference": reference,
        "consignee_name": consignee,
        "status": status,
        "invoice_number": invoice_number,
        "emc_invoices": emc_invoices,
        "store_code": store_code,
        "zip_code": zip_code,
        "sales_rep": sales_rep,
        "shipment_method": shipment_method,
        "shipment_date": shipment_date,
        "delivered_date": delivered_date,
        "weight_kg": weight_kg,
        "freight_cost": freight_cost,
    }


# ---------------------------------------------------------------------------
# Ana giris noktasi
# ---------------------------------------------------------------------------

def import_tracking_list(
    file_path: str | Path,
    db: ShipmentsDB,
    sheet: str | int = 0,
) -> dict:
    """Tek bir Excel sheet'ini DB'ye aktarir.

    Donen sozluk: {"total": N, "imported": M, "skipped": K, "errors": [...]}.
    """
    path = Path(file_path)
    df = pd.read_excel(path, sheet_name=sheet, dtype=object)
    sheet_name = sheet if isinstance(sheet, str) else list_sheets(path)[sheet]
    colmap = build_column_map(df.columns.tolist())

    required = {"tracking_no"}
    have = set(colmap.values())
    if not required.issubset(have):
        return {
            "total": len(df),
            "imported": 0,
            "skipped": len(df),
            "errors": [
                f"Gerekli sutun bulunamadi: 'PARCEL NUMBER' veya 'Tracking Number'. "
                f"Bulunan sutunlar: {list(df.columns)}"
            ],
        }

    hint = sheet_status_hint(sheet_name)
    imported = 0
    skipped = 0
    errors: list[str] = []
    seen: set[str] = set()

    for idx, row in df.iterrows():
        try:
            parcel = row_to_parcel(row.to_dict(), colmap, sheet_name)
            if not parcel:
                skipped += 1
                continue
            if parcel["tracking_no"] in seen:
                skipped += 1
                continue
            seen.add(parcel["tracking_no"])
            db.add_parcel(**parcel)
            # "Problematic Packages" / "Return to Sender" sayfalari asli
            # kaynaktir: parca ana listede "teslim edildi" gorunse bile bu
            # sayfada yer aliyorsa sorunludur. add_parcel mevcut statuye
            # dokunmadigi icin burada acikca ustune yaziyoruz.
            if hint == "exception":
                db.update_status(parcel["tracking_no"], "exception",
                                 note=parcel["explain"] or sheet_name)
                db.update_tracking_info(parcel["tracking_no"],
                                        problem_reason=parcel["explain"] or sheet_name)
            imported += 1
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(i18n.t("imp.row", row=idx + 2, detail=exc))

    return {
        "total": len(df),
        "imported": imported,
        "skipped": skipped,
        "errors": errors[:20],  # ilk 20 hatayi ilet
        "column_map": colmap,
        "sheet": sheet_name,
    }


def import_all_sheets(file_path: str | Path, db: ShipmentsDB) -> dict:
    """Dosyadaki TUM sayfalari sirayla aktarir.

    Kullanicinin dosyalarinda ana liste disinda "TESLIMAT KANITI ALINACAKLAR",
    "Problematic Packages", "Return to Sender" gibi sayfalar var; her kayit
    hangi sayfadan geldigiyle (`source_sheet`) birlikte saklanir. Takip numarasi
    sutunu olmayan sayfalar (ornek: fatura ozeti) sessizce atlanir.
    """
    path = Path(file_path)
    sheets, totals = [], {"total": 0, "imported": 0, "skipped": 0}
    errors: list[str] = []

    for name in list_sheets(path):
        result = import_tracking_list(path, db, sheet=name)
        sheets.append({"sheet": name, **{k: result[k] for k in ("total", "imported", "skipped")}})
        for key in totals:
            totals[key] += result[key]
        errors += [f"{name} / {e}" for e in result["errors"]]

    return {**totals, "errors": errors[:20], "sheets": sheets}


def list_sheets(file_path: str | Path) -> list[str]:
    return pd.ExcelFile(Path(file_path)).sheet_names
