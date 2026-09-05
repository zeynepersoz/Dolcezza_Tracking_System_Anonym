"""Etiketsiz koli sorgusu — ag baglantisi olmadan, sahte istemciyle."""
from datetime import date, datetime

from erp.boxes import available_dates, fetch_unlabeled_boxes


class Spy:
    """Son sorguyu ve parametrelerini saklar, sabit satir dondurur."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.sql = ""
        self.params = ()

    def query(self, sql, params=()):
        self.sql, self.params = sql, params
        return self.rows


def _row(**over):
    row = {
        "RecId": "1001",
        "BoxCode": "8682500093675",
        "UD_Buyer": "nunat1n",
        "UD_KA": 23.5,
        "UD_Koli": "60X42X46",
        "UD_PaketlemeTarihi": datetime(2026, 8, 14, 14, 5),
        "invoice": "5000402",
        "CustomerName": "AHA Austria",
        "CustomerCountry": "AT",
    }
    row.update(over)
    return row


def test_only_boxes_without_tracking_and_with_invoice_are_asked_for():
    """Takip numarasi olan koli zaten etiketlenmis; faturasiz koliye etiket basilamaz."""
    spy = Spy()
    fetch_unlabeled_boxes(spy, "2026-08-14")

    assert "UD_TrackingNumber IS NULL OR LTRIM(RTRIM(b.UD_TrackingNumber)) = ''" in spy.sql
    assert "LTRIM(RTRIM(b.UD_BCHInvoiceNumber)) <> ''" in spy.sql
    assert "IsDeleted IS NULL OR b.IsDeleted = 0" in spy.sql


def test_packing_date_is_a_parameter_not_sql_text():
    """Tarih bir DEGERDIR — sorgu metnine gomulmemeli."""
    spy = Spy()
    fetch_unlabeled_boxes(spy, date(2026, 8, 14))

    assert "= %s" in spy.sql
    assert spy.params == ("2026-08-14",)


def test_gb_and_ie_are_filtered_in_sql():
    """Ingiltere (GB/UK), Irlanda (IE), Rusya (RU), Avustralya (AU) ve Yeni Zelanda (NZ) toplu etiket sorgusundan elenir."""
    spy = Spy()
    fetch_unlabeled_boxes(spy, "2026-08-14")

    assert "NOT IN ('GB', 'UK', 'IE', 'IRL', 'RU', 'RUS', 'AU', 'AUS', 'NZ', 'NZL', 'TR', 'TUR')" in spy.sql
    # Kod suzgeci artik ALT DIZE aramaz, `...P` ILE BITENLERI eler: alt dize
    # arayan eski hali kodunda tesadufen "AU"/"IE" gecen GERCEK Avrupa
    # magazalarini sessizce eliyordu (PNHFR1N/FR, OLIESCN/ES — kullanici
    # bildirdi 2026-09-01).
    assert "UD_Buyer NOT LIKE '%P'" in spy.sql
    assert "UD_Buyer NOT LIKE '%P[-]%'" in spy.sql
    # Eski ALT DIZE suzgecleri geri GELMEMELI.
    for gone in ("'%GB%'", "'%IE%'", "'%RU%'", "'%AU%'", "'%NZ%'"):
        assert f"UD_Buyer NOT LIKE {gone}" not in spy.sql


def test_row_is_mapped_to_internal_names():
    box = fetch_unlabeled_boxes(Spy([_row()]), "2026-08-14")[0]

    assert box["store_code"] == "NUNAT1N"      # buyuk harfe cevrilir
    assert box["invoice"] == "5000402"
    assert box["weight_kg"] == 23.5
    assert box["dimensions"] == "60X42X46"
    assert box["packing_date"] == "2026-08-14"


def test_weight_accepts_comma_decimal_and_falls_back_to_zero():
    """Bos agirlik 0.0 olur — uydurma bir varsayilan yanlis etiket bastirir."""
    assert fetch_unlabeled_boxes(Spy([_row(UD_KA="17,5")]), "d")[0]["weight_kg"] == 17.5
    assert fetch_unlabeled_boxes(Spy([_row(UD_KA=None)]), "d")[0]["weight_kg"] == 0.0
    assert fetch_unlabeled_boxes(Spy([_row(UD_KA="GLS")]), "d")[0]["weight_kg"] == 0.0


def test_available_dates_counts_boxes_and_stores():
    rows = [{"packing_date": date(2026, 8, 14), "boxes": 83, "stores": 30}]
    days = available_dates(Spy(rows), days=30)

    assert days == [{"date": "2026-08-14", "boxes": 83, "stores": 30}]


def test_turkey_is_out_of_scope_for_bulk_labels():
    """Kullanici karari (2026-09-01): magaza kodu suzgeci duzeltilince BUTR344
    (13 koli, GLS NL gecmisi YOK) listeye dusmustu. Turkiye'den cikan sevkiyat
    GLS NL kanalindan etiketlenmez."""
    spy = Spy()
    fetch_unlabeled_boxes(spy, "2026-08-14")

    assert "'TR'" in spy.sql and "'TUR'" in spy.sql


def test_row_carries_the_season_so_labels_have_it_immediately():
    """Kullanici bildirdi (2026-09-02): toplu etiketle olusan kolilerin sezonu
    bos gozukuyordu. `erp/sync.py` senkronu yalnizca paketleme tarihi GECMISTE
    olan kolileri kapsar; ileri tarihli etiketler gunlerce beklerdi. Sezon artik
    etiket olusurken DOGRUDAN buradan gelir."""
    box = fetch_unlabeled_boxes(Spy([_row(UD_Season="FA26")]), "2026-08-14")[0]
    assert box["season"] == "FA26"


def test_a_blank_season_column_is_not_invented():
    box = fetch_unlabeled_boxes(Spy([_row(UD_Season=None)]), "2026-08-14")[0]
    assert box["season"] == ""


def test_the_query_selects_the_season_column():
    spy = Spy()
    fetch_unlabeled_boxes(spy, "2026-08-14")
    assert "UD_Season" in spy.sql
