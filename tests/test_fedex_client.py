# -*- coding: utf-8 -*-
"""FedEx Track API testleri (mock sunucuya karşı) + `map_fedex_result` normalize testi.

Şema `developer.fedex.com`den indirilen gerçek OpenAPI dosyasıyla doğrulandı;
canlı sandbox'a (`apis-sandbox.fedex.com`) gerçek kimlik bilgileriyle de bir
kez elle test edildi (2026-08) — buradaki alan adları uydurma değil.

Çalıştırma: pytest -q tests/test_fedex_client.py
"""
import os

import pytest

from gls_api import providers
from gls_api.fedex_client import FedExClient, FedExError
from tracking.db import ShipmentsDB
from tracking.scheduler import Tracker, map_fedex_result

# Port conftest.py tarafindan secilir (bkz. oradaki aciklama).
BASE = f"http://127.0.0.1:{os.environ['GLS_MOCK_PORT']}"

KNOWN = "128667043726"     # mock_server.FEDEX_KNOWN_TRACKING icinde
UNKNOWN = "000000000000"


@pytest.fixture
def fedex():
    return FedExClient(base_url=f"{BASE}/fedex", api_key="mock", api_secret="mock")


# ======================================================================
# Istemci
# ======================================================================
def test_track_returns_the_known_parcel(fedex):
    output = fedex.track([KNOWN])
    results = output["completeTrackResults"]
    assert results[0]["trackingNumber"] == KNOWN
    assert results[0]["trackResults"][0]["latestStatusDetail"]["code"] == "HL"


def test_track_one_returns_none_for_unknown_number(fedex):
    """Bilinmeyen numara hata FIRLATMAZ — `error` alaniyla sonuc doner (gercek FedEx davranisi)."""
    result = fedex.track_one(UNKNOWN)
    assert result is not None
    assert result.get("error", {}).get("code") == "TRACKING.TRACKINGNUMBER.NOTFOUND"


def test_token_is_cached_between_calls(fedex):
    """Ayni istemci iki kez track() cagirinca token'i TEKRAR ISTEMEMELI."""
    fedex.track_one(KNOWN)
    token_after_first = fedex._token
    fedex.track_one(KNOWN)
    assert fedex._token == token_after_first


def test_track_rejects_more_than_thirty_numbers(fedex):
    with pytest.raises(FedExError):
        fedex.track([str(i) for i in range(31)])


def test_track_empty_list_short_circuits_without_a_request(fedex):
    assert fedex.track([]) == {"completeTrackResults": []}


# ======================================================================
# map_fedex_result — saf normalize fonksiyonu, ag YOK
# ======================================================================
def test_map_fedex_result_picks_delivered_from_date_and_times():
    result = {
        "latestStatusDetail": {"description": "Delivered"},
        "scanEvents": [
            {"date": "2026-08-20T09:00:00-05:00", "eventDescription": "Picked up"},
            {"date": "2026-08-22T15:30:00-05:00", "eventDescription": "Delivered"},
        ],
        "dateAndTimes": [
            {"type": "ACTUAL_PICKUP", "dateTime": "2026-08-20T09:00:00-05:00"},
            {"type": "ACTUAL_DELIVERY", "dateTime": "2026-08-22T15:30:00-05:00"},
        ],
    }
    out = map_fedex_result(result)
    assert out["status"] == "delivered"
    assert out["delivered_date"] == "2026-08-22T15:30:00-05:00"
    assert out["note"] == "Delivered"
    # En son olay TARIHE gore seciliyor, listedeki sira degil.
    assert out["event_at"] == "2026-08-22T15:30:00-05:00"


def test_map_fedex_result_builds_the_fedex_com_tracking_url():
    """FedEx yanitinda hic link/url alani yok (dogrulandi, 2026-08-31) —
    kullanicinin istegiyle GLS web linkleri yerine burada kuruluyor.
    Onceden: FedEx kolileri panelde yanlislikla GLS Irlanda linki aliyordu."""
    result = {
        "trackingNumberInfo": {"trackingNumber": "876365567966"},
        "latestStatusDetail": {"description": "Out for delivery"},
    }
    out = map_fedex_result(result)
    assert out["tracking_url"] == "https://www.fedex.com/fedextrack/?trknbr=876365567966"


def test_map_fedex_result_tracking_url_is_empty_without_a_tracking_number():
    assert map_fedex_result({})["tracking_url"] == ""


def test_map_fedex_result_picks_the_latest_scan_regardless_of_list_order():
    result = {
        "scanEvents": [
            {"date": "2026-08-22T10:00:00-05:00", "eventDescription": "In transit"},
            {"date": "2026-08-20T09:00:00-05:00", "eventDescription": "Picked up"},
        ],
    }
    out = map_fedex_result(result)
    assert out["event_at"] == "2026-08-22T10:00:00-05:00"
    assert out["note"] == "In transit"
    assert out["delivered_date"] == ""


def test_map_fedex_result_falls_back_to_latest_status_when_no_scans():
    result = {"latestStatusDetail": {"description": "Ready for pickup"}}
    out = map_fedex_result(result)
    assert out["note"] == "Ready for pickup"
    assert out["raw_events"] == []


def test_map_fedex_result_builds_raw_events_for_sync_events():
    result = {
        "scanEvents": [
            {"date": "2026-08-20T09:00:00-05:00", "eventDescription": "Picked up",
             "scanLocation": {"city": "Vancouver"}},
        ],
    }
    out = map_fedex_result(result)
    assert out["raw_events"] == [
        {"date": "2026-08-20T09:00:00-05:00", "description": "Picked up", "depotName": "Vancouver"},
    ]


# ======================================================================
# Tarayici entegrasyonu (kullanici istegi, 2026-08-31: production erisimi geldi —
# "tam entegre et, wp bildirimi haric")
# ======================================================================
@pytest.fixture
def tracker(tmp_path, monkeypatch):
    db = ShipmentsDB(path=tmp_path / "fedex.db")
    monkeypatch.setattr(providers, "_fedex",
                        FedExClient(base_url=f"{BASE}/fedex", api_key="mock", api_secret="mock"))
    monkeypatch.setattr(providers.config, "fedex_configured", lambda: True)
    return Tracker(db=db)


def test_a_fedex_parcel_is_tracked_through_a_full_tick(tracker):
    tracker.db.add_parcel(tracking_no=KNOWN, channel="FEDEX", reference="T-1",
                          country="CA", consignee_name="Test", status="created")
    tracker.tick()

    parcel = tracker.db.get(KNOWN)
    assert parcel["last_event_text"]
    assert tracker.last_error is None


def test_an_unknown_fedex_number_is_marked_checked_not_an_error(tracker):
    """FedEx numarayi henuz tanimiyor olabilir — GLS NL'in "bilinmeyen numara"
    davranisiyla ayni (bkz. Tracker._nl_results): hata degil, sessizce atlanir."""
    tracker.db.add_parcel(tracking_no=UNKNOWN, channel="FEDEX", reference="T-2",
                          country="CA", consignee_name="Test", status="created")
    tracker.tick()

    assert tracker.db.get(UNKNOWN)["status"] == "created"
    assert tracker.last_error is None


def test_fedex_deliveries_do_not_trigger_a_whatsapp_notification(tracker, monkeypatch):
    """Kullanici istegi (2026-08-31): "wp bildirimi haric" — FedEx TAKIP edilir
    ama WhatsApp'a hic gitmez, kanal GLS NL'den ayri tutulur."""
    from notify import whatsapp

    calls = []
    monkeypatch.setattr(whatsapp, "handle_tracker_event", lambda db, event: calls.append(event))
    tracker.on_change = lambda event: whatsapp.handle_tracker_event(tracker.db, event)

    tracker.db.add_parcel(tracking_no=KNOWN, channel="FEDEX", reference="T-3",
                          country="CA", consignee_name="Test", status="created")
    tracker.tick()

    assert len(calls) == 1
    assert calls[0]["channel"] == "FEDEX"
    # handle_tracker_event'in KENDISI de kanali kontrol eder (asagida gercek
    # fonksiyonla, monkeypatch olmadan) — burada sadece cagrildigini dogruluyoruz.


def test_handle_tracker_event_keeps_fedex_out_of_the_main_gls_group(tmp_path, monkeypatch):
    """FedEx ANA GLS grubuna girmez (kullanici karari 2026-08-31).

    2026-09-01'den beri tamamen susmuyor: KENDI grubuna bildiriyor (bkz.
    `notify_fedex_status`). Burada kanitlanan sey, ana grubun kullandigi
    `send_text`/`send_template` yolunun HIC calistirilmadigidir."""
    from unittest.mock import patch
    from gls_api import config
    from notify import whatsapp

    monkeypatch.setattr(config, "OPENWA_ENABLED", "1")   # bildirim ac, guard'i GERCEKTEN test et
    db = ShipmentsDB(path=tmp_path / "wa.db")
    db.add_parcel(tracking_no=KNOWN, channel="FEDEX", reference="T-4",
                  country="CA", consignee_name="Test", status="delivered")

    # FedEx grubu KAPALI: bu durumda hicbir sey gonderilmemeli.
    monkeypatch.setattr(config, "OPENWA_FEDEX_CHAT_ID", "")
    with patch("notify.whatsapp.send_text") as mock_send, \
         patch("notify.whatsapp.send_template") as mock_template:
        whatsapp.handle_tracker_event(db, {
            "tracking_no": KNOWN, "new_status": "delivered", "channel": "FEDEX",
        })

    mock_send.assert_not_called()
    mock_template.assert_not_called()


def test_a_fedex_event_goes_to_the_fedex_group_only(tmp_path, monkeypatch):
    """Grup TANIMLIYKEN mesaj gider — ama FedEx grubuna, ana gruba degil."""
    from gls_api import config
    from notify import whatsapp

    monkeypatch.setattr(config, "OPENWA_ENABLED", "1")
    monkeypatch.setattr(config, "OPENWA_FEDEX_CHAT_ID", "100000000000002@g.us")
    db = ShipmentsDB(path=tmp_path / "wa-fedex.db")
    db.add_parcel(tracking_no=KNOWN, channel="FEDEX", reference="T-5",
                  country="CA", consignee_name="Test", status="delivered")

    hedefler = []
    monkeypatch.setattr(whatsapp, "send_text",
                        lambda text, chat_id="", **kw: hedefler.append(chat_id) or (True, ""))

    whatsapp.handle_tracker_event(db, {
        "tracking_no": KNOWN, "new_status": "delivered", "channel": "FEDEX"})

    assert hedefler == ["100000000000002@g.us"]
    assert config.OPENWA_CHAT_ID not in hedefler
