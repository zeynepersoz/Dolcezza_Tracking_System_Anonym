# -*- coding: utf-8 -*-
"""Sezon sutunu ve suzgeci + surekli bos kalan sutunlarin ekrandan kalkmasi.

Kullanicinin gerekcesi: "uc gun sonra yeni sezonlar geldiginde karismamasi
lazim". Bu yuzden buradaki testlerin en onemlisi sezonun TAHMIN EDILMEDIGINI
kanitlayandir — tahmin, tam da onlenmek istenen karisikligi uretir.
"""
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from erp.sync import map_row
from tracking.db import ShipmentsDB
from web import deps


@pytest.fixture
def db(tmp_path, monkeypatch):
    s = ShipmentsDB(path=tmp_path / "s.db")
    monkeypatch.setattr(deps, "_shipments_db", s)

    s.add_parcel("38120177000001", "NL", store_code="AHAAT01",
                 consignee_name="Ahaa Wien", country="AT", season="FA26")
    s.add_parcel("38120177000002", "NL", store_code="AHAAT01",
                 consignee_name="Ahaa Wien", country="AT", season="SP27")
    return s


@pytest.fixture
def client(db):
    from web.main import app

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "avrupa", "role": "admin"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


# ------------------------------------------------- sezon nereden geliyor (ERP)

def test_the_season_comes_from_the_erp_field_not_the_source_name():
    """Taban tabloda kaynak adi duz "Erp_Box"tir, sezon kolondadir.

    `config.season_from_source` bu durumda GUNCEL sezona duser; o tahminle
    etiketlenen eski koliler yeni sezon acildiginda yenisiyle karisirdi.
    """
    parcel = map_row({"UD_TrackingNumber": "38120177000001", "UD_Buyer": "AHAAT01",
                      "UD_Season": "SP27", "CustomerCountry": "AT"}, "Erp_Box")

    assert parcel["season"] == "SP27"


def test_a_view_without_the_column_still_gets_its_season_from_its_name():
    """Sezon gorunumlerinde `UD_Season` kolonu yok — ad zaten sezonu soyluyor."""
    parcel = map_row({"UD_TrackingNumber": "38120177000001", "UD_Buyer": "AHAAT01",
                      "CustomerCountry": "AT"}, "Vision_FA26_Boxed_EUROPE")

    assert parcel["season"] == "FA26"


def test_an_unknown_source_leaves_the_season_empty_instead_of_guessing():
    """Bos rozet, YANLIS rozetten iyidir: yanlisi kimse fark etmez."""
    parcel = map_row({"UD_TrackingNumber": "38120177000001", "UD_Buyer": "AHAAT01",
                      "CustomerCountry": "AT"}, "Erp_Box")

    assert parcel["season"] == ""


# ------------------------------------------- kolondan onceki parcalarin sezonu

def test_old_parcels_get_their_season_from_the_source_name_once(tmp_path):
    """Kolon eklendiginde MEVCUT parcalar bos kalir; canlida 994'unun 994'u.

    ERP senkronu bekleyip sezon sutununu bastan sona "—" gostermek yerine goc
    kaynak adindan bir kez doldurur.
    """
    path = tmp_path / "s.db"
    first = ShipmentsDB(path=path)
    first.add_parcel("38120177000005", "NL", source_sheet="Vision_FA26_Boxed_EUROPE")
    first.conn.execute("UPDATE parcels SET season = ''")   # kolon oncesi hâl
    first.conn.commit()
    first.conn.close()

    again = ShipmentsDB(path=path)

    assert again.get("38120177000005")["season"] == "FA26"


def test_a_source_without_a_season_in_its_name_stays_empty(tmp_path):
    """"Erp_Box" sezon soylemez — goc de tahmin etmez."""
    path = tmp_path / "s.db"
    first = ShipmentsDB(path=path)
    first.add_parcel("38120177000006", "NL", source_sheet="Erp_Box")
    first.conn.close()

    assert ShipmentsDB(path=path).get("38120177000006")["season"] == ""


def test_the_backfill_never_overwrites_what_the_erp_said(tmp_path):
    """Kaynak adi ile ERP alani celisirse ERP kazanir (gorunum eski kalabilir)."""
    path = tmp_path / "s.db"
    first = ShipmentsDB(path=path)
    first.add_parcel("38120177000007", "NL", season="SP27",
                     source_sheet="Vision_FA26_Boxed_EUROPE")
    first.conn.close()

    assert ShipmentsDB(path=path).get("38120177000007")["season"] == "SP27"


# -------------------------------------------------------------- sezon suzgeci

def test_the_filter_narrows_the_list_to_one_season(db):
    found = db.list_parcels(season="SP27")

    assert [p["tracking_no"] for p in found] == ["38120177000002"]


def test_the_filter_options_are_newest_first(client):
    """Gecis doneminde iki sezon birden acik; aranan hemen her zaman yenisi.

    Duz alfabetik siralama "FA26" < "SP27" verir ve dogru gorunur, bu yuzden
    olcut ayni yildaki iki sezonla kurulmali (FA sonbahardir, SP'den SONRA).
    """
    from web.routers.tracking import season_options

    db = deps.get_shipments_db()
    db.add_parcel("38120177000003", "NL", season="SP26")

    assert [code for code, _, _, _ in season_options(db)] == ["SP27", "FA26", "SP26"]


# -------------------------------------------------- paneldeki hizli arama kutusu

def test_choosing_a_season_alone_is_enough_to_search(client):
    """Kutuya bir sey yazmadan sezon secmek "bu sezonu goster" demektir.

    Onceden arama metni bostu diye uc hic sorgu yapmadan bos donuyordu: sezon
    secilir, ekranda hicbir sey olmazdi.
    """
    html = client.get("/tracking/quick", params={"season": "SP27"}).text

    assert "38120177000002" in html and "38120177000001" not in html


def test_an_untouched_panel_still_shows_no_rows(client):
    """Sezon de arama da bossa panel 1000 satirla dolmamali."""
    assert "38120177000001" not in client.get("/tracking/quick").text


def test_searching_without_a_season_does_not_hide_everything(client):
    """Bos sezon "secim yok" demek; `season = ''` diye SORULMAMALI."""
    html = client.get("/tracking/quick", params={"search": "AHAAT01"}).text

    assert "38120177000001" in html and "38120177000002" in html


# ------------------------------------------------------------ ekrandaki sutunlar

def test_the_store_code_gets_its_own_column(client):
    """Magaza kodu fatura numarasinin altinda kucuk gri bir alt satirdi; Avrupa
    ofisi koliyi ONUNLA ariyor."""
    html = client.get("/tracking").text

    assert "AHAAT01" in html
    assert '>Mağaza Kodu<' in html or "Store Code" in html


def test_the_season_badge_is_on_the_row(client):
    assert "FA26" in client.get("/tracking").text


@pytest.mark.parametrize("baslik", ["Satış Temsilcisi", "Ağırlık"])
def test_the_always_empty_columns_are_gone(client, baslik):
    """Satis temsilcisi ve agirlik ekrandan kalkti; alanlar veritabaninda DURUYOR."""
    assert baslik not in client.get("/tracking").text


def test_shipment_method_is_back_on_tracking_screen(client):
    """Kullanici talebi: Sevkiyat yontemi (1st Truck Shipment vs.) geri getirildi."""
    html = client.get("/tracking").text
    assert "Sevkiyat Yöntemi" in html or "Shipment Method" in html


def test_the_data_behind_the_removed_columns_is_still_stored(db):
    """"Arka planda kalabilir, ekrana gelmesin" — sutun kalkti, alan kalkmadi."""
    db.add_parcel("38120177000004", "NL", sales_rep="Zeynep", weight_kg=4.2)

    parcel = db.get("38120177000004")
    assert parcel["sales_rep"] == "Zeynep" and parcel["weight_kg"] == 4.2
