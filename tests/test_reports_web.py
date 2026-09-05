# -*- coding: utf-8 -*-
"""`/reports/*` sayfalari — sablon gercekten renderlaniyor mu (bkz. test_dashboard.py
ile ayni istemci deseni).

Kullanici istegi (2026-08-31): eski tek sayfali `/reports` IKIye bolundu —
`/reports/delivery-times` (teslimat sureleri) ve `/reports/parcel-shop`
(ParcelShop teslimatlari), ayri menu ogeleriyle secilebilir. `/reports`
geriye donuk uyumluluk icin ilkine yonlendirir.
"""
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from gls_api import config
from tracking.db import ShipmentsDB
from web import deps


@pytest.fixture
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "s.db")


@pytest.fixture
def client(db, monkeypatch):
    from web.main import app

    monkeypatch.setattr(deps, "_shipments_db", db)
    monkeypatch.setattr(config, "mssql_configured", lambda: False)

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "k", "role": "admin"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


# --------------------------------------------------------------- yonlendirme

def test_bare_reports_redirects_to_delivery_times(client):
    """Eski yer imleri kirilmasin — `/reports` hala calisir, ilk alt sayfaya iner."""
    resp = client.get("/reports", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/reports/delivery-times"


# ------------------------------------------------------------ teslimat sureleri

def test_delivery_times_page_renders_with_no_data(client):
    resp = client.get("/reports/delivery-times")
    assert resp.status_code == 200
    assert "Sevkiyat" in resp.text


def test_delivery_times_page_renders_with_a_delivered_parcel(client, db):
    db.add_parcel(tracking_no="38120177000001", channel="NL", reference="r",
                  consignee_name="c", country="FR",
                  shipment_method="1st Truck Shipment NL",
                  shipment_date="2026-08-10", status="delivered")
    db.update_tracking_info("38120177000001",
                            handed_over_at="2026-08-20T09:00:00Z",
                            delivered_date="2026-08-21T09:00:00Z")
    db.mark_checked("38120177000001")

    html = client.get("/reports/delivery-times").text

    assert "FR" in html
    assert "1st Truck Shipment NL" in html


def test_delivery_times_page_shows_the_delay_warning_when_something_is_delayed(client, db):
    """Uyari kutusu KARSILASTIRMA gosterir — hem normal hem gecikmeli en az
    birer koli lazim, yoksa 'normale gore X kat' anlamsiz kalir."""
    db.add_parcel(tracking_no="38120177000001", channel="NL", reference="r",
                  consignee_name="c", country="DE",
                  shipment_method="2nd Truck Shipment NL",
                  shipment_date="2026-08-10", status="delivered")
    db.update_tracking_info("38120177000001",
                            handed_over_at="2026-08-18T09:00:00Z",
                            delivered_date="2026-08-19T09:00:00Z")
    db.mark_checked("38120177000001")

    db.add_parcel(tracking_no="38120177000002", channel="NL", reference="r",
                  consignee_name="c", country="DE",
                  shipment_method="2nd Truck Shipment NL",
                  shipment_date="2026-08-10", status="delivered")
    db.update_tracking_info("38120177000002",
                            handed_over_at="2026-08-18T09:00:00Z",
                            delivered_date="2026-08-24T09:00:00Z")
    db.mark_checked("38120177000002")
    db.update_status("38120177000002", "exception", note="Not out for delivery")
    db.update_status("38120177000002", "delivered", note="The parcel has been delivered.")

    html = client.get("/reports/delivery-times").text

    assert "Lokal Teslimat Gecikmeleri" in html


def test_delivery_times_page_does_not_mention_parcel_shop(client, db):
    """ParcelShop bolumu artik KENDI sayfasinda — burada karismamali
    (kullanici istegi, 2026-08-31: iki ayri alan)."""
    db.add_parcel(tracking_no="38120177000009", channel="NL", reference="r",
                  consignee_name="c", country="FR",
                  shipment_method="1st Truck Shipment NL",
                  shipment_date="2026-08-10", status="delivered")
    db.update_tracking_info("38120177000009",
                            handed_over_at="2026-08-18T09:00:00Z",
                            delivered_date="2026-08-19T09:00:00Z")

    html = client.get("/reports/delivery-times").text

    # Menudeki alt-oge baglantisi "ParcelShop Teslimatları" YAZAR (bkz. base.html) —
    # burada denetlenen SAYFA ICERIGI, o ayri sayfaya ozel kart/uyari metni.
    assert "Şu An Riskte" not in html
    assert "iadeye" not in html.lower()


def test_delivery_times_export_buttons_only_appear_with_data(client, db):
    assert "export.xlsx" not in client.get("/reports/delivery-times").text

    db.add_parcel(tracking_no="38120177000010", channel="NL", reference="r",
                  consignee_name="c", country="FR",
                  shipment_method="1st Truck Shipment NL",
                  shipment_date="2026-08-10", status="delivered")
    db.update_tracking_info("38120177000010",
                            handed_over_at="2026-08-18T09:00:00Z",
                            delivered_date="2026-08-19T09:00:00Z")

    assert "/reports/delivery-times/export.xlsx" in client.get("/reports/delivery-times").text


def test_delivery_times_xlsx_export_downloads(client, db):
    db.add_parcel(tracking_no="38120177000011", channel="NL", reference="r",
                  consignee_name="c", country="FR",
                  shipment_method="1st Truck Shipment NL",
                  shipment_date="2026-08-10", status="delivered")
    db.update_tracking_info("38120177000011",
                            handed_over_at="2026-08-18T09:00:00Z",
                            delivered_date="2026-08-19T09:00:00Z")

    resp = client.get("/reports/delivery-times/export.xlsx")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert len(resp.content) > 0


def test_delivery_times_pdf_export_downloads(client, db):
    db.add_parcel(tracking_no="38120177000012", channel="NL", reference="r",
                  consignee_name="c", country="FR",
                  shipment_method="1st Truck Shipment NL",
                  shipment_date="2026-08-10", status="delivered")
    db.update_tracking_info("38120177000012",
                            handed_over_at="2026-08-18T09:00:00Z",
                            delivered_date="2026-08-19T09:00:00Z")

    resp = client.get("/reports/delivery-times/export.pdf")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


# ---------------------------------------------------------------- parcel shop

def test_parcel_shop_page_says_so_when_empty(client, db):
    html = client.get("/reports/parcel-shop").text
    assert "ParcelShop Teslimatları" in html
    assert "Kayıtlarda ParcelShop üzerinden geçen koli bulunamadı." in html
    assert "export.xlsx" not in html    # veri yoksa export dugmesi de yok


def test_parcel_shop_page_lists_touched_parcels(client, db):
    """Kullanici bulgusu (2026-08-31): "parcel shopa teslim edilen ancak
    musterinin almadigi gonderi de iadeye donebiliyor" — ayri sayfada gorunmeli."""
    db.add_parcel(tracking_no="38120177000003", channel="NL", reference="r",
                  consignee_name="c", country="ES", status="out_for_delivery")
    db.update_status("38120177000003", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")

    html = client.get("/reports/parcel-shop").text

    assert "38120177000003" in html
    assert "/reports/parcel-shop/export.xlsx" in html


def test_parcel_shop_row_links_to_the_tracking_search(client, db):
    """"Tıklayınca detayların da gözükmesi lazım" (kullanici istegi, 2026-08-31)."""
    db.add_parcel(tracking_no="38120177000013", channel="NL", reference="r",
                  consignee_name="c", country="ES", status="out_for_delivery")
    db.update_status("38120177000013", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")

    html = client.get("/reports/parcel-shop").text

    assert "/tracking?search=38120177000013" in html


def test_parcel_shop_xlsx_export_downloads(client, db):
    db.add_parcel(tracking_no="38120177000014", channel="NL", reference="r",
                  consignee_name="c", country="ES", status="out_for_delivery")
    db.update_status("38120177000014", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")

    resp = client.get("/reports/parcel-shop/export.xlsx")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def test_parcel_shop_pdf_export_downloads(client, db):
    db.add_parcel(tracking_no="38120177000015", channel="NL", reference="r",
                  consignee_name="c", country="ES", status="out_for_delivery")
    db.update_status("38120177000015", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")

    resp = client.get("/reports/parcel-shop/export.pdf")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


# -------------------------------------------------------------------- yetki

def test_a_viewer_can_open_both_report_pages(db, monkeypatch):
    """Rapor salt okunur bilgidir — `viewer` da gorebilmeli (dashboard/tracking ile ayni kural)."""
    from web.main import app
    monkeypatch.setattr(deps, "_shipments_db", db)
    monkeypatch.setattr(config, "mssql_configured", lambda: False)

    async def fake_viewer(request: Request):
        request.state.user = {"uid": 2, "username": "v", "role": "viewer"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_viewer
    try:
        c = TestClient(app)
        assert c.get("/reports/delivery-times").status_code == 200
        assert c.get("/reports/parcel-shop").status_code == 200
    finally:
        app.dependency_overrides.clear()


# -------------------------------------------------------------------- menu

def test_the_reports_group_expands_to_both_sub_pages(client, db):
    """Kullanici istegi (2026-08-31): "reports kutusuna bastığında altta
    parcelshop deliveries ve delivery times adlı iki ayrı alan açıp oradan
    da seçmesi lazım"."""
    html = client.get("/reports/delivery-times").text

    assert "/reports/delivery-times" in html
    assert "/reports/parcel-shop" in html
    assert "ParcelShop Teslimatları" in html   # menudeki alt-oge etiketi
    assert "Teslimat Süreleri" in html


# ------------------------------------------------------------ tamamlanma

@pytest.fixture
def erp_orders(monkeypatch):
    """Tamamlanma raporu icin sahte ERP (Onun ornek rakamlari)."""
    monkeypatch.setattr(config, "mssql_configured", lambda: True)
    monkeypatch.setattr(config, "season_label", lambda: "FA26")
    monkeypatch.setattr("erp.client.ERPClient", lambda timeout=0: object())
    monkeypatch.setattr("erp.orders.fetch_totals", lambda c, s="": {
        "order_count": 3, "customer_count": 2, "order_qty": 80000, "inv_qty": 60000})
    monkeypatch.setattr("erp.orders.fetch_by_country", lambda c, s="": {
        "FR": {"order_count": 2, "order_qty": 50000, "inv_qty": 40000}})
    monkeypatch.setattr("erp.contents.total_quantities", lambda c, nos: {})


def test_completion_page_shows_the_three_levels(client, erp_orders):
    html = client.get("/reports/completion").text

    assert "Sipariş Tamamlanma" in html
    assert "80,000" in html      # siparis
    assert "60,000" in html      # sevk
    assert "%75.0" in html       # tamamlanma orani


def test_completion_page_states_the_scope_difference(client, erp_orders):
    """Kapsam farki GIZLENMEZ: teslim rakami yalnizca GLS NL kanalini kapsar."""
    html = client.get("/reports/completion").text
    assert "Kapsam Farkı" in html


def test_completion_page_says_so_without_order_data(client, monkeypatch):
    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    html = client.get("/reports/completion").text
    assert "sipariş verisi bulunamadı" in html
    assert "export.xlsx" not in html


def test_completion_exports_download(client, erp_orders):
    xlsx = client.get("/reports/completion/export.xlsx")
    assert xlsx.status_code == 200
    assert xlsx.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    pdf = client.get("/reports/completion/export.pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_the_reports_menu_now_lists_three_pages(client, db):
    html = client.get("/reports/delivery-times").text
    assert "/reports/completion" in html
    assert "/reports/delivery-times" in html
    assert "/reports/parcel-shop" in html


# ======================================================================
# Taşıma firması sekmeleri (kullanıcı isteği 2026-09-03)
# ======================================================================
def test_delivery_times_report_is_per_carrier(client, db):
    """/reports/delivery-times?carrier=IE -> yalnızca GLS-IE kolileri."""
    def _delivered(tn, channel, country):
        db.add_parcel(tracking_no=tn, channel=channel, reference="r",
                      consignee_name="c", country=country,
                      shipment_date="2026-08-01", status="delivered")
        db.update_tracking_info(tn, handed_over_at="2026-08-02T09:00:00Z",
                                delivered_date="2026-08-04T09:00:00Z")
        db.mark_checked(tn)

    _delivered("38120177030001", "NL", "DE")
    _delivered("21569979700", "IE", "IE")

    nl = client.get("/reports/delivery-times?carrier=NL").text
    ie = client.get("/reports/delivery-times?carrier=IE").text
    assert "GLS-IE" in nl and "GLS-NL" in nl        # sekmeler her sayfada
    # NL sekmesinde NL ülkesi (DE) tablo satırında, IE sekmesinde IE ülkesi
    assert ">DE<" in nl
    assert ">IE<" in ie and ">DE<" not in ie


def test_an_unknown_carrier_falls_back_to_the_first(client):
    r = client.get("/reports/delivery-times?carrier=BOGUS")
    assert r.status_code == 200        # normalize_report_channel -> NL


def test_parcel_shop_report_is_per_carrier(client):
    r = client.get("/reports/parcel-shop?carrier=FEDEX")
    assert r.status_code == 200
    assert "GLS-NL" in r.text and "FEDEX" in r.text     # sekmeler
