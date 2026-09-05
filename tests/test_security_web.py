# -*- coding: utf-8 -*-
"""Giris kaydi + elle IP engelleme.

Denetim izinin degeri BASARISIZ denemelerdedir: bir saldiri ancak "ayni IP'den
yuzlerce hatali deneme" izinden gorulur, basarili girisler onu gostermez. Bu
yuzden testlerin cogu basarisiz yolu kovaliyor.

Calistirma: pytest -q
"""
from datetime import datetime, timedelta

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from auth import ratelimit, security
from auth.db import AuthDB
from auth.dependencies import get_current_user
from web import deps
from web.routers import auth as auth_router
from web.routers import settings as settings_router


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = AuthDB(path=tmp_path / "auth.db")
    monkeypatch.setattr(deps, "_auth_db", d)
    # Kilitlenme sayaci sureclidir (modul globali) — testler birbirini kirletmesin.
    ratelimit.reset_all()
    return d


@pytest.fixture
def user(db):
    db.create_user("ayse", security.hash_password("dogru-sifre-123"), role="admin")
    return db.get_user_by_username("ayse")


def _client(role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(auth_router.router)
    app.include_router(settings_router.router, dependencies=[Depends(get_current_user)])

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "yonetici", "role": role}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app)


# ---------------------------------------------------------------- giris kaydi

def test_a_failed_attempt_is_recorded_with_its_reason(db, user):
    _client().post("/auth/login", data={"username": "ayse", "password": "yanlis"})

    events = db.login_events()
    assert len(events) == 1
    assert events[0]["success"] == 0
    assert events[0]["reason"] == "bad_credentials"
    assert events[0]["username"] == "ayse"


def test_a_username_that_does_not_exist_is_recorded_too(db):
    """Var olmayan ad denemesi asil ilginc olandir — sozluk saldirisi boyle gorunur."""
    _client().post("/auth/login", data={"username": "root", "password": "x"})

    assert db.login_events()[0]["username"] == "root"


def test_a_missing_username_still_pays_the_full_pbkdf2_cost(db, monkeypatch):
    """Olmayan kullanici icin `verify_password` KISA DEVRE edilmemeli.

    Aksi halde var-olan bir hesaba gore yanit daha YAVAS (gercek hash
    dogrulanir), olmayana gore ANLIK doner — sure farkindan "bu kullanici
    adi sistemde var mi" bilgisi sizar (zamanlama saldirisi)."""
    calls = []
    real_verify = security.verify_password

    def spy(password, encoded_hash):
        calls.append(encoded_hash)
        return real_verify(password, encoded_hash)

    monkeypatch.setattr(security, "verify_password", spy)
    _client().post("/auth/login", data={"username": "hic-yok", "password": "x"})

    assert calls == [security.DUMMY_HASH]


def test_a_disabled_account_is_told_apart_from_a_wrong_password(db, user):
    db.set_active(user["id"], False)

    _client().post("/auth/login", data={"username": "ayse", "password": "dogru-sifre-123"})

    assert db.login_events()[0]["reason"] == "inactive"


def test_a_successful_login_is_recorded(db, user):
    _client().post("/auth/login", data={"username": "ayse", "password": "dogru-sifre-123"})

    events = db.login_events()
    assert events[0]["success"] == 1
    assert events[0]["reason"] == ""


def test_a_broken_audit_log_does_not_keep_anyone_out(db, user, monkeypatch):
    """Disk dolarsa ya da DB kilitlenirse kimse kapida kalmamali."""
    def boom(*a, **kw):
        raise RuntimeError("disk dolu")

    monkeypatch.setattr(db, "record_login", boom)

    r = _client().post("/auth/login", data={"username": "ayse", "password": "dogru-sifre-123"},
                       follow_redirects=False)

    assert r.status_code == 303


def test_old_records_are_purged_on_write(db, user):
    old = (datetime.utcnow() - timedelta(days=200)).isoformat(timespec="seconds")
    db.conn.execute("INSERT INTO login_events (username, ip, success, at) VALUES (?, ?, ?, ?)",
                    ("eski", "1.2.3.4", 0, old))
    db.conn.commit()

    db.record_login("ayse", ip="5.6.7.8", success=True)

    assert [e["username"] for e in db.login_events()] == ["ayse"]


def test_only_failed_filters_out_the_noise(db, user):
    db.record_login("ayse", success=True)
    db.record_login("root", success=False, reason="bad_credentials")

    assert [e["username"] for e in db.login_events(only_failed=True)] == ["root"]


def test_the_ip_summary_counts_success_and_failure_apart(db):
    db.record_login("ayse", ip="10.0.0.5", success=True)
    db.record_login("root", ip="10.0.0.5", success=False, reason="bad_credentials")
    db.record_login("root", ip="10.0.0.5", success=False, reason="bad_credentials")

    row = db.login_ip_summary()[0]
    assert row["ip"] == "10.0.0.5"
    assert row["total"] == 3
    assert row["ok"] == 1


# ---------------------------------------------------------------- kaba kuvvet freni

def test_repeated_failures_lock_the_username(db, user):
    for _ in range(ratelimit.LOGIN_USER.limit):
        _client().post("/auth/login", data={"username": "ayse", "password": "yanlis"})

    r = _client().post("/auth/login", data={"username": "ayse", "password": "yanlis"})

    assert r.status_code == 429


def test_a_dictionary_attack_across_many_usernames_is_stopped(db, user):
    """Sayac YALNIZCA kullanici adi basina sayilsaydi, 500 farkli ad deneyen bir
    bot hicbir kovayi doldurmaz ve sinirsiz denerdi — sayac hep 1'de kalir."""
    for i in range(ratelimit.LOGIN_IP.limit):
        _client().post("/auth/login", data={"username": f"kullanici{i}", "password": "x"})

    r = _client().post("/auth/login", data={"username": "bambaska", "password": "x"})

    assert r.status_code == 429
    assert db.login_events()[0]["reason"] == "ip_rate_limited"


def test_a_correct_login_does_not_reset_the_ip_counter(db, user):
    """Saldirgan aradaki tek bir dogru girisle (kendi hesabiyla) IP sayacini
    sifirlayabilseydi fren hic devreye girmezdi."""
    for i in range(ratelimit.LOGIN_IP.limit - 1):
        _client().post("/auth/login", data={"username": f"kullanici{i}", "password": "x"})
    _client().post("/auth/login", data={"username": "ayse", "password": "dogru-sifre-123"})

    _client().post("/auth/login", data={"username": "sonuncu", "password": "x"})
    r = _client().post("/auth/login", data={"username": "bambaska", "password": "x"})

    assert r.status_code == 429


def test_a_rate_limited_reply_is_a_page_not_a_json_error(db, user):
    """Ciplak bir JSON hatasi kullaniciyi panelden atilmis gibi hissettirir ve
    'ne yapmali' bilgisi gitmez."""
    for _ in range(ratelimit.LOGIN_USER.limit):
        _client().post("/auth/login", data={"username": "ayse", "password": "yanlis"})

    r = _client().post("/auth/login", data={"username": "ayse", "password": "yanlis"})

    assert "<form" in r.text


# ---------------------------------------------------------------- IP engelleme

def test_a_blocked_ip_cannot_sign_in_even_with_the_right_password(db, user):
    db.block_ip("testclient")          # TestClient'in gorundugu adres

    r = _client().post("/auth/login", data={"username": "ayse", "password": "dogru-sifre-123"})

    assert r.status_code == 403
    assert db.login_events()[0]["reason"] == "ip_blocked"


def test_blocking_takes_effect_without_touching_open_sessions(db, user):
    """Engel YALNIZCA giris ekranindadir: yonetici kendini kilitlerse geri alabilsin."""
    client = _client()
    db.block_ip("testclient")

    assert client.get("/settings/security").status_code == 200


def test_you_cannot_block_your_own_address(db):
    r = _client().post("/auth/ip/block", data={"ip": "testclient"})

    assert r.status_code == 400
    assert db.blocked_ips() == []


def test_an_empty_ip_is_refused(db):
    r = _client().post("/auth/ip/block", data={"ip": "   "})

    assert r.status_code == 400


def test_blocking_records_who_did_it(db):
    _client().post("/auth/ip/block", data={"ip": "10.0.0.9", "note": "surekli deniyor"})

    row = db.blocked_ips()[0]
    assert row["ip"] == "10.0.0.9"
    assert row["blocked_by"] == "yonetici"
    assert row["note"] == "surekli deniyor"


def test_blocking_the_same_ip_twice_updates_the_note(db):
    c = _client()
    c.post("/auth/ip/block", data={"ip": "10.0.0.9", "note": "ilk"})
    c.post("/auth/ip/block", data={"ip": "10.0.0.9", "note": "ikinci"})

    assert [r["note"] for r in db.blocked_ips()] == ["ikinci"]


def test_unblocking_removes_the_row(db):
    c = _client()
    c.post("/auth/ip/block", data={"ip": "10.0.0.9"})
    c.post("/auth/ip/unblock", data={"ip": "10.0.0.9"})

    assert db.blocked_ips() == []
    assert db.is_ip_blocked("10.0.0.9") is False


def test_an_operator_cannot_block_anyone(db):
    r = _client(role="operator").post("/auth/ip/block", data={"ip": "10.0.0.9"})

    assert r.status_code == 403
    assert db.blocked_ips() == []


# ---------------------------------------------------------------- ekran

def test_the_screen_is_admin_only(db):
    assert _client(role="operator").get("/settings/security").status_code == 403


def test_the_screen_shows_the_attempts_and_marks_your_own_ip(db):
    db.record_login("root", ip="10.0.0.9", success=False, reason="bad_credentials")
    db.record_login("ayse", ip="testclient", success=True)

    html = _client().get("/settings/security").text

    assert "10.0.0.9" in html
    assert "Hatalı şifre" in html          # security.reason.bad_credentials
    assert "siz" in html                    # kendi IP'sini isaretleyen rozet


# ------------------------------------------------- giris kaydi sayfalamasi

def _fill(db, n: int, *, failed_every: int = 1) -> None:
    for i in range(n):
        db.record_login(f"k{i}", ip="192.0.2.20", success=bool(i % failed_every),
                        reason="" if i % failed_every else "bad_password")


def test_the_counter_and_the_page_share_one_filter(db):
    """Iki ayri WHERE kurucusu olsaydi "1243 kayit" derken sayfalar baska bir
    kumede biterdi — `tracking/db.py:_list_where` ayni gerekceyle ayrilmisti."""
    _fill(db, 10, failed_every=2)

    assert db.count_login_events(only_failed=True) == 5
    assert len(db.login_events(limit=100, only_failed=True)) == 5


def test_the_second_page_does_not_repeat_the_first(db):
    """Ayni saniyede yazilan denemeler `id DESC` olmadan sirasiz kalirdi."""
    _fill(db, 250)

    first = {e["id"] for e in db.login_events(limit=100)}
    second = {e["id"] for e in db.login_events(limit=100, offset=100)}

    assert len(first) == len(second) == 100
    assert not (first & second)


def test_a_nonsense_page_number_does_not_crash_the_screen(db):
    """Deger istekten geliyor; ciplak `int()` 500 dondururdu."""
    assert _client().get("/settings/security?page=abc").status_code == 200
    assert _client().get("/settings/security?page=-4").status_code == 200


def test_the_next_page_link_carries_the_failed_filter(db):
    """"Sonraki"ye basan yonetici suzgeci sessizce kaybetmemeli."""
    _fill(db, 150)  # hepsi basarisiz

    html = _client().get("/settings/security?failed=1").text

    assert "/settings/security?page=2&amp;failed=1" in html


def test_the_note_no_longer_promises_a_ceiling_of_200(db):
    """Liste artik sayfalaniyor; "Son 200 deneme" yazisi yanlis olurdu."""
    import i18n

    assert "200" not in i18n.t("security.log.note", "tr")
