# -*- coding: utf-8 -*-
"""Excel takip listesi ve adres defteri ice aktaricilarinin testleri.

Buradaki senaryolar kullanicinin gercek dosyalarindan (SP26/FA26/SP27 TRACKING
LIST.xlsx ve GLS portalinin addresses-export.json'u) cikan bozukluklara karsi
yazildi — hepsi bir kez uretimde yanlis veri urettigi icin var.
"""
import json
from datetime import date, datetime

import pandas as pd
import pytest

from address_book.importers import import_gls_xlsx, import_json
from tracking.db import ShipmentsDB
from web.importers.tracking_list import (
    _fmt_date, detect_channel, detect_status, sheet_status_hint,
    import_all_sheets, import_tracking_list, list_sheets,
)


# ======================================================================
# Kanal tespiti
# ======================================================================
def test_fedex_is_recognized_from_the_agency_text():
    """Kullanici karari (2026-08-31): FedEx ulke/uzunluk tahminiyle
    KARISTIRILMAZ — yalnizca acikca "fedex" yazan ajans metninden taninir."""
    assert detect_channel("FedEx", "CA - Canada", "876365567966") == "FEDEX"
    assert detect_channel("Fedex", "IE - Ireland", "876307060255") == "FEDEX"


def test_a_canadian_shipment_without_an_agency_still_falls_back_to_the_old_guess():
    """Ajans metni yoksa (eski davranis KORUNUR) — FedEx'e uydurma dusulmez."""
    assert detect_channel("", "CA - Canada", "876365567966") == "NL"


def test_gls_netherlands_and_ireland_still_work():
    assert detect_channel("Gls Netherlands", "FR - France", "38120177012293") == "NL"
    assert detect_channel("Gls Ireland", "IE - Ireland", "21569979617") == "IE"


# ======================================================================
# Excel tarih cozumleme
# ======================================================================
@pytest.mark.parametrize("raw, expected", [
    # Excel seri numarasi — hucre bicimi kaybolunca sayi olarak gelir
    (46026, "2026-01-04"),
    ("46026", "2026-01-04"),
    # ABD duzeni metin (EMC dosyalarinin varsayilani): 3.30.2026 = 30 Mart
    ("3.30.2026", "2026-03-30"),
    ("03/30/2026", "2026-03-30"),
    # Ayin 30'u ABD duzeninde ay olamaz -> TR duzenine dusulur
    ("30.03.2026", "2026-03-30"),
    ("2026-03-30", "2026-03-30"),
    # Gercek datetime/date hucreleri
    (datetime(2026, 3, 30, 14, 5), "2026-03-30"),
    (date(2026, 3, 30), "2026-03-30"),
    (pd.Timestamp("2026-03-30"), "2026-03-30"),
])
def test_fmt_date_parses_every_real_world_shape(raw, expected):
    assert _fmt_date(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "   ", "nan", "NaT",
    "Return to Sender",                       # serbest metin
    "A call request was opened on March 2",   # serbest metin
    12,                                       # koli adedi — tarih degil
    999999,                                   # makul araligin disinda
])
def test_fmt_date_rejects_non_dates(raw):
    """Tarih olmayan hucre bos donmeli; aksi halde detect_status 'teslim edildi' sanir."""
    assert _fmt_date(raw) == ""


def test_free_text_in_date_column_is_not_treated_as_delivered():
    assert detect_status("", _fmt_date("Return to Sender")) != "delivered"
    assert detect_status("", _fmt_date("3.30.2026")) == "delivered"


# ======================================================================
# Sayfa adindan statu ipucu
# ======================================================================
@pytest.mark.parametrize("sheet, expected", [
    ("Problematic Packages", "exception"),
    ("Return to Sender", "returned"),
    ("TESLİMAT KANITI ALINACAKLAR", "delivered"),
    ("Sheet1", None),
])
def test_sheet_status_hint(sheet, expected):
    assert sheet_status_hint(sheet) == expected


# ======================================================================
# Excel ice aktarma (tek sayfa / tum sayfalar)
# ======================================================================
COLUMNS = ["TRACKING NUMBER", "STORE CODE", "INVOICE NUMBER", "CONSIGNEE NAME",
           "COUNTRY", "SHIPMENT DATE", "DELIVERED DATE", "EXPLAIN"]


def _workbook(tmp_path):
    path = tmp_path / "tracking.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame([
            ["21569761233", "ST-1", "INV-1", "Alici A", "Ireland", 46026, "3.30.2026", ""],
            ["21569761234", "ST-2", "INV-2", "Alici B", "Ireland", 46026, "", "In transit"],
        ], columns=COLUMNS).to_excel(writer, sheet_name="TESLİMAT KANITI ALINACAKLAR",
                                     index=False)
        pd.DataFrame([
            ["21569761246", "ST-3", "INV-3", "Alici C", "Ireland", 46026, "", ""],
        ], columns=COLUMNS).to_excel(writer, sheet_name="Problematic Packages", index=False)
        pd.DataFrame([
            ["35000001406746", "ST-4", "INV-4", "Alici D", "Netherlands", 46026,
             "Return to Sender", ""],
        ], columns=COLUMNS).to_excel(writer, sheet_name="Return to Sender", index=False)
    return path


@pytest.fixture
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "import.db")


def test_list_sheets(tmp_path):
    assert list_sheets(_workbook(tmp_path)) == [
        "TESLİMAT KANITI ALINACAKLAR", "Problematic Packages", "Return to Sender",
    ]


def test_import_single_sheet(tmp_path, db):
    result = import_tracking_list(_workbook(tmp_path), db, sheet=0)
    assert result["imported"] == 2
    row = db.get("21569761233")
    assert row["status"] == "delivered"
    assert row["shipment_date"] == "2026-01-04"     # seri numara cozuldu
    assert row["delivered_date"] == "2026-03-30"    # ABD duzeni metin cozuldu


def test_import_all_sheets_marks_problem_sheets(tmp_path, db):
    result = import_all_sheets(_workbook(tmp_path), db)
    assert result["imported"] == 4

    assert db.get("21569761246")["status"] == "exception"
    assert db.get("21569761246")["source_sheet"] == "Problematic Packages"

    # "Return to Sender" hucresi tarih degil -> iade statuleri
    rts = db.get("35000001406746")
    assert rts["status"] == "returned"
    assert rts["delivered_date"] == ""


def test_import_is_idempotent(tmp_path, db):
    path = _workbook(tmp_path)
    import_all_sheets(path, db)
    total = db.total()
    import_all_sheets(path, db)
    assert db.total() == total, "ayni dosya iki kez aktarilinca kayit cogalmamali"


# ======================================================================
# Adres defteri JSON (GLS portali disa aktarimi)
# ======================================================================
def test_import_json_maps_camel_case_and_address_type(tmp_path):
    path = tmp_path / "addresses-export.json"
    path.write_text(json.dumps([
        {"name": "Dolcezza BV", "street": "Damrak", "houseNumber": "12",
         "postalCode": "1012AB", "city": "Amsterdam", "countryCode": "NL",
         "type": 1, "email": "info@example.com", "tags": ["musteri", "nl"]},
        {"name": "Jan Jansen", "street": "Kalverstraat", "houseNo": "3",
         "zipCode": "1012NZ", "city": "Amsterdam", "country": "NL", "type": 2},
        {"street": "isimsiz"},   # ad/firma yok -> atlanir
    ], ensure_ascii=False), encoding="utf-8")

    records = import_json(str(path))
    assert len(records) == 2

    first = records[0]
    assert first["house_number"] == "12"     # houseNumber -> house_number
    assert first["postal_code"] == "1012AB"  # postalCode  -> postal_code
    assert first["country"] == "NL"          # countryCode -> country
    assert first["address_type"] == "business"   # 1 -> business
    assert first["tags"] == "musteri, nl"        # liste -> virgullu metin

    assert records[1]["address_type"] == "private"   # 2 -> private
    assert records[1]["house_number"] == "3"         # houseNo takma adi
    assert records[1]["postal_code"] == "1012NZ"     # zipCode takma adi


def test_import_json_accepts_wrapped_body(tmp_path):
    path = tmp_path / "wrapped.json"
    path.write_text(json.dumps({"addresses": [{"company": "EMC Logistic"}]}), encoding="utf-8")
    records = import_json(str(path))
    assert records[0]["name"] == "EMC Logistic"   # ad yoksa firma adina duser


# ======================================================================
# Adres defteri XLSX (GLS portali addresses-export.xlsx)
# ======================================================================
def test_import_gls_xlsx_maps_columns_and_dutch_type(tmp_path):
    path = tmp_path / "addresses-export.xlsx"
    pd.DataFrame([
        # Company = magaza kodu -> name2'ye tasinir; Type Hollandaca; posta/tel/mail dolu
        {"Type": "Zakelijk", "Name": "ASK & EMBLA", "Company": "NNFES1N",
         "Street": "Avenida Del Albir", "House Number": "38A",
         "Postal Code": "03581", "City": "EL ALBIR", "Country": "ES",
         "Phone": "34966446765", "Email": "rkvam@outlook.com", "Notes": ""},
        # House Number == Postal Code -> temizlenir; Particulier -> private
        {"Type": "Particulier", "Name": "Jan Jansen", "Company": "",
         "Street": "Kalverstraat", "House Number": "1012", "Postal Code": "1012",
         "City": "Amsterdam", "Country": "NL", "Phone": "", "Email": "", "Notes": ""},
    ]).to_excel(path, sheet_name="Addresses", index=False)

    records = import_gls_xlsx(str(path))
    assert len(records) == 2

    first = records[0]
    assert first["name"] == "ASK & EMBLA"
    assert first["name2"] == "NNFES1N"           # Company magaza kodu -> name2
    assert first["company"] == ""                # kod name2'ye tasindi
    assert first["postal_code"] == "03581"
    assert first["house_number"] == "38A"
    assert first["phone"] == "34966446765"
    assert first["email"] == "rkvam@outlook.com"
    assert first["address_type"] == "business"   # Zakelijk -> business

    second = records[1]
    assert second["address_type"] == "private"   # Particulier -> private
    assert second["house_number"] == ""          # posta koduyla ayni -> temizlendi
