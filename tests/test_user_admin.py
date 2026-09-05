# -*- coding: utf-8 -*-
"""Yoneticinin kullanici yonetimi: e-posta, rol degistirme, silme, sifre atama.

Bu uclar paneldeki EN yikici islemleri yapiyor (hesap silmek geri alinamaz,
rol dusurmek birini disarida birakabilir). Testlerin cogu bu yuzden "islem
oldu mu"dan cok "olmamasi gerektigi yerde durduruldu mu" sorusunu kovaliyor —
ozellikle son yonetici korumasini: o kirilirsa panel kalici olarak
yonetilemez hale gelir ve geri donus yolu yoktur.

Calistirma: pytest -q
"""
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from auth import ratelimit, security
from auth.db import AuthDB
from auth.dependencies import get_current_user
from web import deps
from web.routers import auth as auth_router

SIFRE = "S3cret!23"
YENI = "Yeni!Sifre123"


@pytest.fixture
def db(tmp_path, monkeypatch):
    ratelimit.reset_all()
    d = AuthDB(path=tmp_path / "auth.db")
    # uid 1 = testlerdeki oturum sahibi yonetici
    d.create_user("yonetici", security.hash_password(SIFRE), role="admin",
                  email="yonetici@dolcezza.com.tr")
    monkeypatch.setattr(deps, "_auth_db", d)
    return d


def _client(role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(auth_router.router)

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "yonetici", "role": role}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app, follow_redirects=False)


def _ekle(db, username="ayse", role="operator", email="ayse@dolcezza.com.tr") -> int:
    return db.create_user(username, security.hash_password(SIFRE), role=role,
                          email=email)


# ---------------------------------------------------------------- e-posta

def test_kullanici_e_postasiyla_olusturulur(db):
    _client().post("/auth/users/create", data={
        "username": "ayse", "password": SIFRE, "role": "operator",
        "full_name": "Ayşe Y.", "email": "Ayse@Dolcezza.com.TR"})

    assert db.get_user_by_username("ayse")["email"] == "ayse@dolcezza.com.tr"


def test_e_posta_bos_birakilabilir(db):
    """Depo terminali gibi paylasimli hesaplarin adresi olmayabilir."""
    response = _client().post("/auth/users/create", data={
        "username": "depo", "password": SIFRE, "role": "operator", "email": ""})

    assert response.status_code == 303
    assert db.get_user_by_username("depo")["email"] == ""


@pytest.mark.parametrize("bozuk", ["ayse", "@dolcezza.com", "ayse@", "a b@c.com"])
def test_bozuk_e_posta_reddedilir(db, bozuk):
    """Yanlis yazilan adres sessizce kabul edilseydi, kullanici bunu ancak
    sifresini unuttugunda — yani en kotu anda — ogrenirdi."""
    response = _client().post("/auth/users/create", data={
        "username": "ayse", "password": SIFRE, "role": "operator", "email": bozuk})

    assert response.status_code == 400
    assert db.get_user_by_username("ayse") is None


def test_e_posta_listede_gorunur(db):
    _ekle(db)

    kayit = next(u for u in db.list_users() if u["username"] == "ayse")

    assert kayit["email"] == "ayse@dolcezza.com.tr"


def test_e_posta_guncellenir(db):
    uid = _ekle(db)

    _client().post(f"/auth/users/{uid}/update", data={
        "full_name": "Ayşe Y.", "email": "yeni@dolcezza.com.tr", "role": "operator"})

    assert db.get_user(uid)["email"] == "yeni@dolcezza.com.tr"


# ---------------------------------------------------------------- rol degistirme

def test_rol_degistirilir(db):
    uid = _ekle(db, role="operator")

    _client().post(f"/auth/users/{uid}/update", data={
        "full_name": "", "email": "", "role": "viewer"})

    assert db.get_user(uid)["role"] == "viewer"


def test_rol_degisince_acik_oturumlar_duser(db):
    """Yetki imzali cerezin ICINDE. Oturum dusurulmezse kullanici en fazla
    `AUTH_SESSION_MINUTES` boyunca eski roluyle gezmeye devam eder."""
    uid = _ekle(db)
    _, ozet, expires = security.new_refresh_token()
    db.store_refresh_token(uid, ozet, expires)

    _client().post(f"/auth/users/{uid}/update", data={
        "full_name": "", "email": "", "role": "viewer"})

    assert db.get_refresh_token(ozet)["revoked"] == 1


def test_rol_degismediyse_oturum_dusmez(db):
    """Sadece adi duzeltilen kullanici panelden atilmamali."""
    uid = _ekle(db)
    _, ozet, expires = security.new_refresh_token()
    db.store_refresh_token(uid, ozet, expires)

    _client().post(f"/auth/users/{uid}/update", data={
        "full_name": "Ayşe Yılmaz", "email": "ayse@dolcezza.com.tr",
        "role": "operator"})

    assert db.get_refresh_token(ozet)["revoked"] == 0


def test_gecersiz_rol_reddedilir(db):
    uid = _ekle(db)

    response = _client().post(f"/auth/users/{uid}/update", data={
        "full_name": "", "email": "", "role": "superadmin"})

    assert response.status_code == 400
    assert db.get_user(uid)["role"] == "operator"


def test_yonetici_kendi_rolunu_degistiremez(db):
    """Kendi kapisini kilitleyen yoneticiyi ancak BASKA bir admin kurtarabilir."""
    response = _client().post("/auth/users/1/update", data={
        "full_name": "", "email": "", "role": "viewer"})

    assert response.status_code == 400
    assert db.get_user(1)["role"] == "admin"


def test_son_yoneticinin_rolu_dusurulemez(db):
    ikinci = _ekle(db, "admin2", role="admin")
    db.set_active(1, False)          # tek aktif admin: ikinci
    yonetici = _client()
    yonetici.app.dependency_overrides.clear()

    async def sahte(request: Request):
        request.state.user = {"uid": 99, "username": "baska", "role": "admin"}
        return request.state.user

    yonetici.app.dependency_overrides[get_current_user] = sahte

    response = yonetici.post(f"/auth/users/{ikinci}/update", data={
        "full_name": "", "email": "", "role": "viewer"})

    assert response.status_code == 400
    assert db.get_user(ikinci)["role"] == "admin"


# ---------------------------------------------------------------- silme

def test_kullanici_tamamen_silinir(db):
    uid = _ekle(db)

    _client().post(f"/auth/users/{uid}/delete")

    assert db.get_user(uid) is None


def test_silinen_kullanicinin_oturumlari_da_gider(db):
    """`refresh_tokens` CASCADE ile temizlenmeli; kalan bir satir silinmis
    hesabin oturumunu ayakta tutardi."""
    uid = _ekle(db)
    _, ozet, expires = security.new_refresh_token()
    db.store_refresh_token(uid, ozet, expires)

    _client().post(f"/auth/users/{uid}/delete")

    assert db.get_refresh_token(ozet) is None


def test_silinen_kullanicinin_sifirlama_baglantilari_da_gider(db):
    uid = _ekle(db)
    _, ozet, expires = security.new_reset_token()
    db.create_password_reset(uid, ozet, expires)

    _client().post(f"/auth/users/{uid}/delete")

    assert db.get_password_reset(ozet) is None


def test_giris_kaydi_silinmez(db):
    """Denetim izi bilerek KALIR: 'kim ne zaman girdi' sorusunun cevabi,
    hesabi silmekle ortadan kalkmamali."""
    uid = _ekle(db)
    db.record_login("ayse", ip="10.0.0.1", user_agent="t", success=True)

    _client().post(f"/auth/users/{uid}/delete")

    assert any(k["username"] == "ayse" for k in db.login_events())


def test_yonetici_kendini_silemez(db):
    response = _client().post("/auth/users/1/delete")

    assert response.status_code == 400
    assert db.get_user(1) is not None


def test_son_yonetici_silinemez(db):
    """Panel yonetilemez hale gelirdi ve geri donus yolu yok."""
    ikinci = _ekle(db, "admin2", role="admin")
    db.set_active(1, False)

    response = _client().post(f"/auth/users/{ikinci}/delete")

    assert response.status_code == 400
    assert db.get_user(ikinci) is not None


def test_son_yonetici_devre_disi_birakilamaz(db):
    ikinci = _ekle(db, "admin2", role="admin")
    db.set_active(1, False)

    response = _client().post(f"/auth/users/{ikinci}/toggle")

    assert response.status_code == 400
    assert db.get_user(ikinci)["is_active"] == 1


def test_olmayan_kullanici_404(db):
    assert _client().post("/auth/users/999/delete").status_code == 404


# ---------------------------------------------------------------- sifre atama

def test_yonetici_dogrudan_sifre_atar(db):
    """Mail yolu calismadiginda (Graph kapali, adres yok) TEK kurtaris bu."""
    uid = _ekle(db)

    _client().post(f"/auth/users/{uid}/password", data={"new_password": YENI})

    assert security.verify_password(YENI, db.get_user(uid)["password_hash"])


def test_atanan_sifre_mevcut_sifreyi_sormaz(db):
    """Zaten unutuldugu icin buradayiz; yetki `admin` olmasindan geliyor."""
    uid = _ekle(db)

    response = _client().post(f"/auth/users/{uid}/password",
                              data={"new_password": YENI})

    assert response.status_code == 303


def test_kisa_sifre_atanamaz(db):
    uid = _ekle(db)

    response = _client().post(f"/auth/users/{uid}/password",
                              data={"new_password": "kisa"})

    assert response.status_code == 400
    assert security.verify_password(SIFRE, db.get_user(uid)["password_hash"])


def test_sifre_ataninca_oturumlar_ve_baglantilar_duser(db):
    uid = _ekle(db)
    _, oturum, oturum_exp = security.new_refresh_token()
    db.store_refresh_token(uid, oturum, oturum_exp)
    _, baglanti, baglanti_exp = security.new_reset_token()
    db.create_password_reset(uid, baglanti, baglanti_exp)

    _client().post(f"/auth/users/{uid}/password", data={"new_password": YENI})

    assert db.get_refresh_token(oturum)["revoked"] == 1
    assert db.get_password_reset(baglanti) is None


# ---------------------------------------------------------------- yetki

@pytest.mark.parametrize("rol", ["operator", "viewer"])
@pytest.mark.parametrize("yol", ["update", "password", "delete"])
def test_yonetici_olmayan_kullanici_yonetemez(db, rol, yol):
    """Uclar `/settings` disinda oldugu icin sayfa yetkisi onlari KAPSAMAZ;
    korumanin ucun kendisinde olmasi gerekir."""
    uid = _ekle(db)

    response = _client(rol).post(f"/auth/users/{uid}/{yol}", data={
        "full_name": "", "email": "", "role": "admin", "new_password": YENI})

    assert response.status_code == 403
    assert db.get_user(uid)["role"] == "operator"
