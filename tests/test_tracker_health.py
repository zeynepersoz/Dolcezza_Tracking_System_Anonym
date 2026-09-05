# -*- coding: utf-8 -*-
"""Taramanin sessizce olmesine karsi kurulan uc kanal.

Neden var: uygulama ayakta oldugu surece panel aciliyor, sayfalar dolu
gorunuyor ve `/health` "ok" diyordu — tarama saatlerdir patlıyor olsa bile.
Tek belirti kargo durumlarinin donmasiydi ve onu ancak biri fark ederse
anlasiliyordu. Buradaki testler ucunun de AYNI esikle konustugunu kanitlar.

Zaman ilerletmek icin saat degil VERITABANI oynanir (`_age`): testin gercekten
beklemesi ya da `datetime`in yamalanmasi gerekmesin.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tracking.db import ShipmentsDB
from tracking.scheduler import Tracker
from web import deps


@pytest.fixture
def db(tmp_path, monkeypatch):
    s = ShipmentsDB(path=tmp_path / "health.db")
    monkeypatch.setattr(deps, "_shipments_db", s)
    return s


def _age(db, hours: float, column: str = "last_success_at") -> None:
    """Kayitli damgayi geriye alir — 'X saat once calisti' demenin kisa yolu."""
    stamp = (datetime.utcnow() - timedelta(hours=hours)).isoformat(timespec="seconds")
    db.conn.execute(f"UPDATE tracker_health SET {column} = ? WHERE id = 1", (stamp,))
    db.conn.commit()


class Spy:
    """`notify.alert.run` yerine gecer — kac kez, hangi bilgiyle cagrildi."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, health):
        self.calls.append(health)


# ---------------------------------------------------------------- kayit

def test_a_failed_run_does_not_move_the_success_clock(db):
    """Basarisiz tur `last_run_at`i tazeler ama saati SIFIRLAMAZ.

    Aksi halde her 15 dakikada bir patlayan bir tarama, sirf denendigi icin
    kendini "az once calisti" gosterir ve esige hicbir zaman ulasmazdi.
    """
    db.record_tick(ok=True)
    first = db.health()["last_success_at"]

    db.record_tick(ok=False, error="GLS 500")

    info = db.health()
    assert info["last_success_at"] == first
    assert info["last_run_at"] >= first
    assert info["last_error"] == "GLS 500"


def test_a_partial_failure_is_not_lost_by_the_next_successful_tick(db):
    """`last_error` bir sonraki basarili turda silinir — kalici gecmis silinMEZ.

    Canli senaryo (kullanici bildirdi, 2026-08-27): bir tur icinde bir GLS
    grubu basarisiz olsa da tur geneli "basarili" sayilabiliyor (`ok=answered
    > 0`), 15 dakika sonraki tur temiz gelirse operator hicbir zaman "bir
    grup atlandi" bilgisini goremiyordu.
    """
    db.record_tick_error("NL grubu (20 parca, ilk 38120177000001): HTTP 429")
    db.record_tick(ok=True)  # tur genel olarak basarili sayildi
    db.record_tick(ok=True)  # 15 dk sonraki tur tertemiz

    info = db.health()
    assert info["last_error"] is None  # tekil alan beklendigi gibi temizlendi

    history = db.recent_tick_errors()
    assert len(history) == 1
    assert "HTTP 429" in history[0]["error"]


def test_tick_errors_are_newest_first_and_capped(db):
    for i in range(5):
        db.record_tick_error(f"hata {i}")
    history = db.recent_tick_errors(limit=3)
    assert [h["error"] for h in history] == ["hata 4", "hata 3", "hata 2"]


def test_recovery_rearms_the_alert(db):
    """Basarili tur `alerted_at`i temizler: bir sonraki ariza yine duyulmali."""
    db.record_tick(ok=False, error="patladi")
    db.mark_alerted()
    assert db.health()["alerted_at"]

    db.record_tick(ok=True)
    assert db.health()["alerted_at"] is None


def test_health_survives_a_restart(tmp_path):
    """Saglik bellekte degil diskte durur.

    `Tracker` bu alanlari zaten oznitelik olarak tutuyordu; surekli acilip
    taramada patlayan bir kurulum her yeniden baslatmada "yeni dogmus"
    gorunur, 6 saatlik esige hic varamaz ve uyari hic gitmezdi.
    """
    path = tmp_path / "restart.db"
    ShipmentsDB(path=path).record_tick(ok=True)

    assert ShipmentsDB(path=path).health()["last_success_at"]


# ---------------------------------------------------------------- uyari

def test_no_alert_while_the_scan_is_fresh(db):
    spy = Spy()
    db.record_tick(ok=True)

    assert Tracker(db=db, alert_sender=spy, alert_after_hours=6).check_health() is False
    assert spy.calls == []


def test_one_alert_per_outage_not_one_per_tick(db):
    """Esik asilinca BIR kez yazilir; tur 15 dakikada bir donuyor."""
    spy = Spy()
    tracker = Tracker(db=db, alert_sender=spy, alert_after_hours=6)
    db.record_tick(ok=True)
    _age(db, hours=9)

    assert tracker.check_health() is True
    assert tracker.check_health() is False
    assert len(spy.calls) == 1
    assert spy.calls[0]["stale_hours"] == pytest.approx(9, abs=0.2)


def test_an_install_that_never_succeeded_still_alerts(db):
    """Hic basarili tur yoksa sure ILK denemeden olculur.

    Yoksa daha ilk turundan bozuk gelen bir kurulum sonsuza dek sessiz kalirdi
    — uyarinin en cok gerektigi durum tam olarak budur.
    """
    spy = Spy()
    db.record_tick(ok=False, error="kimlik yok")
    _age(db, hours=9, column="last_run_at")

    assert Tracker(db=db, alert_sender=spy, alert_after_hours=6).check_health() is True


def test_a_brand_new_install_is_not_an_outage(db):
    """Hic tur donmemis olmak ariza degil — acilisin ilk dakikalari."""
    spy = Spy()

    assert Tracker(db=db, alert_sender=spy, alert_after_hours=6).check_health() is False


def test_the_alert_mail_names_the_outage(db):
    """Govde sablonsuz uretilir: ariza aninda sablon/Excel de patlayabilir."""
    from notify import alert

    subject, html = alert.render({"stale_hours": 9.0, "last_error": "GLS 500",
                                  "last_success_at": "2026-08-17T02:00:00"})

    assert "9.0" in subject
    assert "GLS 500" in html and "2026-08-17T02:00:00" in html


# ---------------------------------------------------------------- /health

def _health_client() -> TestClient:
    from web.main import app
    return TestClient(app)


def test_health_is_ok_while_the_scan_runs(db):
    db.record_tick(ok=True)

    response = _health_client().get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_503_when_the_scan_is_stale(db):
    """Ayakta olmak yetmez. Docker/izleme "ok" gorurse ariza gorunmez kalir."""
    db.record_tick(ok=True)
    _age(db, hours=9)

    response = _health_client().get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["tracker"]["stale_hours"] == pytest.approx(9, abs=0.2)


# ---------------------------------------------------------------- serit

def test_the_banner_only_appears_during_an_outage(db):
    """Serit HER sayfada: kullanici gununu /tracking ve /pod'da geciriyor."""
    db.record_tick(ok=True)
    assert deps.tracker_alarm() is None

    _age(db, hours=9)
    alarm = deps.tracker_alarm()
    assert alarm and alarm["hours"] == pytest.approx(9, abs=0.2)
