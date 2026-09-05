# -*- coding: utf-8 -*-
"""Giristen sonra kullanicinin nereye dustugu.

Oturum 20 dakikada zaman asimina ugruyor. Kullanici bir sevkiyati incelerken
disari atilip giris yaptiginda hep panoya donuyordu — aradigi sayfayi bastan
bulmasi gerekiyordu. Adres cubugunda `next` YAZIYORDU ama kimse okumuyordu.

`next` kullanicidan geldigi icin serbest birakilamaz: giris sayfamiz kimlik
avinda basamak olarak kullanilabilirdi.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.db import AuthDB
from auth.security import hash_password
from web import deps
from web.routers import auth as auth_router

SIFRE = "S3cret!23"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = AuthDB(path=tmp_path / "auth.db")
    db.create_user("zeynep", hash_password(SIFRE), role="admin")
    monkeypatch.setattr(deps, "_auth_db", db)

    app = FastAPI()
    app.include_router(auth_router.router)
    return TestClient(app, follow_redirects=False)


def _login(client, **extra):
    return client.post("/auth/login",
                       data={"username": "zeynep", "password": SIFRE, **extra})


def test_the_user_returns_to_the_page_they_were_kicked_out_of(client):
    response = _login(client, next="/tracking")

    assert response.headers["location"] == "/tracking"


def test_without_a_target_the_user_lands_on_the_dashboard(client):
    assert _login(client).headers["location"] == "/"


@pytest.mark.parametrize("hedef", [
    "https://kotu.site/giris",   # tam adres
    "//kotu.site/giris",         # sema-siz adres — tarayici DIS sayar
])
def test_an_outside_address_is_refused(client, hedef):
    """Aksi halde giris sayfamiz kimlik avinda basamak olurdu."""
    assert _login(client, next=hedef).headers["location"] == "/"


def test_the_target_survives_a_failed_attempt(client):
    """Sifreyi yanlis yazan kullanici hedefini kaybetmemeli."""
    response = client.post("/auth/login",
                           data={"username": "zeynep", "password": "yanlis",
                                 "next": "/tracking"})

    assert response.status_code == 401
    assert 'value="/tracking"' in response.text


# ---------------------------------------------------------------- oturum bitisi

def _app_client() -> TestClient:
    from web.main import app
    return TestClient(app, follow_redirects=False)


def test_an_expired_session_sends_the_browser_to_the_login_page():
    response = _app_client().get("/tracking")

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login?next=/tracking"


def test_an_expired_session_does_not_paint_the_login_form_into_a_table():
    """HTMX kismi istekleri 303'u KENDI izler ve donen giris sayfasini hedef
    alana basar: tablonun ortasinda kucuk bir giris formu belirir, sayfanin
    geri kalani eski haliyle durur (kullanici ekran goruntusu, 2026-08-18).
    `HX-Redirect` govdeyi degil tarayiciyi tasir."""
    response = _app_client().get("/tracking/rows", headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert "<form" not in response.text


def test_the_user_returns_to_a_page_not_to_a_fragment():
    """Kismi adrese (`/tracking/rows`) donen kullanici sablonsuz ham `<tr>`
    yigini goruyordu: beyaz ekranda alt alta takip numaralari (kullanici ekran
    goruntusu, 2026-08-18). Tarayicinin durdugu SAYFA `HX-Current-URL`de."""
    response = _app_client().get(
        "/tracking/rows",
        headers={"HX-Request": "true",
                 "HX-Current-URL": "http://testserver/tracking"})

    assert response.headers["hx-redirect"] == "/auth/login?next=/tracking"


def test_the_filters_the_user_had_open_survive_the_login():
    """Suzgecler sorgu dizesinde; korunmazsa kullanici aramaya bastan baslar."""
    response = _app_client().get(
        "/tracking/rows",
        headers={"HX-Request": "true",
                 "HX-Current-URL": "http://testserver/tracking?f_country=DE"})

    assert response.headers["hx-redirect"] == "/auth/login?next=/tracking%3Ff_country%3DDE"


@pytest.mark.parametrize("sahte", [
    "//kotu.site/giris",             # sema-siz: yalnizca yola bakan kural `/giris` derdi
    "https://kotu.site/tracking",    # tam adres
])
def test_a_forged_current_url_is_ignored(sahte):
    """Baslik da istekle gelir — yani kullanici girdisidir, guvenilmez.

    Baska konak adi gorulurse yolu da atilir ve istegin kendi adresine donulur.
    """
    response = _app_client().get(
        "/tracking/rows",
        headers={"HX-Request": "true", "HX-Current-URL": sahte})

    assert response.headers["hx-redirect"] == "/auth/login?next=/tracking/rows"
