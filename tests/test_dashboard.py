# -*- coding: utf-8 -*-
"""Ana paneldeki ozet sayilar.

"Bu hafta" karti uzun sure 0 gosterdi: olcut parcanin BIZIM veritabanimiza
girdigi an (`created_at`) idi, sevk tarihi degil. MSSQL senkronu binlerce satiri
tek gunde yazinca kart o gunden sonra kalici olarak boslaniyordu.
"""
from datetime import date, timedelta

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import i18n
import timez
from auth.dependencies import get_current_user
from tracking.db import ShipmentsDB
from web import deps


@pytest.fixture
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "s.db")


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def test_this_week_counts_by_shipment_date_not_import_date(db):
    """Bugun senkronlanan ESKI bir sevkiyat "bu hafta"ya sayilmaz."""
    db.add_parcel(tracking_no="38120177000001", channel="NL",
                  consignee_name="c", country="DE", shipment_date=_day(-2))
    db.add_parcel(tracking_no="38120177000002", channel="NL",
                  consignee_name="c", country="DE", shipment_date=_day(-40))

    assert db.week_summary()["total"] == 1


def test_a_parcel_without_a_shipment_date_falls_back_to_its_creation(db):
    """Eski kayitlarda alan bos; kart onlari kaybetmemeli."""
    db.add_parcel(tracking_no="38120177000003", channel="NL",
                  consignee_name="c", country="DE")

    assert db.week_summary()["total"] == 1


# ------------------------------------------------------- tarayici sagligi karti

@pytest.fixture
def client(db, monkeypatch):
    from web.main import app

    monkeypatch.setattr(deps, "_shipments_db", db)

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "k", "role": "admin"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------- "bugun teslim edilen" karti

def test_todays_deliveries_are_counted_by_the_local_day(db):
    """Gun kovasi ISTANBUL gunudur (bkz. `_DELIVERY_DAY`).

    22:30Z bizde ERTESI gun 01:30'dur; UTC gunune bakan bir sayac o teslimati
    bir onceki gune yazar ve kullanici sabah karti 0 gorurdu.
    """
    db.add_parcel(tracking_no="38120177000010", channel="NL", consignee_name="c",
                  country="DE", status="delivered",
                  delivered_date="2026-08-19T22:30:00Z")

    assert db.delivered_on("2026-08-20") == 1
    assert db.delivered_on("2026-08-19") == 0


def test_a_parcel_delivered_on_another_day_is_not_counted(db):
    db.add_parcel(tracking_no="38120177000011", channel="NL", consignee_name="c",
                  country="DE", status="delivered",
                  delivered_date="2026-08-18T10:00:00Z")

    assert db.delivered_on("2026-08-19") == 0


def test_only_delivered_parcels_count(db):
    """Damgasi o gune dusen ama teslim EDILMEMIS parca sayilmamali."""
    db.add_parcel(tracking_no="38120177000012", channel="NL", consignee_name="c",
                  country="DE", status="in_transit",
                  delivered_date="2026-08-19T10:00:00Z")

    assert db.delivered_on("2026-08-19") == 0


def test_the_card_links_to_the_filtered_list(client, db):
    """Sayiyi gorup listeyi elle suzmek fazladan bir adimdi."""
    html = client.get("/").text

    today = timez.now().date().isoformat()
    assert f"/tracking?delivered_day={today}" in html
    assert "/tracking?status=out_for_delivery" in html


def test_the_card_shows_the_parcels_not_just_a_number(client, db):
    """Kullanici sayiyi degil LISTEYI istedi: "takip numarasi, fatura vs.".

    Sayaci gorup listeyi elle suzmek fazladan bir adimdi; panel acilir acilmaz
    bugun ne teslim edildi ve gun icinde ne teslim edilecek gorunmeli.
    """
    today = timez.now().date().isoformat()
    db.add_parcel(tracking_no="38120177000020", channel="NL", consignee_name="c",
                  country="DE", status="delivered", invoice_number="FTR-1",
                  store_code="AHAAT01", delivered_date=f"{today}T09:00:00Z")
    db.add_parcel(tracking_no="38120177000021", channel="NL", consignee_name="c",
                  country="FR", status="out_for_delivery", invoice_number="FTR-2",
                  store_code="BHBBT02")

    html = client.get("/").text

    assert "38120177000020" in html and "FTR-1" in html and "AHAAT01" in html
    assert "38120177000021" in html and "FTR-2" in html and "BHBBT02" in html


def test_the_card_carries_five_columns_so_it_does_not_scroll_sideways(client, db):
    """Bu kutulardan IKISI yan yana duruyor; yedi kolon yarim genislige sigmiyor
    ve altta yatay kaydirma cubugu cikiyordu (kullanici sikayeti).

    Kanal takip numarasinin onekinden, sezon magaza kodundan zaten okunuyor —
    ikisi de /tracking'te duruyor. `#` BASLIKTA durur, hucrede DEGIL: kullanici
    fatura numarasini kopyalayip ERP'de ariyor, `#5000466` orada bulunmaz."""
    db.add_parcel(tracking_no="38120177000022", channel="NL", consignee_name="c",
                  country="FR", status="out_for_delivery", invoice_number="FTR-3",
                  store_code="BHBBT02", season="FA26")

    card = client.get("/").text.split(i18n.t("dash.out_note", "tr"))[1]
    head = card.split("<thead>")[1].split("</thead>")[0]

    assert head.count("<th>") == 5
    assert i18n.t("field.channel", "tr") not in head
    assert i18n.t("field.season", "tr") not in head
    assert "#" in head
    assert ">FTR-3<" in card and "#FTR-3" not in card


def test_an_empty_day_says_so_instead_of_showing_a_blank_table(client, db):
    html = client.get("/").text

    assert i18n.t("dash.no_delivered_today", "tr") in html
    assert i18n.t("dash.no_out_for_delivery", "tr") in html


def test_the_last_scan_survives_a_restart(client, db):
    """Kart "en son ne zaman tarandi"yi DISKTEN okumali.

    Once tarayici nesnesinin oznitelikleri okunuyordu; nesne her yeniden
    baslatmada sifirdan doguyor, dolayisiyla her aktarimdan sonra saatlerdir
    calisan sistem icin kart "Henüz çalışmadı" diyordu. Bu senaryoda uygulama
    yeni ayaga kalkmistir (tarayici nesnesi yok) ama disk turu biliyor.
    """
    db.record_tick(ok=True)

    html = client.get("/").text

    # Damga DISKTE UTC durur, EKRANDA yerel saatle cizilir (bkz. timez).
    assert timez.stamp(db.health()["last_run_at"]) in html


def test_a_broken_parcel_does_not_make_the_card_say_error(client, db):
    """Kart "Hata" demek icin TARAMANIN eskimesine bakmali, `last_error`a degil.

    `record_tick()` hata metnini her turda yaziyor; canlida kalici bozuk uc
    parca vardi, dolayisiyla alan hic bosalmiyor ve kart sistem saglikliyken
    bile surekli kirmizi duruyordu. Olcut `/health` ve uyari mailiyle ayni.
    """
    db.record_tick(ok=True, error="38120177011234: error return without exception set")

    html = client.get("/").text

    assert i18n.t("dash.health_ok", "tr") in html
    assert i18n.t("dash.health_error", "tr") not in html


# ------------------------------------------------------------- durum cubugu

def test_the_bar_adds_up_to_the_total(db):
    """Cubuktaki sayilarin toplami paydanin ta kendisi olmali."""
    from web.routers.dashboard import _status_flow

    for i, status in enumerate(["created", "created", "in_transit", "delivered"]):
        db.add_parcel(tracking_no=f"3812017703000{i}", channel="NL",
                      consignee_name="c", country="DE", status=status)

    flow = _status_flow(db.counts_by_status(), db.total(), db.channel_breakdown())

    assert sum(f["count"] for f in flow) == db.total() == 4
    assert "returned" in [f["code"] for f in flow]


def test_cancelled_status_with_no_parcels_is_not_drawn(db):
    """`cancelled` canlida 0 — yalnizca koli varsa cizilir."""
    from web.routers.dashboard import _status_flow

    db.add_parcel(tracking_no="38120177031000", channel="NL", consignee_name="c",
                  country="DE", status="delivered")

    codes = [f["code"] for f in _status_flow(db.counts_by_status(), db.total(), db.channel_breakdown())]

    assert "cancelled" not in codes
    assert "returned" in codes


def test_fedex_is_excluded_from_the_top_status_strip_when_asked(db):
    """Kullanici istegi (2026-08-31): FedEx (olcek COK farkli) GLS'in "ilk 8
    kutucuk" ozetine karismasin."""
    db.add_parcel(tracking_no="38120177032000", channel="NL", consignee_name="c",
                  country="DE", status="delivered")
    db.add_parcel(tracking_no="876365567966", channel="FEDEX", consignee_name="c",
                  country="CA", status="delivered")

    counts = db.counts_by_status(exclude_channels=("FEDEX",))

    assert counts["delivered"] == 1
    assert sum(counts.values()) == 1


def test_counts_by_status_includes_everything_by_default(db):
    """Baska cagiranlar (rapor sayfasi vb.) TUM kanallari gormeye devam eder —
    disari birakma yalnizca ACIKCA istenince olur."""
    db.add_parcel(tracking_no="38120177032001", channel="NL", consignee_name="c",
                  country="DE", status="delivered")
    db.add_parcel(tracking_no="876365567967", channel="FEDEX", consignee_name="c",
                  country="CA", status="delivered")

    assert db.counts_by_status()["delivered"] == 2


def test_a_dashboard_request_does_not_count_fedex_in_the_flow(db, monkeypatch):
    """Uctan uca: `/` yaniti FedEx'i ust seritte SAYMAMALI."""
    from fastapi import Request as _Request
    from web.main import app

    db.add_parcel(tracking_no="38120177032002", channel="NL", consignee_name="c",
                  country="DE", status="delivered")
    db.add_parcel(tracking_no="876365567968", channel="FEDEX", consignee_name="c",
                  country="CA", status="delivered")
    monkeypatch.setattr(deps, "_shipments_db", db)

    async def fake_user(request: _Request):
        request.state.user = {"uid": 1, "username": "k", "role": "admin"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    try:
        resp = TestClient(app).get("/")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200


# ------------------------------------------------------- kanal kirilimi

def test_fedex_gets_its_own_bucket_not_merged_into_nl(db):
    """Kullanicinin ilk bulgusu (2026-08-31): FedEx bilinmeyen kanal sayilip
    "NL"ye sessizce karisiyordu — kendi kutusu olmadigi icin filtrede de hic
    gorunmuyordu."""
    db.add_parcel(tracking_no="38120177032010", channel="NL", consignee_name="c",
                  country="DE", status="delivered")
    db.add_parcel(tracking_no="876365567969", channel="FEDEX", consignee_name="c",
                  country="CA", status="delivered")

    breakdown = db.channel_breakdown()

    assert breakdown["NL"]["delivered"] == 1
    assert breakdown["FEDEX"]["delivered"] == 1


def test_a_truly_unknown_channel_still_falls_back_to_nl(db):
    """Guvenlik agi korunur: NL/IE/FEDEX disinda bir deger gelirse (beklenmez)
    hala NL'e dusuyor — sessizce kaybolmuyor."""
    db.add_parcel(tracking_no="99999999999999", channel="UPS", consignee_name="c",
                  country="DE", status="delivered")

    assert db.channel_breakdown()["NL"]["delivered"] == 1


def test_an_empty_database_does_not_divide_by_zero():
    """Yuzde hesabi payda 0 iken 0.0 doner."""
    from web.routers.dashboard import _status_flow

    flow = _status_flow({}, 0, {})
    assert all(f["pct"] == 0.0 for f in flow)
    assert all(f["count"] == 0 for f in flow)


def test_returned_status_box_is_always_drawn(client):
    html = client.get("/").text

    assert "/tracking?status=returned" in html
    assert "İade" in html or "Returned" in html


def test_the_cards_say_what_they_measure_in_their_titles(client, db):
    """Uc ayri "teslim" sayisi yan yana duruyor ve olcutu yazmiyordu.

    Kartlarin ALTINDAKI aciklama satirlari kullanici istegiyle kaldirildi, bu
    yuzden olcut basligin kendisinde olmali — yoksa ayrim yine kaybolur.
    """
    html = client.get("/").text

    assert "Sevk Edilen" in i18n.t("dash.week", "tr")
    assert i18n.t("dash.week", "tr") in html
    assert i18n.t("dash.delivered_today", "tr") in html


def test_the_dead_weight_line_is_gone(client, db):
    """986 kaydin hicbirinde `weight_kg` yok; kart hep "0 kg" yaziyordu."""
    html = client.get("/").text

    # Yalnizca ust serit: sayfanin geri kalaninda (CDN adresleri dahil) "kg"
    # gecen baska metinler var, ham arama yanlis alarm verirdi.
    strip = html.split(i18n.t("dash.week", "tr"))[1].split("quick-search")[0]
    assert " kg" not in strip


# -------------------------------------------------- teslimat sureleri grafigi

def test_the_delivery_chart_is_hidden_without_data(client, db):
    """Hic teslim edilmis 38120177 kolisi yoksa grafik kartinin hic cizilmemesi
    gerekir — bos bir canvas gormek yerine."""
    html = client.get("/").text

    assert 'id="chart-delivery"' not in html


def test_the_delivery_chart_renders_with_data(client, db):
    """Kullanici istegi (2026-08-31): "dashbordun altinda grafik tarzi bir sey"."""
    db.add_parcel(tracking_no="38120177040001", channel="NL", consignee_name="c",
                  country="FR", status="delivered", shipment_date="2026-08-10")
    db.update_tracking_info("38120177040001", handed_over_at="2026-08-20T09:00:00Z",
                            delivered_date="2026-08-21T09:00:00Z")

    html = client.get("/").text

    assert 'id="chart-delivery"' in html
    assert i18n.t("dash.delivery_chart_title", "tr") in html
    assert '"name": "FR"' in html or '"FR"' in html


# -------------------------------------------------------- ParcelShop uyarisi

def test_parcel_shop_box_says_so_when_nothing_is_at_risk(client, db):
    """Baslikta/metinde kesme isareti var — Jinja autoescape onu `&#39;`e
    cevirir, bu yuzden kiyaslama kesme isaretsiz alt dizelerle yapilir."""
    html = client.get("/").text

    assert "Bekleyen Kargolar" in html
    assert "riskli koli yok" in html


def test_parcel_shop_box_lists_at_risk_parcels(client, db):
    """Kullanici istegi (2026-08-31): "dashboarda delivered to parcel shop
    uyarısı olan ayrı bir kutu ekleyelim"."""
    db.add_parcel(tracking_no="38120177050001", channel="NL", consignee_name="c",
                  country="ES", status="out_for_delivery")
    db.update_status("38120177050001", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")
    db.update_status("38120177050001", "exception",
                     note="The parcel has reached the maximum storage time in the ParcelShop.")

    html = client.get("/").text

    assert "38120177050001" in html
    assert '/tracking?search=38120177050001' in html
    assert "/reports/parcel-shop" in html


def test_parcel_shop_box_excludes_collected_and_returned_ones(client, db):
    """Sadece RISKTEKI (henuz alinmamis/iadeye donmemis) koliler gorunur —
    basariyla teslim alinan bu kutuda GORUNMEMELI (baska bir kartta —
    "Bugün Teslim Edilen" — haklı olarak gorunuyor, bu yuzden kontrol
    PARCELSHOP PANELINE ozel bir dilime daralttiriliyor)."""
    db.add_parcel(tracking_no="38120177050002", channel="NL", consignee_name="c",
                  country="ES", status="out_for_delivery")
    db.update_status("38120177050002", "delivered",
                     note="The parcel has been delivered at the ParcelShop (see ParcelShop information).")

    html = client.get("/").text
    panel = html[html.index("Bekleyen Kargolar"):][:1500]

    assert "38120177050002" not in panel
    assert "riskli koli yok" in panel


def test_every_card_carries_its_percentage(client, db):
    """Kart altindaki tek satir yuzde: paydayi serit basindaki "Toplam" verir."""
    db.add_parcel(tracking_no="38120177033000", channel="NL", consignee_name="c",
                  country="DE", status="delivered")

    html = client.get("/").text

    assert i18n.t("dash.pct_of_total", "tr", pct=100.0) in html
    assert i18n.t("dash.total_parcels", "tr", count=1) in html


# ---------------------------------------------- WhatsApp oturum seridi
# Kullanici karari (2026-09-01): "admin gorsun".

def test_the_whatsapp_banner_is_shown_to_an_admin(client, db, monkeypatch):
    from notify import whatsapp as wa
    monkeypatch.setattr(wa, "session_status",
                        lambda: {"ok": False, "status": "qr_ready", "detail": ""})

    html = client.get("/").text

    assert "qr_ready" in html
    assert i18n.t("alert.whatsapp.banner", "tr") in html


def test_the_whatsapp_banner_is_hidden_when_connected(client, db, monkeypatch):
    from notify import whatsapp as wa
    monkeypatch.setattr(wa, "session_status",
                        lambda: {"ok": True, "status": "connected", "detail": ""})

    assert i18n.t("alert.whatsapp.banner", "tr") not in client.get("/").text


def test_a_viewer_does_not_see_the_whatsapp_banner(db, monkeypatch):
    """Cozum OpenWA'da QR okutmak — operatorun/viewer'in elinde degil."""
    from fastapi import Request as _Request
    from notify import whatsapp as wa
    from web.main import app

    monkeypatch.setattr(deps, "_shipments_db", db)
    monkeypatch.setattr(wa, "session_status",
                        lambda: {"ok": False, "status": "qr_ready", "detail": ""})

    async def fake_viewer(request: _Request):
        request.state.user = {"uid": 2, "username": "v", "role": "viewer"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_viewer
    try:
        html = TestClient(app).get("/").text
    finally:
        app.dependency_overrides.clear()

    assert i18n.t("alert.whatsapp.banner", "tr") not in html


def test_the_database_waits_for_a_lock_instead_of_failing_fast(tmp_path):
    """SQLite varsayilani 5 sn ve bu sistemde YETMIYOR: tarayici tur atarken
    ERP senkronu ayni dosyaya yaziyor. Canli olcum (2026-09-01): turlar ust
    uste "database is locked" ile dustu, hatayi kaydeden `record_tick` bile."""
    from tracking.db import BUSY_TIMEOUT_SECONDS, ShipmentsDB

    d = ShipmentsDB(path=tmp_path / "busy.db")
    got = d.conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert BUSY_TIMEOUT_SECONDS >= 30
    assert got == BUSY_TIMEOUT_SECONDS * 1000


def test_a_second_writer_is_not_rejected_immediately(tmp_path):
    """Iki ayri baglanti ayni dosyaya yazabilmeli (biri kisa sure bekleyerek)."""
    from tracking.db import ShipmentsDB

    a = ShipmentsDB(path=tmp_path / "two.db")
    b = ShipmentsDB(path=tmp_path / "two.db")
    a.add_parcel("38120177000900", "NL", consignee_name="c", country="FR")
    b.add_parcel("38120177000901", "NL", consignee_name="c", country="FR")

    assert a.get("38120177000901") is not None
