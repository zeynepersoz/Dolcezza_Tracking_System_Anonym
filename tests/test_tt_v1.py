# -*- coding: utf-8 -*-
"""GLS Track & Trace v1 (OAuth 2.0 geçit) testleri.

Bu kanal grubun ortak geçidinden (api.gls-group.net) geçer: önce
client-credentials ile Bearer token alınır, sonra /track-and-trace-v1/...
çağrılır. Canlı doğrulanan uçlar (2026-09-02):
    GET /tracking/events/codes             -> 200
    GET /tracking/simple/trackids/{ids}    -> 200 (bilinmeyen numara errorCode)

Çalıştırma: pytest -q tests/test_tt_v1.py
"""
import os

import pytest
import requests

from gls_api import providers
from gls_api import config
from gls_api.tt_v1_client import (
    MAX_TRACKIDS_PER_CALL, GLSGroupError, TrackTraceV1Client)
from tracking.db import ShipmentsDB
from tracking.scheduler import Tracker, map_glsg_tt_parcel

BASE = f"http://127.0.0.1:{os.environ['GLS_MOCK_PORT']}"

KNOWN = "21569761233"        # mock_server.KNOWN_PARCELS
KNOWN2 = "21569761234"
UNKNOWN = "29999999999"


def _client():
    return TrackTraceV1Client(
        base_url=f"{BASE}/glsg",
        token_url=f"{BASE}/glsg/oauth2/v2/token",
        client_id="mock", client_secret="mock", scope="all",
    )


@pytest.fixture
def tt_v1():
    return _client()


# ======================================================================
# Token
# ======================================================================
def test_token_is_fetched_and_cached(tt_v1):
    first = tt_v1._bearer()
    assert first.startswith("mock-glsg-")
    assert tt_v1._bearer() == first          # ikinci çağrı önbellekten


def test_token_endpoint_failure_raises(monkeypatch):
    """Token isteği 200 dönmezse GLSGroupError — sessizce devam edilmez."""
    client = TrackTraceV1Client(
        base_url=f"{BASE}/glsg", token_url=f"{BASE}/glsg/does-not-exist",
        client_id="mock", client_secret="mock", scope="all")
    with pytest.raises(GLSGroupError) as e:
        client.event_codes()
    assert e.value.status == 404


def test_token_request_rejects_missing_grant_type():
    """Geçit, grant_type olmadan gelen token isteğini 400 ile reddeder."""
    resp = requests.post(f"{BASE}/glsg/oauth2/v2/token",
                         data={"client_id": "x", "client_secret": "y"})
    assert resp.status_code == 400


def test_scope_omitted_when_empty(tt_v1):
    """`scope=""` verilince istekte scope alanı hiç gitmemeli."""
    tt_v1.scope = ""
    assert tt_v1._bearer().startswith("mock-glsg-")


# ======================================================================
# Event kod sözlüğü — doctor bunu bağlantı testi olarak kullanır
# ======================================================================
def test_event_codes_returns_mapping(tt_v1):
    codes = tt_v1.event_codes()
    assert "DELIVD.NORMAL" in codes
    assert codes["DELIVD.NORMAL"]["codeNo"] == "3.0"
    assert "delivered" in codes["DELIVD.NORMAL"]["description"].lower()


def test_missing_bearer_is_rejected():
    """Geçit, token'sız isteği HTML gövdeli 401 ile reddeder."""
    resp = requests.get(f"{BASE}/glsg/track-and-trace-v1/tracking/events/codes")
    assert resp.status_code == 401
    assert "Authorization failed" in resp.text


# ======================================================================
# Takip
# ======================================================================
def test_track_simple_returns_known_parcel(tt_v1):
    found = tt_v1.track_simple([KNOWN])
    assert KNOWN in found
    p = found[KNOWN]
    assert p["unitno"] == KNOWN
    assert p["status"] == "DELIVERED"
    assert p["statusDateTime"]
    # canlı yanıt TAM olay geçmişini `simple` içinde verir, yeni -> eski sıralı
    assert p["events"] and p["events"][0]["code"] == "DELIVD.NORMAL"
    assert p["events"][0]["eventDateTime"]


def test_unknown_trackid_is_dropped_not_raised(tt_v1):
    """Bilinmeyen numara HATA DEĞİL: HTTP 200, sözlükte yer almaz."""
    found = tt_v1.track_simple([KNOWN, UNKNOWN])
    assert set(found) == {KNOWN}


def test_full_and_references_endpoints_do_not_exist(tt_v1):
    """Canlı bulgu: bu iki yol 404 "No static resource" döner (Spring varsayılanı).

    İstemcide `track()` / `references()` YOK; yanlışlıkla geri eklenirse bu
    test hatırlatır.
    """
    assert not hasattr(tt_v1, "track")
    assert not hasattr(tt_v1, "references")


def test_chunks_are_split_at_the_limit(tt_v1, monkeypatch):
    """10'dan fazla numara tek istekte gitmemeli."""
    calls = []
    real_get = tt_v1._get

    def spy(path, _retry=True):
        calls.append(path)
        return real_get(path, _retry)

    monkeypatch.setattr(tt_v1, "_get", spy)
    tt_v1.track_simple([str(20000000000 + i) for i in range(MAX_TRACKIDS_PER_CALL + 5)])
    assert len(calls) == 2
    assert calls[0].count(",") == MAX_TRACKIDS_PER_CALL - 1


def test_expired_token_is_refreshed_once(tt_v1):
    tt_v1.event_codes()                       # token'ı ısıt
    tt_v1._token = "mock-glsg-stale"          # geçiti reddettirecek sahte token
    tt_v1._token_expires_at = float("inf")    # süresi "dolmamış" görünsün
    # 401 -> zorla yenile -> tekrar dene -> 200
    assert "DELIVD.NORMAL" in tt_v1.event_codes()


# ======================================================================
# Günlük kota koruması
# ======================================================================
# Tarayıcı günde 96 tur atıyor; 63 aktif IE parçası 672 istek eder ve GLS'in
# 500/gün kotası daha ilk gün patlar (ölçüldü 2026-09-02, FA26). İstemci
# saydığı için tavana varınca istek ATILMAZ.
def test_budget_is_spent_per_request(tt_v1, monkeypatch):
    monkeypatch.setattr(config, "GLSG_DAILY_LIMIT", 5, raising=False)
    assert tt_v1.budget_left() == 5
    tt_v1.event_codes()
    assert tt_v1.budget_left() == 4


def test_requests_stop_when_the_daily_quota_runs_out(tt_v1, monkeypatch):
    monkeypatch.setattr(config, "GLSG_DAILY_LIMIT", 2, raising=False)
    tt_v1.event_codes()
    tt_v1.event_codes()
    assert tt_v1.budget_left() == 0

    # Üçüncü istek AĞA ÇIKMADAN reddedilir.
    def boom(*a, **kw):
        raise AssertionError("kota dolmuşken istek atılmamalıydı")

    monkeypatch.setattr(tt_v1.session, "get", boom)
    with pytest.raises(GLSGroupError) as e:
        tt_v1.event_codes()
    assert e.value.is_quota_error
    assert not e.value.is_auth_error


def test_the_counter_resets_on_a_new_utc_day(tt_v1, monkeypatch):
    monkeypatch.setattr(config, "GLSG_DAILY_LIMIT", 1, raising=False)
    tt_v1.event_codes()
    assert tt_v1.budget_left() == 0
    tt_v1._day = "2000-01-01"          # dün
    assert tt_v1.budget_left() == 1     # sayaç sıfırlandı


def test_the_tick_skips_ie_when_the_quota_is_gone(tmp_path, wired, monkeypatch):
    """Kota bitmişken tur hata YAĞDIRMAZ, IE parçaları sıraya bırakılır."""
    db = ShipmentsDB(path=tmp_path / "quota.db")
    db.add_parcel(tracking_no=KNOWN, channel="IE", reference="r",
                  consignee_name="c", country="IE", status="in_transit")

    monkeypatch.setattr(config, "GLSG_DAILY_LIMIT", 0, raising=False)
    tracker = Tracker(db=db)
    tracker.tick()

    assert db.get(KNOWN)["status"] == "in_transit"   # dokunulmadı
    assert tracker.last_error is None                # hata satırı yazılmadı


# ======================================================================
# providers / doctor entegrasyonu
# ======================================================================
def test_doctor_lists_the_channel(monkeypatch):
    monkeypatch.setattr(providers.config, "glsg_tt_configured", lambda: True)
    monkeypatch.setattr(providers, "get_glsg_tt", _client)
    names = {r["name"]: r for r in providers.doctor()}
    assert names["glsg_tt"]["ok"] is True
    assert "olay kodu" in names["glsg_tt"]["detail"] or "event codes" in names["glsg_tt"]["detail"]


def test_channel_for_infers_from_tracking_no():
    # kayıtlı değer güvenilirse ona uy
    assert providers.channel_for("21569761233", "NL") == "NL"
    assert providers.channel_for("38120177000001", "IE") == "IE"
    # boş/bilinmeyen kanal -> numaradan çıkar
    assert providers.channel_for("21569761233", "") == "IE"          # 11 hane
    assert providers.channel_for("38120177000001", "") == "NL"       # "38120177" öneki
    assert providers.channel_for("35000001406746", None) == "NL"     # 14 hane
    assert providers.channel_for("2156 9761 233", "??") == "IE"      # boşluklu


def test_ie_channel_routes_to_glsg(monkeypatch):
    monkeypatch.setattr(providers.config, "glsg_tt_configured", lambda: True)
    monkeypatch.setattr(providers, "get_glsg_tt", _client)
    name, client = providers.tracking_provider_for(providers.CHANNEL_IE)
    assert name == "glsg_tt"
    # POD orada YOK — POD sağlayıcısı hâlâ ShipIT/T&T SOAP.
    assert providers.pod_provider_for(providers.CHANNEL_IE)[0] != "glsg_tt"


# ======================================================================
# map_glsg_tt_parcel — ortak sözlüğe çevrim
# ======================================================================
# 21569979615 — canlı `simple/trackids` yanıtından (2026-09-02), aynen.
REAL_DELIVERED = {
    "requested": "21569979615",
    "unitno": "21569979615",
    "status": "DELIVERED",
    "statusDateTime": "2026-07-29T10:02:00+0200",
    "events": [
        {"code": "DELIVD.NORMAL", "city": "", "postalCode": "", "country": "IE",
         "description": "The parcel has been delivered.",
         "eventDateTime": "2026-07-29T10:02:00+0200"},
        {"code": "OUTDEL.NORMAL", "city": "", "postalCode": "", "country": "IE",
         "description": "The parcel is expected to be delivered during the day.",
         "eventDateTime": "2026-07-29T06:52:37+0200"},
        {"code": "INBOUD.NORMAL", "city": "", "postalCode": "", "country": "IE",
         "description": "The parcel has reached the parcel center.",
         "eventDateTime": "2026-07-29T06:52:36+0200"},
        {"code": "INTIAL.NORMAL", "city": "", "postalCode": "", "country": "IE",
         "description": "The parcel was handed over to GLS.",
         "eventDateTime": "2026-07-28T18:00:52+0200"},
        {"code": "INTIAL.PREADVICE", "city": "", "postalCode": "", "country": "IE",
         "description": "The parcel data was entered into the GLS IT system; "
                        "the parcel was not yet handed over to GLS.",
         "eventDateTime": "2026-07-14T16:56:07+0200"},
    ],
}


def test_mapper_real_delivered_response():
    info = map_glsg_tt_parcel(REAL_DELIVERED)
    assert info["status"] == "delivered"
    # son olay = teslim (yeni->eski gelen listeyi eskiden yeniye çevirdik)
    assert info["note"] == "The parcel has been delivered."
    assert info["event_at"] == "2026-07-29T10:02:00+0200"
    assert info["delivered_date"] == "2026-07-29T10:02:00+0200"
    # ilk gerçek hareket: preadvice DEĞİL, "handed over to GLS"
    assert info["handed_over_at"] == "2026-07-28T18:00:52+0200"
    assert info["issue_text"] == ""
    assert info["tracking_url"].endswith("21569979615")
    # tam geçmiş status_events'e yazılsın diye eskiden yeniye sıralı
    assert [e["code"] for e in info["raw_events"]][:2] == ["INTIAL.PREADVICE", "INTIAL.NORMAL"]
    assert info["raw_events"][-1]["code"] == "DELIVD.NORMAL"


def test_mapper_status_as_object():
    """`status` düz metin yerine {code/name} sözlüğü de olabilir (savunma)."""
    info = map_glsg_tt_parcel({
        "unitno": KNOWN,
        "status": {"code": "OUTFORDELIVERY", "name": "Out for delivery"},
        "statusDateTime": "2026-08-03T07:00:00+0200",
        "events": [{"code": "OUTDEL.NORMAL", "eventDateTime": "2026-08-03T07:00:00+0200",
                    "description": "The parcel is expected to be delivered during the day."}],
    })
    assert info["status"] == "out_for_delivery"


def test_mapper_flags_a_blocking_issue():
    parcel = {
        "unitno": KNOWN, "status": "INWAREHOUSE",
        "events": [
            {"code": "NOTDEL.ABSENT", "eventDateTime": "2026-08-02T09:00:00+0200",
             "description": "The parcel could not be delivered as the consignee was absent."},
            {"code": "INBOUD.NORMAL", "eventDateTime": "2026-08-01T09:00:00+0200",
             "description": "The parcel has reached the parcel center."},
        ],
    }
    info = map_glsg_tt_parcel(parcel)
    assert info["status"] == "exception"
    assert "could not be delivered" in info["issue_text"].lower()


def test_mapper_survives_status_only_no_events():
    """Olay listesi hiç gelmezse (savunma) status'tan yürür."""
    info = map_glsg_tt_parcel({"unitno": KNOWN, "status": "INTRANSIT"})
    assert info["status"] == "in_transit"
    assert info["raw_events"] is None


# ======================================================================
# Uçtan uca: Tracker.tick() IE parçalarını T&T v1'den tarar
# ======================================================================
@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(providers.config, "glsg_tt_configured", lambda: True)
    monkeypatch.setattr(providers, "get_glsg_tt", _client)
    monkeypatch.setattr("tracking.scheduler.config.glsg_tt_configured", lambda: True)


def test_tick_updates_ie_parcels_via_ttv1(tmp_path, wired):
    db = ShipmentsDB(path=tmp_path / "ttv1.db")
    db.add_parcel(tracking_no=KNOWN, channel="IE", reference="r1",
                  consignee_name="c", country="IE", status="in_transit")
    db.add_parcel(tracking_no=KNOWN2, channel="IE", reference="r2",
                  consignee_name="c", country="IE", status="created")

    tracker = Tracker(db=db)
    tracker.tick()

    assert db.get(KNOWN)["status"] == "delivered"
    assert db.get(KNOWN2)["status"] == "delivered"
    assert db.get(KNOWN)["last_event_text"]
    # simple/trackids TAM geçmişi verir -> status_events'e birden çok satır
    assert len(db.events_for(KNOWN)) >= 3
    assert tracker.last_error is None


def test_tick_marks_unknown_ie_parcel_checked_without_crashing(tmp_path, wired):
    db = ShipmentsDB(path=tmp_path / "ttv1b.db")
    db.add_parcel(tracking_no=UNKNOWN, channel="IE", reference="r",
                  consignee_name="c", country="IE", status="created")

    Tracker(db=db).tick()

    row = db.get(UNKNOWN)
    assert row["status"] == "created"           # GLS tanımıyor, dokunulmadı
    assert row["last_checked_at"] is not None   # ama "bakıldı" işaretlendi
