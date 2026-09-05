# -*- coding: utf-8 -*-
"""`viewer` rolu — Avrupa ofisi icin salt okuma hesabi.

Avrupa'daki kullanicilar kargo takibi, POD ve sorunlu kolilere bakacak; etiket
uretimi, sevkiyat, adres defteri ve ayarlar onlarin isi degil. Rol matrisi
`auth/access.py`dedir; buradaki testler o matrisin GERCEKTEN uygulandigini
kanitlar.

Iki ayri tuzak var ve ikisi de test ediliyor:
1. Menude olmayan sayfanin URL'i CALISMAYA devam etmesi (gizlemek koruma degil).
2. Sayfanin acik olup icindeki yazma ucunun da acik kalmasi — `/tracking`
   `viewer`a acik ama "tara / senkron / yukle / iptal" uclari degil.
"""
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from auth.db import ROLES, AuthDB
from auth.dependencies import get_current_user
from tracking.db import ShipmentsDB
from web import deps

TN = "38120177000001"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_shipments_db", ShipmentsDB(path=tmp_path / "t.db"))
    from web.main import app as fastapi_app
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


def client_as(app, role: str) -> TestClient:
    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "avrupa",
                              "full_name": "Avrupa Ofisi", "role": role}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app, follow_redirects=False)


# ------------------------------------------------------------------ gorebildikleri

@pytest.mark.parametrize("yol", [
    "/", "/tracking", "/tracking/rows", "/pod", "/problems", "/archive",
])
def test_the_viewer_sees_the_tracking_side(app, yol):
    assert client_as(app, "viewer").get(yol).status_code == 200


# ------------------------------------------------------------------ goremedikleri

@pytest.mark.parametrize("yol", [
    "/labels", "/dispatch", "/settings",
])
def test_the_viewer_cannot_open_the_label_side(app, yol):
    """Menude yoklar; URL elle yazilinca da acilmamalilar."""
    assert client_as(app, "viewer").get(yol).status_code == 403


@pytest.mark.parametrize("yol", [f"/archive?kind=label", f"/archive/label/{TN}"])
def test_the_viewer_cannot_reach_the_label_branch_of_the_archive(app, yol):
    """Kullanici karari: `viewer` Belge Arsivi'nde yalnizca PODlari gorur."""
    assert client_as(app, "viewer").get(yol).status_code == 403


def test_the_archive_opens_straight_on_the_pods_for_a_viewer(app):
    """Tek dal kalinca kok seviye (tek kartlik klasor ekrani) anlamsiz olur."""
    html = client_as(app, "viewer").get("/archive").text

    assert "kind=label" not in html


# ------------------------------------------------------------------ yazma uclari

def test_the_viewer_cannot_trigger_a_scan(app):
    assert client_as(app, "viewer").post("/tracking/refresh").status_code == 403


def test_the_viewer_cannot_sync_the_erp(app):
    assert client_as(app, "viewer").post("/tracking/sync-mssql").status_code == 403


def test_the_viewer_cannot_upload_a_tracking_list(app):
    response = client_as(app, "viewer").post(
        "/tracking/upload", files={"file": ("liste.xlsx", b"x")})

    assert response.status_code == 403


def test_the_viewer_cannot_cancel_a_label(app):
    """En pahali uc: GLS'te GERI ALINAMAZ bir silme yapar."""
    assert client_as(app, "viewer").post(f"/tracking/cancel/{TN}").status_code == 403


# ------------------------------------------------------------------ menu

def test_the_menu_hides_what_the_viewer_cannot_open(app):
    html = client_as(app, "viewer").get("/tracking").text

    for yol in ("/labels", "/dispatch", "/settings"):
        assert f'href="{yol}"' not in html
    assert 'href="/tracking"' in html and 'href="/archive"' in html


def test_the_viewer_gets_no_write_buttons_on_the_tracking_page(app):
    """Gorunup tiklayinca 403 veren dugme, olmayan dugmeden kotudur."""
    html = client_as(app, "viewer").get("/tracking").text

    assert "/tracking/refresh" not in html
    assert "/tracking/upload" not in html
    assert "/tracking/sync-mssql" not in html


# ------------------------------------------------------------------ regresyon

@pytest.mark.parametrize("yol", ["/", "/tracking", "/pod", "/problems",
                                 "/archive", "/labels"])
def test_the_operator_keeps_everything_they_had(app, yol):
    assert client_as(app, "operator").get(yol).status_code == 200


def test_the_operator_still_sees_the_label_branch_of_the_archive(app):
    assert "kind=label" in client_as(app, "operator").get("/archive").text


# ------------------------------------------------------------------ hesap acma

@pytest.mark.parametrize("rol", ROLES)
def test_every_role_on_the_screen_can_actually_be_created(app, tmp_path, monkeypatch, rol):
    """Rol listesi UC yerde yaziliydi; biri (`auth.py:create_user`) guncellenmeyince
    `viewer` ekranda secilebiliyor ama kayitta "Gecersiz rol" veriyordu — yani
    canlida hic olusturulamiyordu. Ekranin sundugu her rol kabul EDILMELI."""
    monkeypatch.setattr(deps, "_auth_db", AuthDB(path=tmp_path / "auth.db"))

    response = client_as(app, "admin").post("/auth/users/create", data={
        "username": f"kul_{rol}", "password": "GecerliParola1", "role": rol,
    })

    assert response.status_code == 303
    assert deps.get_auth_db().get_user_by_username(f"kul_{rol}")["role"] == rol
