# -*- coding: utf-8 -*-
"""Takip listesi suzgecleri.

Sol panel ve Excel tarzi ust huni menusu AYNI parametre adlarini kullanir
(`status`, `channel`, `f_<alan>`); ust menu coklu secim yaptigi icin ayni ad
birden fazla kez gelebilir. Burada test edilen sey budur.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracking.db import ShipmentsDB
from web import deps
from web.routers import tracking as tracking_router


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = ShipmentsDB(path=tmp_path / "shipments.db")
    d.add_parcel(tracking_no="38120177000001", channel="NL", status="delivered",
                 consignee_name="AHA Austria", country="AT")
    d.add_parcel(tracking_no="38120177000002", channel="NL", status="in_transit",
                 consignee_name="Bella Germany", country="DE")
    d.add_parcel(tracking_no="21569700003", channel="IE", status="created",
                 consignee_name="Cara Ireland", country="IE")
    d.add_parcel(tracking_no="876365567966", channel="FEDEX", status="out_for_delivery",
                 consignee_name="Dana Canada", country="CA")
    monkeypatch.setattr(deps, "_shipments_db", d)
    return d


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(tracking_router.router)
    return TestClient(app)


# ---------------------------------------------------------------- veri katmani
def test_single_status_still_works(db):
    """Eski tek degerli baglantilar (?status=delivered) bozulmamali."""
    assert len(db.list_parcels(status="delivered")) == 1


def test_several_statuses_are_ored(db):
    rows = db.list_parcels(status=["delivered", "in_transit"])
    assert {r["tracking_no"] for r in rows} == {"38120177000001", "38120177000002"}


def test_empty_list_means_no_filter(db):
    assert len(db.list_parcels(status=[])) == 4


def test_blank_values_are_ignored(db):
    """Sol paneldeki "Durum seç" secenegi bos string yollar — suzgec sayilmamali."""
    assert len(db.list_parcels(status=[""], channel=[""])) == 4


def test_fedex_channel_filters_correctly(db):
    """Kullanici istegi (2026-08-31): FedEx filtrede parametrik olmali ve calismali."""
    rows = db.list_parcels(channel=["FEDEX"])
    assert [r["tracking_no"] for r in rows] == ["876365567966"]


def test_status_and_channel_are_anded(db):
    rows = db.list_parcels(status=["delivered", "in_transit"], channel=["NL"])
    assert {r["tracking_no"] for r in rows} == {"38120177000001", "38120177000002"}


# ---------------------------------------------------------------- uc noktalar
def test_rows_endpoint_accepts_repeated_status(client):
    body = client.get("/tracking/rows?status=delivered&status=in_transit").text
    assert "38120177000001" in body
    assert "38120177000002" in body
    assert "21569700003" not in body


def test_page_renders_with_counts_in_the_header_menu(client):
    """Huni menusundeki sayilar sablonun render edildiginin de kanitidir."""
    body = client.get("/tracking").text
    assert 'name="status" value="delivered"' in body
    assert 'name="channel" value="NL"' in body


def test_fedex_appears_as_a_channel_option_in_the_menu(client):
    """Onceden FedEx menude HIC yoktu (yalnizca NL/IE hardcode edilmisti) —
    kanal secilebilir olsa bile filtreye giremiyordu."""
    body = client.get("/tracking").text
    assert 'name="channel" value="FEDEX"' in body


def test_checked_boxes_survive_a_page_reload(client):
    body = client.get("/tracking?status=delivered&status=created").text
    assert 'value="delivered" checked' in body
    assert 'value="created" checked' in body
    assert 'value="in_transit" checked' not in body


def test_duplicate_text_filter_takes_the_non_empty_one(client):
    """Ayni alan iki arayuzden de gelir; biri bos olsa da suzgec calismali."""
    body = client.get("/tracking/rows?f_consignee_name=&f_consignee_name=Bella").text
    assert "38120177000002" in body
    assert "38120177000001" not in body


# ------------------------------------------------------- "bugun teslim edilen"

def test_delivered_day_filters_the_list(db):
    """Gecmis bir gun secilir: fikstürdeki teslimatin `delivered_date`i bostur,
    `_DELIVERY_DAY` onu `updated_at`e (bugune) dusurur."""
    db.add_parcel(tracking_no="38120177000004", channel="NL", status="delivered",
                  consignee_name="Dora", country="FR",
                  delivered_date="2026-01-15T10:00:00Z")

    rows = db.list_parcels(delivered_day="2026-01-15")

    assert [r["tracking_no"] for r in rows] == ["38120177000004"]


def test_an_empty_delivered_day_is_not_a_filter(db):
    assert len(db.list_parcels(delivered_day="")) == 4


def test_the_filter_survives_the_30_second_self_refresh(client):
    """Tablo kendini `hx-include` ile yeniliyor.

    `delivered_day` o listede olmasaydi, kullanici panelden gelip yarim dakika
    bekledikten sonra filtre SESSIZCE kaybolur, ekran tum parcalara donerdi.
    """
    body = client.get("/tracking?delivered_day=2026-08-19").text

    assert "[name='delivered_day']" in body
    assert 'name="delivered_day" value="2026-08-19"' in body


# ---------------------------------------------------------------- POD sutunu

def test_a_delivery_without_a_pod_says_so(client):
    """Teslim edilmis HER satirda yesil "POD" yaziyordu; belgesi olmayanda bag
    404'e goturuyordu. Canlida 379 teslimatin 16'sinin goruntusu GLS'te hic
    olusmadi — operator hangisinin belgesiz oldugunu tiklamadan gormeli."""
    body = client.get("/tracking/rows?status=delivered").text

    assert "text-amber-600" in body        # uyari isareti
    assert "text-emerald-600" not in body  # yesil indirme bagi degil


def test_a_delivery_with_a_pod_keeps_the_download_link(client, db):
    db.set_pod_path("38120177000001", "/tmp/pod.png", "png")

    body = client.get("/tracking/rows?status=delivered").text

    assert "/pod/download/38120177000001" in body
    assert "text-amber-600" not in body


# ======================================================================
# IE: TUM kolileri listede (kullanici 2026-09-03: "bunlar gizli kalmasin")
# ======================================================================
def test_ie_list_shows_every_status(tmp_path):
    """2026-09-02'de "IE'de yalnizca teslim edilenler" suzgeci vardi; kullanici
    2026-09-03'te kaldirtti — GLS'in henuz almadigi koliler de gorunmeli."""
    db = ShipmentsDB(path=tmp_path / "ie.db")
    for tn, st in (("21569979601", "delivered"), ("21569979636", "created"),
                   ("21569979637", "in_transit")):
        db.add_parcel(tracking_no=tn, channel="IE", reference="r",
                      consignee_name="c", country="IE")
        db.update_status(tn, st)

    listed = {p["tracking_no"] for p in db.list_parcels()}
    assert listed == {"21569979601", "21569979636", "21569979637"}
    assert db.count_parcels() == 3


# ======================================================================
# Huni menusu sayaclari DINAMIK (kullanici bildirdi 2026-09-03)
# ======================================================================
# "kanalı fedex seçtik 289 adet ya diğer tarafta delivered sayısı 1000 küsür
# olmasın" — sayaclar tum tablodan geliyordu, aktif suzgeci yok sayiyordu.
def _mixed(tmp_path):
    db = ShipmentsDB(path=tmp_path / "facet.db")
    for i in range(5):                                  # NL delivered
        db.add_parcel(tracking_no=f"3812017700{i:04d}", channel="NL",
                      consignee_name="c", country="DE", status="delivered")
    for i in range(2):                                  # FEDEX delivered
        db.add_parcel(tracking_no=f"87663410{i:04d}", channel="FEDEX",
                      consignee_name="c", country="CA", status="delivered")
    db.add_parcel(tracking_no="876634109999", channel="FEDEX",
                  consignee_name="c", country="CA", status="in_transit")
    return db


def test_status_counts_follow_the_channel_filter(tmp_path):
    db = _mixed(tmp_path)
    # suzgecsiz: 7 teslim (5 NL + 2 FEDEX)
    assert db.facet_counts("status")["delivered"] == 7
    # kanal=FEDEX secilince statu sayaci SADECE FedEx'i saymali
    assert db.facet_counts("status", channel=["FEDEX"])["delivered"] == 2


def test_channel_counts_follow_the_status_filter(tmp_path):
    db = _mixed(tmp_path)
    assert db.facet_counts("channel", status=["delivered"]) == {"NL": 5, "FEDEX": 2}
    assert db.facet_counts("channel", status=["in_transit"]) == {"FEDEX": 1}


def test_the_menu_counts_match_the_visible_list(tmp_path):
    """Sayac ile liste AYNI `_list_where`den gecer — toplamlar birebir tutar."""
    db = _mixed(tmp_path)
    db.add_parcel(tracking_no="21569979636", channel="IE",
                  consignee_name="c", country="IE")
    db.update_status("21569979636", "created")

    assert db.facet_counts("channel")["IE"] == 1        # IE artik gorunur
    assert db.count_parcels() == len(db.list_parcels())
    assert sum(db.facet_counts("channel").values()) == db.count_parcels()


def test_an_unknown_facet_column_is_refused(tmp_path):
    """Sutun adi sorguya METIN olarak giriyor — beyaz liste disi ad reddedilir."""
    db = ShipmentsDB(path=tmp_path / "inj.db")
    with pytest.raises(ValueError):
        db.facet_counts("tracking_no; DROP TABLE parcels")
