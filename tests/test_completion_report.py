# -*- coding: utf-8 -*-
"""Siparis tamamlanma raporu (İş biriminin istegi, 2026-08-31).

Uc seviye: siparis (OrderQty) -> sevk (InvQty) -> musteriye teslim (bizim
takip verimiz). Kapsam farki bilerek vardir ve rapor onu gizlemez —
bkz. tracking/completion_report.py modul basligi.
"""
import pytest

from erp import orders
from gls_api import config
from tracking.completion_report import build_completion_report, completion_export_table
from tracking.db import ShipmentsDB


@pytest.fixture
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "s.db")


@pytest.fixture
def erp(monkeypatch):
    """MSSQL'i taklit eder: ERP'ye HIC gidilmez, sayilar testte sabittir."""
    monkeypatch.setattr(config, "mssql_configured", lambda: True)
    monkeypatch.setattr(config, "season_label", lambda: "FA26")
    monkeypatch.setattr("erp.client.ERPClient", lambda timeout=0: object())

    state = {
        "totals": {"order_count": 3, "customer_count": 2,
                   "order_qty": 80000, "inv_qty": 60000},
        "by_country": {
            "FR": {"order_count": 2, "order_qty": 50000, "inv_qty": 40000},
            "IE": {"order_count": 1, "order_qty": 30000, "inv_qty": 20000},
        },
        "quantities": {},
    }
    monkeypatch.setattr("erp.orders.fetch_totals", lambda c, s="": state["totals"])
    monkeypatch.setattr("erp.orders.fetch_by_country", lambda c, s="": state["by_country"])
    monkeypatch.setattr("erp.contents.total_quantities",
                        lambda c, nos: state["quantities"])
    return state


# ---------------------------------------------------------------- gorunum adi

def test_the_orders_view_name_comes_from_the_season(monkeypatch):
    """Sezon AYRI bir ayar alani degil — `MSSQL_SEASON_VIEWS`ten turer."""
    monkeypatch.setattr(config, "season_label", lambda: "FA26")
    assert orders.orders_view() == "BCH_FA26_Dolcezza_Orders"
    assert orders.orders_view("SP27") == "BCH_SP27_Dolcezza_Orders"


def test_a_bogus_season_cannot_inject_sql():
    """Gorunum adi SQL metnine gomulur — `safe_identifier` kapiyi kapatir."""
    from erp.client import ERPError
    with pytest.raises(ERPError):
        orders.orders_view("FA26; DROP TABLE Erp_Box--")


def test_kayhans_country_filter_is_used_verbatim():
    assert orders.EXCLUDED_ORDER_COUNTRIES == ("CA", "US", "RU", "AU", "NZ")


# ---------------------------------------------------------------- rapor

def test_the_headline_ratio_matches_kayhans_example(db, erp):
    """Onun ornegi: "80000 adet siparis, 60000 tr'den cikti" -> %75."""
    report = build_completion_report(db)

    assert report["has_data"] is True
    assert report["order_qty"] == 80000
    assert report["inv_qty"] == 60000
    assert report["pct_shipped"] == 75.0


def test_delivered_quantities_come_from_our_own_tracking(db, erp):
    """3. seviye ERP'den DEGIL bizim takip verimizden gelir: yalnizca
    `delivered` kolilerin icerigi sayilir."""
    db.add_parcel(tracking_no="38120177000001", channel="NL", consignee_name="c",
                  country="FR", season="FA26", status="delivered")
    db.add_parcel(tracking_no="38120177000002", channel="NL", consignee_name="c",
                  country="FR", season="FA26", status="in_transit")
    erp["quantities"] = {"38120177000001": 120, "38120177000002": 80}

    report = build_completion_report(db)

    assert report["boxed_qty"] == 200        # ikisi de kutulanmis
    assert report["delivered_qty"] == 120    # yalnizca teslim edilen
    assert report["pct_delivered_of_boxed"] == 60.0


def test_a_country_we_do_not_track_is_marked_not_tracked(db, erp):
    """IE bizim takip kapsamimizda DEGIL (bkz. erp/sync.py). Sutunun "%0"
    yazmasi "hic teslim edilmedi" demek olurdu — oysa olcmuyoruz."""
    db.add_parcel(tracking_no="38120177000003", channel="NL", consignee_name="c",
                  country="FR", season="FA26", status="delivered")
    erp["quantities"] = {"38120177000003": 500}

    report = build_completion_report(db)
    rows = {r["country"]: r for r in report["by_country"]}

    assert rows["FR"]["is_tracked"] is True
    assert rows["IE"]["is_tracked"] is False
    assert rows["IE"]["delivered_qty"] == 0


def test_countries_are_sorted_by_order_quantity(db, erp):
    report = build_completion_report(db)
    assert [r["country"] for r in report["by_country"]] == ["FR", "IE"]


def test_parcels_from_another_season_are_not_counted(db, erp):
    """Rapor SEZON bazlidir; gecen sezonun kolileri karismamali."""
    db.add_parcel(tracking_no="38120177000004", channel="NL", consignee_name="c",
                  country="FR", season="SP26", status="delivered")
    erp["quantities"] = {"38120177000004": 999}

    assert build_completion_report(db)["delivered_qty"] == 0


def test_a_zero_order_quantity_does_not_divide_by_zero(db, erp):
    erp["totals"] = {"order_count": 0, "customer_count": 0, "order_qty": 0, "inv_qty": 0}
    erp["by_country"] = {}

    report = build_completion_report(db)

    assert report["has_data"] is False
    assert report["pct_shipped"] is None


# ---------------------------------------------------------------- dayaniklilik

def test_the_report_opens_without_mssql(db, monkeypatch):
    """MSSQL yoksa (yerel/mock) rapor PATLAMAZ, bos doner."""
    monkeypatch.setattr(config, "mssql_configured", lambda: False)

    report = build_completion_report(db)

    assert report["has_data"] is False
    assert report["by_country"] == []


def test_a_missing_orders_view_does_not_break_the_page(db, monkeypatch):
    """Yeni sezonda gorunum henuz acilmamis olabilir — panel 500 vermemeli."""
    monkeypatch.setattr(config, "mssql_configured", lambda: True)
    monkeypatch.setattr(config, "season_label", lambda: "SP27")
    monkeypatch.setattr("erp.client.ERPClient", lambda timeout=0: object())
    monkeypatch.setattr("erp.orders.fetch_totals",
                        lambda c, s="": (_ for _ in ()).throw(RuntimeError("Invalid object name")))

    assert build_completion_report(db)["has_data"] is False


def test_an_erp_contents_failure_still_shows_the_order_numbers(db, erp, monkeypatch):
    """Urun adedi cekilemezse siparis/sevk rakamlari YINE gorunmeli —
    3. seviye bos kalir, ilk ikisi kaybolmaz."""
    db.add_parcel(tracking_no="38120177000005", channel="NL", consignee_name="c",
                  country="FR", season="FA26", status="delivered")
    monkeypatch.setattr("erp.contents.total_quantities",
                        lambda c, nos: (_ for _ in ()).throw(RuntimeError("MSSQL kapali")))

    report = build_completion_report(db)

    assert report["order_qty"] == 80000
    assert report["delivered_qty"] == 0
    assert report["tracked"] is False


# ---------------------------------------------------------------- disa aktarim

def test_the_export_table_marks_untracked_countries(db, erp):
    db.add_parcel(tracking_no="38120177000006", channel="NL", consignee_name="c",
                  country="FR", season="FA26", status="delivered")
    erp["quantities"] = {"38120177000006": 100}

    headers, rows = completion_export_table(build_completion_report(db))

    assert len(headers) == 8
    by_country = {r[0]: r for r in rows}
    assert by_country["FR"][5] == 100      # teslim edilen adet
    assert by_country["IE"][7] == "—"      # takip disi -> yuzde yok
