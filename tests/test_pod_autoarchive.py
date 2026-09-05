"""POD'un kendiliginden arsivlenmesi — takip taramasinin ikinci adimi.

Neden var: POD'u kullanicinin "indir" tiklamasina birakmak calismadi. 2026-08
olcumu: 173 teslimattan yalnizca 2'sinin POD'u arsivlenmisti. Buradaki testler
kuyrugun tikanmadigini ve tur basina tavanin asilmadigini kanitlar.
"""
from datetime import datetime, timedelta

import pytest

from tracking.db import ShipmentsDB
from tracking.scheduler import Tracker


@pytest.fixture()
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "pod.db")


def _delivered(db, tracking_no, channel="NL"):
    db.add_parcel(tracking_no=tracking_no, channel=channel, reference="r",
                  consignee_name="c", country="DE")
    db.update_status(tracking_no, "delivered")


def _pod_asked_hours_ago(db, tracking_no, hours: float):
    """`pod_attempted_at`i geriye alir — taze teslimatin yeniden sorulma
    araligini (POD_FRESH_RETRY_HOURS) test etmek icin."""
    stamp = (datetime.utcnow() - timedelta(hours=hours)).isoformat(timespec="seconds")
    db.conn.execute("UPDATE parcels SET pod_attempted_at = ? WHERE tracking_no = ?",
                    (stamp, tracking_no))
    db.conn.commit()


class FakeFetcher:
    """`web.routers.pod.archive_pod` yerine gecer: cagrilanlari sayar, DB'ye yazar.

    `misses` icindekiler icin "POD yok" der, `boom` icindekiler icin patlar.
    """

    def __init__(self, db, misses=(), boom=()):
        self.db = db
        self.seen: list[str] = []
        self.misses = set(misses)
        self.boom = set(boom)

    def __call__(self, parcel):
        tn = parcel["tracking_no"]
        self.seen.append(tn)
        if tn in self.boom:
            raise RuntimeError("GLS patladi")
        if tn in self.misses:
            return None
        self.db.set_pod_path(tn, f"/arsiv/{tn}.png", "png")
        return f"/arsiv/{tn}.png"


# ---------------------------------------------------------------- kuyruk

def test_only_delivered_parcels_without_pod_are_queued(db):
    _delivered(db, "38120177000001")
    db.add_parcel(tracking_no="38120177000002", channel="NL", reference="r",
                  consignee_name="c", country="DE")          # yolda
    _delivered(db, "38120177000003")
    db.set_pod_path("38120177000003", "/arsiv/x.png", "png")  # POD'u zaten var

    assert [p["tracking_no"] for p in db.parcels_without_pod()] == ["38120177000001"]


def test_never_asked_parcels_come_first(db):
    _delivered(db, "38120177000001")
    _delivered(db, "38120177000002")
    db.mark_pod_attempted("38120177000001")
    _pod_asked_hours_ago(db, "38120177000001", 3)  # kisa gap gecti, hala kuyrukta

    assert [p["tracking_no"] for p in db.parcels_without_pod()] == [
        "38120177000002", "38120177000001"]


def test_a_parcel_without_pod_does_not_block_the_queue(db):
    """GLS bazi teslimatlar icin POD vermez (204). O parca kuyrugun basinda kalmamali."""
    _delivered(db, "38120177000001")
    _delivered(db, "38120177000002")
    fetcher = FakeFetcher(db, misses={"38120177000001", "38120177000002"})

    tracker = Tracker(db=db, pod_fetcher=fetcher, max_pod_per_tick=1)
    tracker.tick()
    tracker.tick()

    assert fetcher.seen == ["38120177000001", "38120177000002"]


def test_parcel_is_retried_after_the_others(db):
    """POD teslimattan saatler sonra olusabiliyor — kuyruktan tamamen dusmez.

    Ama HER TUR degil: taze teslimat POD_FRESH_RETRY_HOURS'da bir yeniden
    sorulur (~40 taze teslimat her turda sorulunca tur ~2,5 dk tikaniyordu,
    2026-09-03). Iki tur ARASINDA kisa gap gecmis sayilir.
    """
    _delivered(db, "38120177000001")
    fetcher = FakeFetcher(db, misses={"38120177000001"})
    tracker = Tracker(db=db, pod_fetcher=fetcher, max_pod_per_tick=5)

    tracker.tick()
    _pod_asked_hours_ago(db, "38120177000001", 3)
    tracker.tick()

    assert fetcher.seen == ["38120177000001"] * 2


# ------------------------------------------------------- eskiyen teslimat

def _old_delivery(db, tracking_no, days: int, attempted_hours_ago: float):
    """Gunler once teslim edilmis, POD'u hala cikmamis bir parca."""
    _delivered(db, tracking_no)
    delivered = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
    asked = (datetime.utcnow() - timedelta(hours=attempted_hours_ago)).isoformat(timespec="seconds")
    db.conn.execute(
        "UPDATE parcels SET delivered_date = ?, pod_attempted_at = ? WHERE tracking_no = ?",
        (delivered, asked, tracking_no))
    db.conn.commit()


def test_an_old_delivery_is_not_asked_every_tick(db):
    """Canli olcum 2026-08-17: 16 teslimatin POD'u gunler sonra hala yoktu ve
    GLS her seferinde 204 donuyordu. Kuyruk onlari her 15 dakikada bir yeniden
    soruyordu — tur basina ~40 saniye, gunde ~2200 bosa istek.
    """
    _old_delivery(db, "38120177000001", days=10, attempted_hours_ago=1)

    assert db.parcels_without_pod() == []


def test_an_old_delivery_still_comes_back_later(db):
    """Seyreltiliyor, VAZGECILMIYOR: POD gunler sonra da olusabiliyor."""
    _old_delivery(db, "38120177000001", days=10, attempted_hours_ago=13)

    assert [p["tracking_no"] for p in db.parcels_without_pod()] == ["38120177000001"]


def test_a_fresh_delivery_is_asked_again_after_the_short_gap(db):
    """Taze teslimat SIK sorulur ama HER TUR DEGIL: POD_FRESH_RETRY_HOURS'da
    bir. ~40 taze teslimat her 15 dk'de sorulunca tur ~2,5 dk tikaniyordu
    (canli olcum 2026-09-03). Son deneme > 2 saat once ise yine kuyruga girer.
    """
    _old_delivery(db, "38120177000001", days=0, attempted_hours_ago=3)

    assert [p["tracking_no"] for p in db.parcels_without_pod()] == ["38120177000001"]


def test_a_fresh_delivery_is_not_re_asked_within_the_short_gap(db):
    """Az once sorulmus taze teslimat bir sonraki turda TEKRAR sorulmaz."""
    _old_delivery(db, "38120177000001", days=0, attempted_hours_ago=0.25)

    assert db.parcels_without_pod() == []


# ---------------------------------------------------------------- tarayici

def test_tick_archives_missing_pods(db):
    _delivered(db, "38120177000001")
    tracker = Tracker(db=db, pod_fetcher=FakeFetcher(db))

    tracker.tick()

    assert db.get("38120177000001")["pod_path"] == "/arsiv/38120177000001.png"
    assert tracker.last_pod_count == 1
    assert db.parcels_without_pod() == []


def test_tick_respects_the_pod_ceiling(db):
    """POD toplu sorgulanamaz; tur basina tavan GLS'in hiz sinirini korur."""
    for i in range(5):
        _delivered(db, f"3812017700000{i}")
    fetcher = FakeFetcher(db)

    Tracker(db=db, pod_fetcher=fetcher, max_pod_per_tick=2).tick()

    assert len(fetcher.seen) == 2


def test_one_broken_parcel_does_not_stop_the_tick(db):
    _delivered(db, "38120177000001")
    _delivered(db, "38120177000002")
    fetcher = FakeFetcher(db, boom={"38120177000001"})

    tracker = Tracker(db=db, pod_fetcher=fetcher, max_pod_per_tick=5)
    tracker.tick()

    assert db.get("38120177000002")["pod_path"]
    assert tracker.last_pod_count == 1


def test_without_a_fetcher_nothing_is_attempted(db):
    """CLI/testlerde POD adimi hic calismaz — GLS'e istek gitmez."""
    _delivered(db, "38120177000001")

    Tracker(db=db).tick()

    assert db.get("38120177000001")["pod_attempted_at"] is None


def test_pod_is_archived_in_the_same_tick_the_parcel_is_delivered(db, monkeypatch):
    """Teslimat ile POD arasinda bir tur (15 dk) beklenmez."""
    db.add_parcel(tracking_no="21569927889", channel="IE", reference="r",
                  consignee_name="c", country="IE")
    # IE artik T&T v1'in toplu ucune gidiyor (`_glsg_results`); seam orasi.
    monkeypatch.setattr(Tracker, "_glsg_results", lambda self, parcels, errors: [
        (p, {"status": "delivered", "note": "Delivered",
             "event_at": "2026-08-05T10:00:00Z",
             "delivered_date": "2026-08-05T10:00:00Z"})
        for p in parcels
    ])
    fetcher = FakeFetcher(db)

    Tracker(db=db, pod_fetcher=fetcher).tick()

    assert fetcher.seen == ["21569927889"]
    assert db.get("21569927889")["pod_path"]


# ======================================================================
# POD dosya adi — {MAGAZA}-{FATURA}, sira numarasi kalici
# ======================================================================
def test_single_box_invoice_keeps_the_plain_name(db):
    db.add_parcel(tracking_no="38120177000010", channel="NL",
                  store_code="NUNAT1N", invoice_number="5000202")
    assert db.reserve_pod_name("38120177000010", "NUNAT1N-5000202") == "NUNAT1N-5000202"


def test_multi_box_invoice_gets_a_sequence_number(db):
    """Bir fatura 34 koliye kadar cikabiliyor — adlar carpismamali."""
    for no in ("38120177000011", "38120177000012", "38120177000013"):
        db.add_parcel(tracking_no=no, channel="NL",
                      store_code="WQSCZ1N", invoice_number="5000314")
    names = [db.reserve_pod_name(no, "WQSCZ1N-5000314")
             for no in ("38120177000011", "38120177000012", "38120177000013")]
    assert names == ["WQSCZ1N-5000314-01", "WQSCZ1N-5000314-02", "WQSCZ1N-5000314-03"]


def test_reserved_name_never_changes(db):
    """Sonradan koli eklenince daha once gonderilmis belgenin adi kaymamali."""
    db.add_parcel(tracking_no="38120177000014", channel="NL",
                  store_code="TSOEL1N", invoice_number="5000171")
    first = db.reserve_pod_name("38120177000014", "TSOEL1N-5000171")

    db.add_parcel(tracking_no="38120177000015", channel="NL",
                  store_code="TSOEL1N", invoice_number="5000171")
    second = db.reserve_pod_name("38120177000015", "TSOEL1N-5000171")

    assert db.reserve_pod_name("38120177000014", "TSOEL1N-5000171") == first
    assert first == "TSOEL1N-5000171"          # tek koliyken sade ad verilmisti
    assert second == "TSOEL1N-5000171-01"      # yeni koli bir sonraki bos numarayi alir
