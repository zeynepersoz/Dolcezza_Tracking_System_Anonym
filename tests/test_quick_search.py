# -*- coding: utf-8 -*-
"""Paneldeki hizli arama — magaza kodundan kargoya tek adimda.

Avrupa ofisi takip numarasini ezbere bilmiyor, MAGAZA KODUNU biliyor
("AHAAT01"). Eskiden yol suydu: panel -> takip sayfasi -> dogru sutunun huni
menusunu ac -> yaz. Ustelik genel arama kutusu magaza koduna HIC bakmiyordu
(yalnizca takip no / referans / alici adi), yani kod yazan kullanici bos sonuc
alip aramanin bozuk oldugunu saniyordu.

Buradaki testler iki seyi kanitlar: aramanin dogru kolonlara baktigini ve
panelin bos kutuyla tum listeyi ekrana yikmadigini.
"""
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from tracking.db import ShipmentsDB
from web import deps

TN = "38120177000001"


@pytest.fixture
def db(tmp_path, monkeypatch):
    s = ShipmentsDB(path=tmp_path / "s.db")
    monkeypatch.setattr(deps, "_shipments_db", s)

    s.add_parcel(TN, "NL", store_code="AHAAT01", invoice_number="5000402",
                 consignee_name="Ahaa Wien", country="AT")
    s.add_parcel("38120177000002", "NL", store_code="WNPAT1N", invoice_number="5000419",
                 consignee_name="Jacadi", country="AT")
    return s


@pytest.fixture
def client(db):
    from web.main import app

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "avrupa", "role": "viewer"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- arama alanlari

@pytest.mark.parametrize("terim", ["AHAAT01", "5000402", TN, "Ahaa"])
def test_the_search_finds_the_parcel_by_anything_the_user_knows(db, terim):
    """Magaza kodu ve fatura no SONRADAN eklendi; ikisi de kullanicinin elindeki
    tek bilgi olabiliyor."""
    found = db.list_parcels(search=terim)

    assert [p["tracking_no"] for p in found] == [TN]


def test_the_search_still_excludes_what_does_not_match(db):
    assert db.list_parcels(search="AHAAT01") != db.list_parcels()


# ---------------------------------------------------------------- panel ucu

def test_an_empty_box_returns_no_rows(client):
    """Kutu bosken tum liste gelseydi panel acilir acilmaz 1000 satir yiginla
    dolardi — arama sonucu ile "hic aranmadi" ayni sey degil."""
    html = client.get("/tracking/quick").text

    assert TN not in html
    assert "38120177000002" not in html


def test_typing_a_store_code_brings_the_parcel(client):
    html = client.get("/tracking/quick", params={"search": "AHAAT01"}).text

    assert TN in html
    assert "38120177000002" not in html


def test_a_result_row_opens_the_detail_panel(client):
    """Satira tiklayinca yandaki panel acilmali: hedef `#detail-drawer`,
    takip sayfasiyla AYNI uc ve AYNI panel."""
    html = client.get("/tracking/quick", params={"search": "AHAAT01"}).text

    assert f'hx-get="/tracking/detail/{TN}"' in html
    assert 'hx-target="#detail-drawer"' in html


def test_the_dashboard_carries_the_box_and_the_drawer(client):
    """Kutu paneldeyse detay paneli de orada olmali; biri olmadan digeri
    tiklamayi sessizce yutar."""
    html = client.get("/").text

    assert 'hx-get="/tracking/quick"' in html
    assert 'id="detail-drawer"' in html


def test_the_box_is_there_for_every_role(client, db):
    """Kullanici istegi: yalnizca `viewer` icin degil, herkes icin."""
    from web.main import app

    for role in ("admin", "operator", "viewer"):
        async def fake_user(request: Request, role=role):
            request.state.user = {"uid": 1, "username": "k", "role": role}
            return request.state.user

        app.dependency_overrides[get_current_user] = fake_user
        assert 'hx-get="/tracking/quick"' in TestClient(app).get("/").text
