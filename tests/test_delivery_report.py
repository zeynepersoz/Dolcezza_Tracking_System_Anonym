# -*- coding: utf-8 -*-
"""Sevkiyat/teslimat suresi raporu — hafta sonu haric gun hesaplari + gruplama."""
from datetime import datetime

import pytest

from gls_api import config
from tracking.db import ShipmentsDB
from tracking.delivery_report import (build_chart_data, build_parcel_shop_report, build_report,
                                      business_days_between)


@pytest.fixture
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "s.db")


def _tz(y, m, d, h=0, mi=0):
    import timez
    return datetime(y, m, d, h, mi, tzinfo=timez.TZ)


# ------------------------------------------------------------------ business_days_between

def test_a_plain_weekday_span_counts_in_full():
    start = _tz(2026, 8, 24, 9, 0)   # Pazartesi
    end = _tz(2026, 8, 26, 9, 0)     # Carsamba
    assert business_days_between(start, end) == 2.0


def test_a_weekend_inside_the_span_does_not_count():
    """Cuma 09:00 -> Pazartesi 09:00: takvimde 3 gun ama is gunu 1 (sadece Cuma->Cmt gecisi)."""
    start = _tz(2026, 8, 21, 9, 0)   # Cuma
    end = _tz(2026, 8, 24, 9, 0)     # Pazartesi
    # Cuma 09:00 -> Cuma 24:00 (0.625 gun sayilir), Cmt/Paz HIC, Pazartesi 00:00->09:00 (0.375 gun)
    assert business_days_between(start, end) == 1.0


def test_entirely_within_a_single_weekend_day_is_zero():
    start = _tz(2026, 8, 22, 10, 0)  # Cumartesi
    end = _tz(2026, 8, 22, 18, 0)    # ayni Cumartesi
    assert business_days_between(start, end) == 0.0


def test_end_before_start_is_zero_not_negative():
    start = _tz(2026, 8, 24, 9, 0)
    end = _tz(2026, 8, 23, 9, 0)
    assert business_days_between(start, end) == 0.0


def test_missing_dates_are_zero():
    assert business_days_between(None, _tz(2026, 8, 24)) == 0.0
    assert business_days_between(_tz(2026, 8, 24), None) == 0.0


# ------------------------------------------------------------------ build_report

def _add_delivered(db, tracking_no, *, country, method, shipment_date,
                   handed_over_at, delivered_date):
    db.add_parcel(tracking_no=tracking_no, channel="NL", reference="r",
                  consignee_name="c", country=country,
                  shipment_method=method, shipment_date=shipment_date,
                  status="delivered")
    db.update_tracking_info(tracking_no, handed_over_at=handed_over_at,
                            delivered_date=delivered_date)
    db.mark_checked(tracking_no)


def test_report_has_no_data_on_an_empty_db(db, monkeypatch):
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    report = build_report(db)
    assert report["has_data"] is False
    assert report["overall"]["box_count"] == 0


def test_a_normal_delivery_is_not_flagged_delayed(db, monkeypatch):
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    _add_delivered(db, "38120177000001", country="fr", method="1st Truck Shipment NL",
                   shipment_date="2026-08-10",
                   handed_over_at="2026-08-20T09:00:00Z",
                   delivered_date="2026-08-21T09:00:00Z")

    report = build_report(db)

    assert report["has_data"] is True
    assert report["overall"]["box_count"] == 1
    assert report["overall"]["delayed_count"] == 0
    assert report["overall"]["avg_gls_normal"] == 1.0


def test_a_parcel_that_ever_had_an_exception_counts_as_local_delay(db, monkeypatch):
    """`UD_Problemli` ile AYNI tanim: GECMISTE hic exception olmus mu."""
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    _add_delivered(db, "38120177000002", country="de", method="2nd Truck Shipment NL",
                   shipment_date="2026-08-10",
                   handed_over_at="2026-08-18T09:00:00Z",
                   delivered_date="2026-08-24T09:00:00Z")
    db.update_status("38120177000002", "exception", note="Not out for delivery")
    db.update_status("38120177000002", "delivered", note="The parcel has been delivered.")

    report = build_report(db)

    assert report["overall"]["delayed_count"] == 1
    assert report["overall"]["avg_gls_normal"] is None    # tek koli, o da gecikmeli
    assert report["overall"]["avg_gls_delayed"] is not None


def test_breakdown_groups_by_country_and_by_shipment_method(db, monkeypatch):
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    _add_delivered(db, "38120177000003", country="fr", method="1st Truck Shipment NL",
                   shipment_date="2026-08-10",
                   handed_over_at="2026-08-20T09:00:00Z",
                   delivered_date="2026-08-21T09:00:00Z")
    _add_delivered(db, "38120177000004", country="de", method="2nd Truck Shipment NL",
                   shipment_date="2026-08-10",
                   handed_over_at="2026-08-20T09:00:00Z",
                   delivered_date="2026-08-21T09:00:00Z")

    report = build_report(db)

    assert report["country_count"] == 2
    assert {c["name"] for c in report["by_country"]} == {"FR", "DE"}
    assert {m["name"] for m in report["by_method"]} == {"1st Truck Shipment NL", "2nd Truck Shipment NL"}


def test_undelivered_parcels_are_excluded(db, monkeypatch):
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    db.add_parcel(tracking_no="38120177000005", channel="NL", reference="r",
                  consignee_name="c", country="FR", status="in_transit")
    db.mark_checked("38120177000005")

    report = build_report(db)

    assert report["has_data"] is False


def test_a_parcel_missing_the_handover_date_still_counts_toward_the_total_leg(db, monkeypatch):
    """`handed_over_at` yoksa EMC->GLS ve GLS->Butik hesaplanamaz, ama uctan
    uca (paketleme->teslim) yine de olculur — koli raporun disina dusmez."""
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    db.add_parcel(tracking_no="38120177000006", channel="NL", reference="r",
                  consignee_name="c", country="FR", shipment_method="1st Air Shipment NL",
                  shipment_date="2026-08-10", status="delivered")
    db.update_tracking_info("38120177000006", delivered_date="2026-08-21T09:00:00Z")
    db.mark_checked("38120177000006")

    report = build_report(db)

    assert report["overall"]["box_count"] == 1
    assert report["overall"]["avg_total_overall"] is not None
    assert report["overall"]["avg_emc_to_gls"] is None


def test_quantities_are_skipped_gracefully_without_mssql(db, monkeypatch):
    """MSSQL yapilandirilmamissa (yerel/mock) rapor urun adedi olmadan acilir."""
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    _add_delivered(db, "38120177000007", country="fr", method="1st Truck Shipment NL",
                   shipment_date="2026-08-10",
                   handed_over_at="2026-08-20T09:00:00Z",
                   delivered_date="2026-08-21T09:00:00Z")

    report = build_report(db)

    assert report["has_items"] is False
    assert report["overall"]["item_count"] == 0


def test_quantities_are_attached_when_mssql_is_available(db, monkeypatch):
    """MSSQL varsa ERP'den urun adedi cekilip satirlara/gruplara islenir."""
    import erp.contents as contents_mod

    monkeypatch.setattr(config, "mssql_configured", lambda: True)
    monkeypatch.setattr("erp.client.ERPClient", lambda: object())
    monkeypatch.setattr(contents_mod, "total_quantities",
                        lambda client, nos: {"38120177000008": 14})
    _add_delivered(db, "38120177000008", country="fr", method="1st Truck Shipment NL",
                   shipment_date="2026-08-10",
                   handed_over_at="2026-08-20T09:00:00Z",
                   delivered_date="2026-08-21T09:00:00Z")

    report = build_report(db)

    assert report["has_items"] is True
    assert report["overall"]["item_count"] == 14
    assert report["by_country"][0]["item_count"] == 14


def test_an_erp_failure_does_not_break_the_report(db, monkeypatch):
    """MSSQL 'yapilandirilmis' gorunse bile baglanti patlarsa rapor yine acilmali."""
    monkeypatch.setattr(config, "mssql_configured", lambda: True)
    monkeypatch.setattr("erp.client.ERPClient",
                        lambda: (_ for _ in ()).throw(RuntimeError("MSSQL kapali")))
    _add_delivered(db, "38120177000009", country="fr", method="1st Truck Shipment NL",
                   shipment_date="2026-08-10",
                   handed_over_at="2026-08-20T09:00:00Z",
                   delivered_date="2026-08-21T09:00:00Z")

    report = build_report(db)

    assert report["overall"]["box_count"] == 1
    assert report["overall"]["item_count"] == 0


# ------------------------------------------------------------------ build_chart_data

def test_chart_data_has_no_data_on_an_empty_db(db):
    """Kullanici istegi (2026-08-31): dashbordun altindaki mini grafik."""
    chart = build_chart_data(db)
    assert chart["has_data"] is False
    assert chart["by_country"] == []


def test_chart_data_does_not_touch_erp(db, monkeypatch):
    """/reports'un aksine urun adedi sorgulanmaz — dashboard ERP gecikmesini
    goze alamaz; `ERPClient`e HIC dokunulmamali."""
    monkeypatch.setattr(config, "mssql_configured",
                        lambda: (_ for _ in ()).throw(AssertionError("ERP'ye gidilmemeliydi")))
    _add_delivered(db, "38120177000010", country="fr", method="1st Truck Shipment NL",
                   shipment_date="2026-08-10",
                   handed_over_at="2026-08-20T09:00:00Z",
                   delivered_date="2026-08-21T09:00:00Z")

    chart = build_chart_data(db)

    assert chart["has_data"] is True
    assert chart["by_country"] == [
        {"name": "FR", "emc_to_gls": 8.5, "gls_to_store": 1.0, "box_count": 1},
    ]


def test_chart_data_is_capped_and_sorted_by_box_count(db, monkeypatch):
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    countries = ["fr", "de", "it", "es", "nl", "be", "pt", "at", "pl"]
    for i, country in enumerate(countries):
        # FR ilk 3 kez eklenir -> en cok koli, listenin basinda olmali.
        n = 3 if country == "fr" else 1
        for j in range(n):
            _add_delivered(db, f"38120177{i:02d}{j:02d}01", country=country,
                           method="1st Truck Shipment NL",
                           shipment_date="2026-08-10",
                           handed_over_at="2026-08-20T09:00:00Z",
                           delivered_date="2026-08-21T09:00:00Z")

    chart = build_chart_data(db, limit=8)

    assert len(chart["by_country"]) == 8       # 9 ulkeden yalnizca ilk 8
    assert chart["by_country"][0]["name"] == "FR"
    assert chart["by_country"][0]["box_count"] == 3


# ------------------------------------------------------------------ ParcelShop
# Kullanici bulgusu (2026-08-31): "parcel shopa teslim edilen ancak musterinin
# almadigi gonderi de iadeye donebiliyor, returned durumu da yanlis oluyor" —
# metinler production'dan gercek GLS tarama metinleridir.

def test_untouched_parcel_is_not_flagged_as_parcel_shop(db):
    _add_delivered(db, "38120177020001", country="fr", method="1st Truck Shipment NL",
                   shipment_date="2026-08-10",
                   handed_over_at="2026-08-20T09:00:00Z",
                   delivered_date="2026-08-21T09:00:00Z")

    assert db.parcel_shop_parcels() == []


def test_a_parcel_shop_delivery_is_flagged_as_collected(db):
    # `add_parcel` "delivered" ile baslarsa, aynisina `update_status` cagirmak
    # NO-OP olur (bkz. ShipmentsDB.update_status: "Sadece degistiyse ... kaydeder")
    # — olay hic yazilmaz. Bu yuzden BASKA bir statuden basliyoruz.
    db.add_parcel(tracking_no="38120177020002", channel="NL", reference="r",
                  consignee_name="c", country="ES", status="out_for_delivery")
    db.update_status("38120177020002", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")

    rows = db.parcel_shop_parcels()

    assert len(rows) == 1
    assert rows[0]["tracking_no"] == "38120177020002"
    assert rows[0]["status"] == "delivered"
    assert rows[0]["timed_out"] is False
    assert rows[0]["returned"] is False
    assert rows[0]["at_risk"] is False


def test_a_parcel_shop_timeout_is_flagged_at_risk(db):
    """Musteri almadi: GLS 'maksimum saklama suresi' olayini yazdi ama henuz
    'returned' demedi — bu koli hala RISKTE, sessizce kaybolmamali."""
    db.add_parcel(tracking_no="38120177020003", channel="NL", reference="r",
                  consignee_name="c", country="ES", status="out_for_delivery")
    db.update_status("38120177020003", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")
    db.update_status("38120177020003", "exception",
                     note="The parcel has reached the maximum storage time in the ParcelShop.")

    rows = db.parcel_shop_parcels()

    assert len(rows) == 1
    assert rows[0]["status"] == "exception"
    assert rows[0]["timed_out"] is True
    assert rows[0]["returned"] is False
    assert rows[0]["at_risk"] is True


def test_a_parcel_shop_parcel_that_bounced_back_is_flagged_returned(db):
    db.add_parcel(tracking_no="38120177020004", channel="NL", reference="r",
                  consignee_name="c", country="ES", status="out_for_delivery")
    db.update_status("38120177020004", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")
    db.update_status("38120177020004", "exception",
                     note="The parcel has reached the maximum storage time in the ParcelShop.")
    db.update_status("38120177020004", "returned", note="Returned to sender.")

    rows = db.parcel_shop_parcels()

    assert rows[0]["status"] == "returned"
    assert rows[0]["returned"] is True
    assert rows[0]["at_risk"] is False       # zaten sonuclandi, riskli degil artik


def test_parcel_shop_parcels_respects_the_channel(db):
    """Onceden takip numarasi onegiyle suzuluyordu; 2026-09-03'ten beri KANAL
    (tasima firmasi) ile — rapor GLS-NL / GLS-IE / FEDEX icin ayri hazirlaniyor."""
    db.add_parcel(tracking_no="38120177020005", channel="NL", reference="r",
                  consignee_name="c", country="ES", status="out_for_delivery")
    db.update_status("38120177020005", "delivered", note="reached the ParcelShop.")
    db.add_parcel(tracking_no="21569700099", channel="IE", reference="r",
                  consignee_name="c", country="IE", status="out_for_delivery")
    db.update_status("21569700099", "delivered", note="reached the ParcelShop.")

    assert [r["tracking_no"] for r in db.parcel_shop_parcels("NL")] == ["38120177020005"]
    assert [r["tracking_no"] for r in db.parcel_shop_parcels("IE")] == ["21569700099"]
    assert len(db.parcel_shop_parcels()) == 2          # kanalsiz = hepsi


def test_report_summarizes_parcel_shop_counts(db, monkeypatch):
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    db.add_parcel(tracking_no="38120177020006", channel="NL", reference="r",
                  consignee_name="c", country="ES", status="out_for_delivery")
    db.update_status("38120177020006", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")
    db.add_parcel(tracking_no="38120177020007", channel="NL", reference="r",
                  consignee_name="c", country="DE", status="out_for_delivery")
    db.update_status("38120177020007", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")
    db.update_status("38120177020007", "exception",
                     note="The parcel has reached the maximum storage time in the ParcelShop.")
    db.update_status("38120177020007", "returned", note="Returned to sender.")

    ps = build_parcel_shop_report(db)

    assert ps["total"] == 2
    assert ps["collected"] == 1
    assert ps["returned"] == 1
    assert ps["timed_out"] == 1
    assert ps["at_risk"] == 0


def test_build_report_no_longer_carries_parcel_shop_since_it_has_its_own_page(db, monkeypatch):
    """`/reports/parcel-shop` ayri bir sayfa oldu (kullanici istegi,
    2026-08-31) — genel raporun icinde ikinci kez hesaplanmamali."""
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    report = build_report(db)
    assert "parcel_shop" not in report
