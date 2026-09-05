# -*- coding: utf-8 -*-
"""Sifremi unuttum: belirtec, mail ve /auth/forgot + /auth/reset uclari.

Bu akis panelin TEK acik yazma ucu — oturum olmadan calisir ve bir sifreyi
degistirir. Testlerin agirligi bu yuzden "calisiyor mu"dan cok "kotuye
kullanilabilir mi" tarafinda: kullanici sayimi, calinabilir baglantı,
tekrar kullanim, sureyi asma.

`notify.mail.send` HER TESTTE patch'lenir: mock modda gercek gonderici
govdeyi kurmadan True doner (`notify/mail.py:98`), yani govdeyi denetleyen
testler sessizce hicbir sey dogrulamazdi.

Calistirma: pytest -q
"""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import notify.mail
from auth import ratelimit, security
from auth.db import AuthDB
from auth.security import hash_password
from gls_api import config
from notify import password_reset
from web import deps
from web.routers import auth as auth_router

PANEL = "https://tracking.dolcezza.tr"
YENI = "Yeni!Sifre123"


@pytest.fixture
def gonderilen(monkeypatch):
    """Giden mailleri toplar; ag'a hic cikilmaz."""
    kutu = []

    def sahte_send(to, subject, html, attachments=None, cc=None):
        kutu.append({"to": to, "subject": subject, "html": html})
        return True, "ok"

    monkeypatch.setattr(notify.mail, "send", sahte_send)
    return kutu


@pytest.fixture
def db(tmp_path, monkeypatch):
    ratelimit.reset_all()
    d = AuthDB(path=tmp_path / "auth.db")
    d.create_user("ayse", hash_password("eski-sifre-123"), role="operator",
                  email="ayse@dolcezza.com.tr")
    monkeypatch.setattr(deps, "_auth_db", d)
    monkeypatch.setattr(config, "NOTIFY_PANEL_URL", PANEL, raising=False)
    return d


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(auth_router.router)
    return TestClient(app, follow_redirects=False)


def _link(kutu) -> str:
    """Maildeki sifirlama adresini cikarir."""
    html = kutu[-1]["html"]
    bas = html.index(f"{PANEL}/auth/reset?token=")
    return html[bas:html.index('"', bas)]


def _token(link: str) -> str:
    return link.split("token=", 1)[1]


# ---------------------------------------------------------------- belirtec

def test_her_belirtec_farklidir():
    """Tahmin edilebilir belirtec, sifirlama baglantisini herkese acik yapardi."""
    ham = {security.new_reset_token()[0] for _ in range(50)}

    assert len(ham) == 50


def test_ham_belirtec_saklanmaz_yalnizca_ozeti(db):
    ham, ozet, expires = security.new_reset_token()
    db.create_password_reset(1, ozet, expires)

    kayit = db.get_password_reset(ozet)

    assert kayit is not None
    assert ham not in str(dict(kayit))


def test_ozet_ayni_hamdan_ayni_cikar():
    ham, ozet, _ = security.new_reset_token()

    assert security.hash_reset_token(ham) == ozet


def test_suresi_gecmis_belirtec_anlasilir():
    gecmis = (datetime.utcnow() - timedelta(minutes=1)).isoformat(timespec="seconds")
    ileri = (datetime.utcnow() + timedelta(minutes=1)).isoformat(timespec="seconds")

    assert security.reset_token_expired(gecmis)
    assert not security.reset_token_expired(ileri)


# ---------------------------------------------------------------- mail govdesi

def test_mail_govdesinde_baglanti_iki_kez_gecer():
    """Bazi posta istemcileri dugmeyi cizmez; ham adres de gorunmeli."""
    _, html = password_reset.render("ayse", f"{PANEL}/auth/reset?token=abc")

    assert html.count(f"{PANEL}/auth/reset?token=abc") == 2


def test_mail_govdesi_kullanici_adini_kacisla_yazar():
    """Kullanici adi DB'den geliyor ama yine de HTML'e ham gomulmez."""
    _, html = password_reset.render("<script>x</script>", "https://x/y")

    assert "<script>" not in html


def test_mail_adresi_hicbir_yere_loglanmaz(db, gonderilen, caplog):
    with caplog.at_level("INFO"):
        password_reset.send("gizli@ornek.com", "ayse", f"{PANEL}/auth/reset?token=t")

    assert "gizli@ornek.com" not in caplog.text


# ---------------------------------------------------------------- /auth/forgot

def test_e_posta_ile_baglanti_gonderilir(client, gonderilen):
    response = client.post("/auth/forgot", data={"identifier": "ayse@dolcezza.com.tr"})

    assert response.status_code == 200
    assert len(gonderilen) == 1
    assert gonderilen[0]["to"] == ["ayse@dolcezza.com.tr"]


def test_kullanici_adi_ile_de_gonderilir(client, gonderilen):
    """Kullanici e-postasini degil kullanici adini hatirliyor olabilir."""
    client.post("/auth/forgot", data={"identifier": "ayse"})

    assert len(gonderilen) == 1


def test_olmayan_hesap_ayni_cevabi_alir(client, gonderilen):
    """Farkli cevap vermek 'bu kullanici adi sistemde var' demek olurdu —
    saldirgan once gecerli kullanici adlarini toplar, sonra sifre dener."""
    var = client.post("/auth/forgot", data={"identifier": "ayse"})
    yok = client.post("/auth/forgot", data={"identifier": "hayalet"})

    assert var.status_code == yok.status_code
    assert var.text == yok.text


def test_pasif_hesaba_baglanti_gitmez(client, db, gonderilen):
    db.set_active(1, False)

    client.post("/auth/forgot", data={"identifier": "ayse"})

    assert gonderilen == []


def test_e_postasi_olmayan_hesap_kimseye_mail_yollamaz(client, db, gonderilen):
    """Bos e-posta bos adrese mail atmamali; ayrica bos aramayla BUTUN eski
    hesaplarin eslesmesi felaket olurdu."""
    db.create_user("mehmet", hash_password("x" * 10), role="operator")

    client.post("/auth/forgot", data={"identifier": ""})

    assert gonderilen == []


def test_baglanti_koku_host_basligindan_gelmez(client, gonderilen):
    """Host basligi kullanici girdisidir. `request.base_url` kullanilsaydi
    saldirgan kendi adresini yazip kurbanin belirtecini toplardi."""
    client.post("/auth/forgot", data={"identifier": "ayse"},
                headers={"Host": "kotu.site"})

    assert "kotu.site" not in gonderilen[0]["html"]
    assert PANEL in gonderilen[0]["html"]


def test_panel_adresi_bosken_mail_gitmez(client, monkeypatch, gonderilen):
    """Yarim bir baglanti gondermek kullaniciyi olu bir adrese goturur."""
    monkeypatch.setattr(config, "NOTIFY_PANEL_URL", "", raising=False)

    response = client.post("/auth/forgot", data={"identifier": "ayse"})

    assert response.status_code == 200      # kullaniciya yine ayni genel mesaj
    assert gonderilen == []


def test_ayni_hedefe_mail_bombasi_engellenir(client, gonderilen):
    for _ in range(ratelimit.FORGOT_ID.limit):
        client.post("/auth/forgot", data={"identifier": "ayse"})

    response = client.post("/auth/forgot", data={"identifier": "ayse"})

    assert response.status_code == 429
    assert len(gonderilen) == ratelimit.FORGOT_ID.limit


def test_ortak_posta_kutusundaki_her_hesap_kendi_baglantisini_alir(client, db, gonderilen):
    """`depo@...` gibi paylasimli adresler mesru. Tek baglanti gonderilse
    hangi hesabin sifirlandigi belirsiz kalirdi."""
    db.create_user("depo2", hash_password("x" * 10), role="operator",
                   email="ayse@dolcezza.com.tr")

    client.post("/auth/forgot", data={"identifier": "ayse@dolcezza.com.tr"})

    assert len(gonderilen) == 2


def test_istek_denetim_izine_yazilir(client, db, gonderilen):
    client.post("/auth/forgot", data={"identifier": "ayse"})

    assert any(k["reason"] == "password_reset_requested" for k in db.login_events())


# ---------------------------------------------------------------- /auth/reset

def test_baglantiyla_yeni_sifre_belirlenir(client, db, gonderilen):
    client.post("/auth/forgot", data={"identifier": "ayse"})
    token = _token(_link(gonderilen))

    response = client.post("/auth/reset", data={
        "token": token, "new_password": YENI, "new_password_confirm": YENI})

    assert response.status_code == 200
    assert security.verify_password(YENI, db.get_user(1)["password_hash"])


def test_baglanti_yalnizca_bir_kez_calisir(client, gonderilen):
    """Belirtec mail kutusunda kalir; ikinci kullanim eski bir mailden hesap
    ele gecirmeyi mumkun kilardi."""
    client.post("/auth/forgot", data={"identifier": "ayse"})
    token = _token(_link(gonderilen))
    veri = {"token": token, "new_password": YENI, "new_password_confirm": YENI}
    client.post("/auth/reset", data=veri)

    ikinci = client.post("/auth/reset", data=veri)

    assert ikinci.status_code == 400


def test_suresi_gecmis_baglanti_reddedilir(client, db, gonderilen):
    client.post("/auth/forgot", data={"identifier": "ayse"})
    token = _token(_link(gonderilen))
    db.conn.execute(
        "UPDATE password_resets SET expires_at = ?",
        ((datetime.utcnow() - timedelta(minutes=1)).isoformat(timespec="seconds"),))
    db.conn.commit()

    response = client.post("/auth/reset", data={
        "token": token, "new_password": YENI, "new_password_confirm": YENI})

    assert response.status_code == 400


def test_uydurma_belirtec_reddedilir(client):
    response = client.post("/auth/reset", data={
        "token": "uydurma", "new_password": YENI, "new_password_confirm": YENI})

    assert response.status_code == 400


def test_eslesmeyen_sifreler_reddedilir(client, gonderilen):
    client.post("/auth/forgot", data={"identifier": "ayse"})
    token = _token(_link(gonderilen))

    response = client.post("/auth/reset", data={
        "token": token, "new_password": YENI, "new_password_confirm": "baska"})

    assert response.status_code == 422


def test_kisa_sifre_reddedilir(client, gonderilen):
    client.post("/auth/forgot", data={"identifier": "ayse"})
    token = _token(_link(gonderilen))

    response = client.post("/auth/reset", data={
        "token": token, "new_password": "kisa", "new_password_confirm": "kisa"})

    assert response.status_code == 422


def test_sifirlamadan_sonra_acik_oturumlar_duser(client, db, gonderilen):
    """Sifirlamanin sebebi hesabin ele gecmis olmasi olabilir; saldirganin
    acik oturumu ayakta kalirsa sifre degistirmek ise yaramazdi."""
    _, ozet, expires = security.new_refresh_token()
    db.store_refresh_token(1, ozet, expires)
    client.post("/auth/forgot", data={"identifier": "ayse"})
    token = _token(_link(gonderilen))

    client.post("/auth/reset", data={
        "token": token, "new_password": YENI, "new_password_confirm": YENI})

    assert db.get_refresh_token(ozet)["revoked"] == 1


def test_sifirlama_giris_kilidini_acar(client, gonderilen):
    """5 kez yanlis deneyip kilitlenen kullanici sifresini sifirladiktan sonra
    5 dakika daha kapida beklememeli — kimligini zaten kanitladi."""
    for _ in range(ratelimit.LOGIN_USER.limit):
        client.post("/auth/login", data={"username": "ayse", "password": "yanlis"})
    client.post("/auth/forgot", data={"identifier": "ayse"})
    token = _token(_link(gonderilen))

    client.post("/auth/reset", data={
        "token": token, "new_password": YENI, "new_password_confirm": YENI})

    assert not ratelimit.locked(ratelimit.login_user("ayse"), ratelimit.LOGIN_USER)


def test_sifirlamadan_sonra_bekleyen_diger_baglantilar_olur(client, db, gonderilen):
    client.post("/auth/forgot", data={"identifier": "ayse"})
    ilk = _token(_link(gonderilen))
    client.post("/auth/forgot", data={"identifier": "ayse"})
    ikinci = _token(_link(gonderilen))

    client.post("/auth/reset", data={
        "token": ikinci, "new_password": YENI, "new_password_confirm": YENI})

    assert db.get_password_reset(security.hash_reset_token(ilk)) is None


def test_sifirlama_formu_belirteci_disariya_sizdirmaz(client, gonderilen):
    """Belirtec adres cubugunda; sayfadaki bir CDN istegi onu `Referer` ile
    disari tasiyabilirdi."""
    client.post("/auth/forgot", data={"identifier": "ayse"})
    token = _token(_link(gonderilen))

    response = client.get(f"/auth/reset?token={token}")

    assert 'name="referrer" content="no-referrer"' in response.text
