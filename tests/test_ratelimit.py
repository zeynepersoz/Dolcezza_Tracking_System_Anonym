# -*- coding: utf-8 -*-
"""Kimlik uclarindaki kaba kuvvet freni.

Sayac `time.monotonic()` uzerine kurulu; testler beklemek yerine saati
`monkeypatch` ile ileri sariyor — aksi halde 15 dakikalik pencereyi dogrulamak
15 dakika surerdi.

Calistirma: pytest -q
"""
import pytest

from auth import ratelimit


@pytest.fixture(autouse=True)
def temiz():
    ratelimit.reset_all()
    yield
    ratelimit.reset_all()


@pytest.fixture
def saat(monkeypatch):
    """Sahte monotonik saat. `ileri(sn)` ile zaman gecirilir."""
    simdi = {"t": 1000.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: simdi["t"])
    return lambda sn: simdi.__setitem__("t", simdi["t"] + sn)


KURAL = ratelimit.Rule(limit=3, window=60, lockout=30)


def test_sinir_asilana_kadar_kilit_yok(saat):
    for _ in range(KURAL.limit - 1):
        ratelimit.hit("k")
    assert not ratelimit.locked("k", KURAL)


def test_sinira_ulasinca_kilitlenir(saat):
    for _ in range(KURAL.limit):
        ratelimit.hit("k")
    assert ratelimit.locked("k", KURAL)


def test_kilit_suresi_dolunca_acilir(saat):
    for _ in range(KURAL.limit):
        ratelimit.hit("k")
    saat(KURAL.lockout + 1)
    assert not ratelimit.locked("k", KURAL)


def test_pencere_kayarken_eski_denemeler_unutulur(saat):
    """Gunde 3 kez sifresini yanlis yazan kullanici HIC kilitlenmemeli.

    Sayac 'toplam deneme' degil 'pencere icindeki deneme' saymali; yoksa
    aylardir ayaktaki bir kurulumda herkes yavas yavas kilide dogru yururdu.
    """
    for _ in range(KURAL.limit * 5):
        ratelimit.hit("k")
        saat(KURAL.window + 1)
        assert not ratelimit.locked("k", KURAL)


def test_clear_kilidi_kaldirir(saat):
    for _ in range(KURAL.limit):
        ratelimit.hit("k")
    ratelimit.clear("k")
    assert not ratelimit.locked("k", KURAL)


def test_kovalar_birbirini_etkilemez(saat):
    """Kullanici ve IP kovalari AYRI sayilmali.

    Ayni sozlukte olduklari icin bir isim carpismasi (orn. 'ayse' hem kullanici
    hem IP anahtari) bir kullanicinin kilidini butun ofise yayardi.
    """
    for _ in range(KURAL.limit):
        ratelimit.hit(ratelimit.login_user("ayse"))
    assert ratelimit.locked(ratelimit.login_user("ayse"), KURAL)
    assert not ratelimit.locked(ratelimit.login_ip("ayse"), KURAL)
    assert not ratelimit.locked(ratelimit.login_user("mehmet"), KURAL)


def test_forgot_kovasi_buyuk_kucuk_harf_ayirmaz():
    """'Ayse@x.com' ile 'ayse@x.com' ayni hedeftir — aksi halde sinir, adresi
    farkli yazarak sonsuz kez asilirdi."""
    assert ratelimit.forgot_id(" Ayse@X.com ") == ratelimit.forgot_id("ayse@x.com")
