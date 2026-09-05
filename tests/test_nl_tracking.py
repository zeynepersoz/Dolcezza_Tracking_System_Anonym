# -*- coding: utf-8 -*-
"""GLS Netherlands takip (Track&Trace REST) testleri.

Bu kanal uzun sure "sorgulanamaz" varsayildigi icin NL parcalari hic taranmadi
ve statuler yalnizca Excel'den geldigi haliyle kaldi. Buradaki testler o
varsayimin geri gelmesini engeller.

Calistirma: pytest -q tests/test_nl_tracking.py
"""
import os

import pytest
import requests

from gls_api import providers
from gls_api.nl_client import GLSNLError
from gls_api.nl_track_client import (
    MAX_PARCELS_PER_CALL, GLSNLTrackClient, _pod_job_date, _trim_padding)
from tracking.db import ShipmentsDB
from tracking.scheduler import Tracker, _fix_mojibake, map_nl_tt_parcel, _live_delivery_scan

# Port conftest.py tarafindan secilir (bkz. oradaki aciklama).
BASE = f"http://127.0.0.1:{os.environ['GLS_MOCK_PORT']}"

KNOWN = "35000001406746"   # mock_server.KNOWN_PARCELS icindeki NL numarasi
UNKNOWN = "39999999999999"


@pytest.fixture
def nl_tt():
    return GLSNLTrackClient(base_url=f"{BASE}/nltt", username="mock", password="mock",
                            pod_base_url=f"{BASE}/nlpod")


# ======================================================================
# Istemci
# ======================================================================
def test_uses_username_field_with_capital_n(nl_tt):
    """Kimlik alani `userName` — etiket API'sindeki `username` DEGIL.

    Mock sunucu `userName` yoksa 401 doner; basarili cagri alanin dogru
    yazildigini kanitlar.
    """
    assert KNOWN in nl_tt.parcel_details([KNOWN])


def test_label_api_field_name_is_rejected():
    """`username` burada kimlik SAYILMAZ — takip ucunun alani `userName`.

    Iki servis ayni kimlikleri paylastigi icin bu fark kolayca gozden kacar ve
    sonucu 401'dir, "parca bulunamadi" degil.
    """
    resp = requests.post(f"{BASE}/nltt/api/parcel/v1/details",
                         json={"username": "mock", "password": "mock",
                               "parcelNumbers": [KNOWN]})
    assert resp.status_code == 401


def test_unknown_parcel_is_dropped_not_raised(nl_tt):
    """GLS bilinmeyen numarayi hata vermeden yanittan duser."""
    found = nl_tt.parcel_details([KNOWN, UNKNOWN])
    assert KNOWN in found
    assert UNKNOWN not in found


def test_parcel_detail_returns_none_when_missing(nl_tt):
    assert nl_tt.parcel_detail(UNKNOWN) is None


def test_splits_into_batches_of_twenty(nl_tt, monkeypatch):
    """GLS tek istekte en fazla 20 numara kabul eder."""
    chunks: list[list[str]] = []

    def fake_post(path, payload):
        chunks.append(payload["parcelNumbers"])
        return {"parcels": []}

    monkeypatch.setattr(nl_tt, "_post", fake_post)
    nl_tt.parcel_details([str(i) for i in range(45)])

    assert [len(c) for c in chunks] == [MAX_PARCELS_PER_CALL, MAX_PARCELS_PER_CALL, 5]


def test_blank_numbers_are_skipped(nl_tt, monkeypatch):
    sent = []
    monkeypatch.setattr(nl_tt, "_post", lambda p, b: sent.append(b["parcelNumbers"]) or {})
    nl_tt.parcel_details(["  ", "", "123 "])
    assert sent == [["123"]]


# ======================================================================
# Eslestirme
# ======================================================================
def _parcel(state, events):
    return {"parcelNo": KNOWN, "state": state, "events": events}


def test_delivered_uses_delivery_event_date_not_last_event():
    """Teslimat olayindan SONRA baska olay gelirse teslim tarihi kaymamali."""
    info = map_nl_tt_parcel(_parcel("Delivered", [
        {"descriptionEN": "The parcel has been delivered.",
         "date": "2026-07-30T08:05:48Z", "details": "J. Doe", "depotName": "Nancy FR0054"},
        {"descriptionEN": "Data record archived.", "date": "2026-08-02T00:00:00Z"},
    ]))
    assert info["status"] == "delivered"
    assert info["delivered_date"] == "2026-07-30T08:05:48Z"
    assert info["event_at"] == "2026-08-02T00:00:00Z"


def test_delivered_note_carries_receiver_name():
    info = map_nl_tt_parcel(_parcel("Delivered", [
        {"descriptionEN": "The parcel has been delivered.",
         "date": "2026-07-30T08:05:48Z", "details": "LÃ©onie"},
    ]))
    assert info["note"] == "Teslim edildi, teslim alan: Léonie"


def test_state_maps_without_reading_event_text():
    assert map_nl_tt_parcel(_parcel("InRegion", []))["status"] == "out_for_delivery"
    assert map_nl_tt_parcel(_parcel("Announced", []))["status"] == "created"


def test_unknown_state_falls_back_to_event_text():
    """GLS yeni durum adlari ekliyor; bilinmeyen deger 'in_transit'e SABITLENMEZ.

    Sabitlenirse teslim edilmis bir parca sonsuza kadar aktif gorunur ve her
    taramada tekrar sorgulanir.
    """
    info = map_nl_tt_parcel(_parcel("SomeNewGlsState", [
        {"descriptionEN": "The parcel has been delivered.", "date": "2026-07-30T08:05:48Z"},
    ]))
    assert info["status"] == "delivered"


DELETED = "The parcel data have been deleted from the GLS IT system."


def test_deleted_parcel_data_is_not_a_cancellation():
    """GLS'in "veriler silindi" olayi iptal DEGIL, on-duyuru temizligidir.

    Bir sure bu metin `cancelled` yaziyordu; `cancelled` takip kuyrugundan
    kalici olarak dusuruldugu icin parca bir daha hic sorulmuyordu. Statuyu
    GLS'in kendi `state`i belirlemeli.
    """
    info = map_nl_tt_parcel(_parcel("Announced", [
        {"descriptionEN": DELETED,
         "date": "2026-08-05T09:00:00Z", "depotName": "Puurs BE 061"},
    ]))
    assert info["status"] == "created"


def test_a_purged_parcel_can_come_back_to_life():
    """Canli 38120177007435: silme olayindan 9 gun SONRA gercekten yola cikti.

    Boyle 14 parca vardi ve hepsi GLS'te yasiyordu; yerel kayit "iptal" diyordu.
    """
    info = map_nl_tt_parcel(_parcel("Delivered", [
        {"descriptionEN": ENTERED, "date": "2026-07-21T09:00:00Z"},
        {"descriptionEN": DELETED, "date": "2026-08-05T09:00:00Z",
         "depotName": "Puurs BE 061"},
        {"descriptionEN": HANDED, "date": "2026-08-14T09:00:00Z"},
        {"descriptionEN": "The parcel has been delivered.",
         "date": "2026-08-17T09:00:00Z"},
    ]))
    assert info["status"] == "delivered"
    assert info["handed_over_at"] == "2026-08-14T09:00:00Z"


def test_empty_parcel_does_not_crash():
    info = map_nl_tt_parcel({"parcelNo": KNOWN})
    assert info == {"status": "created", "note": "", "raw_note": "",
                    "event_at": "", "delivered_date": "",
                    "handed_over_at": "", "issue_text": "",
                    "tracking_url": f"https://www.gls-info.nl/track-and-trace?parcelno={KNOWN}",
                    "raw_events": []}


# ======================================================================
# Teslim alma ani ve sorun tespiti
# ======================================================================
ENTERED = "The parcel data was entered into the GLS IT system; the parcel was not yet handed over to GLS."
HANDED = "The parcel was handed over to GLS."


def test_preadvice_is_not_a_handover():
    """"Consignee contacted/preadvice" GLS'in koliyi ALDIGI anlamina gelmez.

    Canlida 163 parcada bu olay var ve buyuk cogunlugu hala depoda; kullanici
    dogruladi: dagitici aliciyla konusmus, gonderi normal seyrinde.
    """
    info = map_nl_tt_parcel(_parcel("Announced", [
        {"descriptionEN": ENTERED, "date": "2026-07-21T00:00:00Z"},
        {"descriptionEN": "Consignee contacted/preadvice data",
         "date": "2026-07-28T00:00:00Z", "depotName": "Budapest HU0010"},
    ]))
    assert info["handed_over_at"] == ""
    assert info["issue_text"] == ""
    assert info["status"] == "created"


def test_handover_is_the_first_real_scan():
    info = map_nl_tt_parcel(_parcel("Announced", [
        {"descriptionEN": ENTERED, "date": "2026-07-21T00:00:00Z"},
        {"descriptionEN": HANDED, "date": "2026-07-23T09:00:00Z"},
        {"descriptionEN": "The parcel has left the parcel center.",
         "date": "2026-07-24T09:00:00Z"},
    ]))
    assert info["handed_over_at"] == "2026-07-23T09:00:00Z"


def test_issue_is_found_in_the_middle_of_the_history():
    """Sorun olayi SON olay olmayabilir — canli ornek 38120177004885.

    4 Agu teslim, 5 Agu "alici kabul etmedi", ardindan iade; son olay masum.
    Yalnizca son olaya bakan bir kural bu parcayi hic gormez.
    """
    info = map_nl_tt_parcel(_parcel("InRegion", [
        {"descriptionEN": HANDED, "date": "2026-08-01T09:00:00Z"},
        {"descriptionEN": "The parcel could not be delivered as the recipient refused acceptance.",
         "date": "2026-08-05T09:00:00Z", "depotName": "Madrid ES"},
        {"descriptionEN": "The parcel has left the parcel center.",
         "date": "2026-08-06T09:00:00Z"},
    ]))
    assert info["status"] == "exception"
    assert info["issue_text"] == (
        "The parcel could not be delivered as the recipient refused acceptance.")


def test_routine_events_are_not_issues():
    info = map_nl_tt_parcel(_parcel("InRegion", [
        {"descriptionEN": HANDED, "date": "2026-08-01T09:00:00Z"},
        {"descriptionEN": "Relabelled/normal", "date": "2026-08-02T09:00:00Z"},
        {"descriptionEN": "Change completed for/Weight", "date": "2026-08-03T09:00:00Z"},
        {"descriptionEN": "Email with delivery advice notification",
         "date": "2026-08-04T09:00:00Z"},
    ]))
    assert info["issue_text"] == ""
    assert info["status"] == "out_for_delivery"


def test_a_retry_after_a_failed_delivery_closes_the_issue():
    """GLS basarisiz teslimati ertesi gun tekrar dener — bu sorun DEGIL.

    Canli ornek 38120177014105: 10 Agu "teslim edilemedi", 11 Agu "bugun
    dagitima cikacak". Kullanici hakli olarak "INDELIVERY olmasi gerekirken
    EXCEPTION" dedi. Olcum (2026-08-11): 26 sorunlunun 14'u boyleydi.
    """
    info = map_nl_tt_parcel(_parcel("OutForDelivery", [
        {"descriptionEN": HANDED, "date": "2026-08-06T09:00:00Z"},
        {"descriptionEN": "The parcel could not be delivered due to exceeded time frame.",
         "date": "2026-08-10T12:45:00Z"},
        {"descriptionEN": "The parcel is expected to be delivered during the day.",
         "date": "2026-08-11T05:13:00Z"},
    ]))
    assert info["status"] == "out_for_delivery"
    assert info["issue_text"] == ""


def test_a_failed_delivery_after_the_retry_is_still_an_issue():
    """Kurtarma olayindan SONRA yeniden basarisiz olduysa sorun geri aciliyor."""
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": HANDED, "date": "2026-08-06T09:00:00Z"},
        {"descriptionEN": "The parcel is expected to be delivered during the day.",
         "date": "2026-08-10T05:00:00Z"},
        {"descriptionEN": "The parcel could not be delivered as the reception was closed.",
         "date": "2026-08-10T15:00:00Z", "depotName": "Fiumicino ITR101"},
    ]))
    assert info["status"] == "exception"


def test_a_stale_state_does_not_hide_a_real_return():
    """GLS `state`i iadede de bayatlayabiliyor — teslimattaki AYNI kusur.

    Canli olcum (2026-08-26): 38120177014396 gercekten iade edilmisti ama GLS
    `state=OutForDelivery` diyordu; iade sonrasi gelen "Change completed for
    Delivery address" kayit-kapatma olayi hem son olay metnini degistiriyor
    hem eskiden EXCEPTION_PHRASES'teki cıplak "address"e takilip yanlislikla
    "exception" yaziyordu. Kullanici bunu fark edip bildirdi.
    """
    info = map_nl_tt_parcel(_parcel("OutForDelivery", [
        {"descriptionEN": "The parcel has left the parcel center.",
         "date": "2026-08-24T08:08:24Z", "depotName": "GLS.BARCELONA ES H00"},
        {"descriptionEN": "The parcel has returned to sender.",
         "date": "2026-08-25T07:53:23Z", "depotName": "Madrid ES0028"},
        {"descriptionEN": "Change completed for Delivery address",
         "date": "2026-08-25T07:54:12Z", "depotName": "Madrid ES0028"},
    ]))
    assert info["status"] == "returned"


def test_gls_saying_out_for_delivery_right_now_beats_an_old_issue():
    """GLS'in SU ANKI `state`i acikca 'dagitimda' diyorsa, gecmisteki eski bir
    sorun olayi bunu ezmemeli.

    Canli olcum (2026-08-27, kullanici bildirdi): "70 kusur dagitimda gonderi
    olmasina ragmen 12 tane gozukuyor". GLS'e sorulunca 20 koli icin
    `state=InRegion` cikti, 8'i bizde hala "exception" gorunuyordu — gecmiste
    bir "stored in the parcel center" olayindan sonra net bir kurtarma
    ("expected to be delivered") hic gelmemisti, ama GLS zaten "bugun
    dagitimda" diyordu.
    """
    info = map_nl_tt_parcel(_parcel("InRegion", [
        {"descriptionEN": HANDED, "date": "2026-08-20T09:00:00Z"},
        {"descriptionEN": "The parcel is stored in the parcel center.",
         "date": "2026-08-24T03:15:16Z", "depotName": "Lublin PL2600"},
        {"descriptionEN": "The parcel has reached the parcel center.",
         "date": "2026-08-26T06:54:32Z", "depotName": "Regional HUB Gluchowo PL0620"},
    ]))
    assert info["status"] == "out_for_delivery"
    assert info["issue_text"] == ""


def test_a_resolved_access_problem_does_not_hold_forever():
    """"Tatilde"/"resepsiyon kapali"/"adreste yok" GECICIDIR — sert bir
    musteri/nakliye basarisizligi degil, GLS kendiliginden tekrar dener.

    Canli olcum (2026-08-27): yukaridaki duzeltmeden SONRA bile 38120177009255
    ve 38120177014419 hala "exception" gorunuyordu. 014419'da sorun 16 GUN
    once ("reception is closed") gecmisti, ardindan GLS'in kendi state'i
    defalarca InRegion'a donmus, hicbir yeni sorun olayi gelmemisti — eski
    "HARD" sinif ("cannot be delivered"/"not out for delivery") net kurtarma
    beklemeye devam ettigi icin bu koliler sonsuza dek sorunlu kaliyordu.
    """
    info = map_nl_tt_parcel(_parcel("InRegion", [
        {"descriptionEN": HANDED, "date": "2026-08-06T09:00:00Z"},
        {"descriptionEN": "The parcel is stored in the parcel center. "
                          "It cannot be delivered as the reception is closed.",
         "date": "2026-08-10T05:48:33Z"},
        {"descriptionEN": "Not out for delivery/Consignee absent",
         "date": "2026-08-10T05:54:28Z"},
        {"descriptionEN": "The parcel is stored in the parcel center.",
         "date": "2026-08-24T04:26:25Z"},
        {"descriptionEN": "The parcel is stored in the parcel center.",
         "date": "2026-08-26T04:31:12Z"},
    ]))
    assert info["status"] == "out_for_delivery"
    assert info["issue_text"] == ""


def test_a_genuine_hard_failure_still_holds_despite_live_ofd():
    """Ret/yanlis yonlendirme gibi GERCEK bir basarisizlik yumusatilmaz.

    Canli olcum (2026-08-27): 38120177018301 ayni gun "Forwarded misrouted"
    aldi, GLS state hala InRegion'du — bu tatil/kapali resepsiyon gibi
    kendiliginden gecen bir durum degil, gercek bir sevkiyat hatasi.
    """
    info = map_nl_tt_parcel(_parcel("InRegion", [
        {"descriptionEN": HANDED, "date": "2026-08-25T11:13:56Z"},
        {"descriptionEN": "The parcel has reached the parcel center.",
         "date": "2026-08-27T03:49:44Z"},
        {"descriptionEN": "Forwarded misrouted", "date": "2026-08-27T03:57:12Z"},
    ]))
    assert info["status"] == "exception"
    assert info["issue_text"] == "Forwarded misrouted"


def test_an_old_issue_still_holds_when_gls_state_does_not_say_ofd():
    """Duzeltme asiri gitmemeli: GLS state acikca OFD demiyorsa eski kural gecerli."""
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": HANDED, "date": "2026-08-20T09:00:00Z"},
        {"descriptionEN": "The parcel is stored in the parcel center.",
         "date": "2026-08-24T03:15:16Z", "depotName": "Lublin PL2600"},
    ]))
    assert info["status"] == "exception"


def test_a_stale_notdelivered_state_does_not_hide_a_real_return():
    """Ayni kusur `state=NotDelivered` icin — canli 38120177014402/012897."""
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": "The parcel is stored in the parcel center.",
         "date": "2026-08-24T07:59:35Z", "depotName": "GLS.BARCELONA ES H00"},
        {"descriptionEN": "The parcel has returned to sender.",
         "date": "2026-08-24T08:02:58Z", "depotName": "GLS.BARCELONA ES H00"},
        {"descriptionEN": "The parcel has reached the parcel center.",
         "date": "2026-08-26T06:54:32Z", "depotName": "Regional HUB Gluchowo PL0620"},
    ]))
    assert info["status"] == "returned"


def test_delivered_still_wins_over_an_older_return():
    """Iade sonrasi GERCEKTEN teslim edilmisse (yeniden gonderim), teslimat kazanir."""
    info = map_nl_tt_parcel(_parcel("Delivered", [
        {"descriptionEN": "The parcel has returned to sender.",
         "date": "2026-08-10T09:00:00Z"},
        {"descriptionEN": "The parcel has been delivered.",
         "date": "2026-08-20T09:00:00Z"},
    ]))
    assert info["status"] == "delivered"


def test_a_stale_state_does_not_hide_a_delivery():
    """Son olay teslimatsa koli teslim edilmistir — `state` yanilabilir.

    Canli ornek 38120177004885: yon degistirip 7 Agu'da teslim edildi ama GLS
    `state` alanini "InRegion" birakti; panelde teslim gorunmuyordu.
    """
    info = map_nl_tt_parcel(_parcel("InRegion", [
        {"descriptionEN": HANDED, "date": "2026-08-01T09:00:00Z"},
        {"descriptionEN": "The parcel has been delivered.",
         "date": "2026-08-07T07:29:14Z", "details": "Hoppe"},
    ]))
    assert info["status"] == "delivered"
    assert info["delivered_date"] == "2026-08-07T07:29:14Z"


def test_damage_does_not_stop_the_parcel():
    """Hasar teslimati DURDURMAZ — GLS koliyi onarip yola devam ediyor.

    Canli ornek 38120177012132: "Inbound damaged" ardindan "Check scan
    Packaging improved" ve "left the parcel center". Kullanicinin ekraninda
    ERP "EXCEPTION" derken GLS'in kendi sitesi "bugun teslim" diyordu.
    Statu GLS ne diyorsa odur; koli yine de sorunlu listesinde gorunur cunku
    icindeki giysi zarar gormus olabilir.
    """
    info = map_nl_tt_parcel(_parcel("Received", [
        {"descriptionEN": HANDED, "date": "2026-08-05T09:00:00Z"},
        {"descriptionEN": "Inbound damaged", "date": "2026-08-06T22:35:36Z",
         "depotName": "Turku FI2020"},
        {"descriptionEN": "Check scan Packaging improved", "date": "2026-08-06T22:36:54Z"},
    ]))
    assert info["status"] == "in_transit"
    assert info["issue_text"] == "Inbound damaged"


def test_a_blocking_issue_still_wins_over_damage():
    """Hasarin ardindan teslimati durduran bir olay gelirse statu yine exception."""
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": HANDED, "date": "2026-08-05T09:00:00Z"},
        {"descriptionEN": "Inbound damaged", "date": "2026-08-06T22:35:36Z"},
        {"descriptionEN": "The parcel could not be delivered, consignee not to locate.",
         "date": "2026-08-08T10:00:00Z"},
    ]))
    assert info["status"] == "exception"


def test_delivered_parcel_is_never_an_issue():
    """Resepsiyon kapaliydi ama koli ertesi gun teslim edildi — sorun bitti."""
    info = map_nl_tt_parcel(_parcel("Delivered", [
        {"descriptionEN": HANDED, "date": "2026-08-01T09:00:00Z"},
        {"descriptionEN": "The parcel could not be delivered as the reception was closed.",
         "date": "2026-08-04T09:00:00Z"},
        {"descriptionEN": "The parcel has been delivered.", "date": "2026-08-05T09:00:00Z"},
    ]))
    assert info["status"] == "delivered"
    assert info["issue_text"] == ""


def test_the_depot_name_stays_out_of_the_note():
    """Aciklamaya depo adi yapistirilmaz.

    Yapistirildiginda ayni olay her depoda ayri bir metin oluyordu; metin ERP'ye
    (`UD_TasimaAciklama`) ve musteriye giden Excel'e de gidiyor, orada depo
    kodunun isi yok. Konum parcanin kendi alanlarinda zaten duruyor.
    """
    info = map_nl_tt_parcel(_parcel("InRegion", [
        {"descriptionEN": "The parcel has reached the parcel center.",
         "date": "2026-07-29T10:00:00Z", "depotName": "Nancy FR0054"},
    ]))
    assert info["note"] == "The parcel has reached the parcel center."


@pytest.mark.parametrize("raw,expected", [
    ("LÃ©onie", "Léonie"),        # cift kodlanmis UTF-8 -> duzeltilir
    ("Léonie", "Léonie"),         # zaten dogru -> dokunulmaz
    ("Şeyma Ersöz", "Şeyma Ersöz"),   # latin-1'de olmayan harf -> dokunulmaz
    ("J. DOE", "J. DOE"),
])
def test_fix_mojibake(raw, expected):
    assert _fix_mojibake(raw) == expected


# ======================================================================
# POD (apm.gls.nl ucu)
# ======================================================================
@pytest.mark.parametrize("raw, expected", [
    # Takip ucu UTC verir, POD ucu Hollanda yerel gece yarisini bekler.
    ("2026-07-07T22:00:00Z", "2026-07-08T00:00:00"),   # yaz saati (+2)
    ("2026-01-14T23:00:00Z", "2026-01-15T00:00:00"),   # kis saati (+1)
    ("2026-07-08T00:00:00", "2026-07-08T00:00:00"),    # saat dilimsiz -> dokunulmaz
])
def test_job_date_is_converted_to_local_midnight(raw, expected):
    """Yanlis gonderilirse hata degil 204 doner; yani sessizce 'POD yok' olur."""
    assert _pod_job_date(raw) == expected


def test_pod_returns_image_bytes(nl_tt):
    data, media_type = nl_tt.parcel_pod(KNOWN)
    assert media_type == "bmp"
    assert data.startswith(b"BM")


def test_pod_padding_is_trimmed(nl_tt):
    """Gercek uc icerigi 128 KB'a sifirla doldurur; arsive dolgu yazmayalim."""
    import struct
    data, _ = nl_tt.parcel_pod(KNOWN)
    assert len(data) < 131072
    assert len(data) == struct.unpack("<I", data[2:6])[0]


def test_jpeg_padding_is_trimmed_at_eoi():
    """Canli veride JPEG POD'lar 64 KB'a dolduruluyordu (35492 -> 65536)."""
    jpeg = b"\xff\xd8\xff" + b"govde" + b"\xff\xd9"
    assert _trim_padding(jpeg.ljust(65536, b"\x00")) == jpeg


def test_png_trailing_zeros_are_kept():
    """Kor 'sondaki sifirlari kes' PNG/TIFF verisini bozar; bicime bakilmali."""
    png = b"\x89PNG\r\n\x1a\n" + b"veri\x00\x00"
    assert _trim_padding(png) == png


def test_pod_content_type_header_is_not_trusted(nl_tt):
    """Uc nokta "image/png" der ama icerik BMP'dir; bicim sihirli bayttan gelir."""
    resp = requests.post(f"{BASE}/nlpod/api/tracktrace/v1/{KNOWN}/pod",
                         json={"uniqueNo": "X", "jobDate": "2026-01-01T00:00:00"})
    _, media_type = nl_tt.parcel_pod(KNOWN)
    assert resp.status_code == 204          # yanlis uniqueNo -> sessiz 204
    assert media_type != "png"


def test_pod_requires_unique_no():
    """uniqueNo eksikse 400; bu alan yalnizca takip yanitindan ogrenilebilir."""
    resp = requests.post(f"{BASE}/nlpod/api/tracktrace/v1/{KNOWN}/pod", json={})
    assert resp.status_code == 400
    assert "UniqueNo" in resp.json()["errors"]


def test_missing_job_date_returns_204_not_error():
    """jobDate unutulursa uc nokta HATA VERMEZ, bos doner — sessizce yanlis olur."""
    detail = requests.post(f"{BASE}/nltt/api/parcel/v1/details",
                           json={"userName": "mock", "password": "mock",
                                 "parcelNumbers": [KNOWN]}).json()["parcels"][0]
    resp = requests.post(f"{BASE}/nlpod/api/tracktrace/v1/{KNOWN}/pod",
                         json={"uniqueNo": detail["uniqueNo"]})
    assert resp.status_code == 204


def test_pod_for_unknown_parcel_raises(nl_tt):
    with pytest.raises(GLSNLError):
        nl_tt.parcel_pod(UNKNOWN)


# ======================================================================
# Saglayici yonlendirmesi
# ======================================================================
def test_nl_uses_same_client_for_tracking_and_pod():
    """NL'de POD ayri bir host'ta ama ayni istemci uzerinden alinir."""
    assert providers.tracking_provider_for(providers.CHANNEL_NL)[0] == "nl_tt"
    assert providers.pod_provider_for(providers.CHANNEL_NL)[0] == "nl_tt"


def test_nl_tracking_never_falls_back_to_another_provider(monkeypatch):
    """NL parca numarasi baska hesapta tanimsizdir; dusmek sessizce yanlis olur."""
    monkeypatch.setattr(providers.config, "nl_tt_configured", lambda: False)
    assert providers.tracking_provider_for(providers.CHANNEL_NL) is None


# ---------- FedEx yonlendirmesi (kullanici istegi, 2026-08-31: production erisimi geldi) ----------

def test_fedex_tracking_provider_is_used_when_configured(monkeypatch):
    monkeypatch.setattr(providers.config, "fedex_configured", lambda: True)
    name, client = providers.tracking_provider_for(providers.CHANNEL_FEDEX)
    assert name == "fedex"


def test_fedex_tracking_is_none_when_not_configured_and_does_not_fall_back(monkeypatch):
    """ShipIT/TT yapilandirilmis olsa bile FedEx numarasi onlara DUSMEMELI —
    baska bir tasiyicinin hesabinda FedEx numarasi tanimsizdir."""
    monkeypatch.setattr(providers.config, "fedex_configured", lambda: False)
    monkeypatch.setattr(providers.config, "shipit_configured", lambda: True)
    monkeypatch.setattr(providers.config, "tt_configured", lambda: True)
    assert providers.tracking_provider_for(providers.CHANNEL_FEDEX) is None


def test_fedex_has_no_pod_provider(monkeypatch):
    """FedEx istemcisinde POD ucu yok — ShipIT/TT'ye sessizce dusmemeli."""
    monkeypatch.setattr(providers.config, "fedex_configured", lambda: True)
    monkeypatch.setattr(providers.config, "shipit_configured", lambda: True)
    assert providers.pod_provider_for(providers.CHANNEL_FEDEX) is None


def test_fedex_has_no_label_provider():
    """Bu sistemden FedEx etiketi basilmaz (kullanici karari) — ShipIT/NL'e dusmemeli."""
    assert providers.label_provider_for(providers.CHANNEL_FEDEX) is None


# ======================================================================
# Tarayici
# ======================================================================
@pytest.fixture
def tracker(tmp_path, monkeypatch):
    db = ShipmentsDB(path=tmp_path / "nl.db")
    monkeypatch.setattr(providers, "_nl_tt",
                        GLSNLTrackClient(base_url=f"{BASE}/nltt",
                                         username="mock", password="mock"))
    return Tracker(db=db)


def test_tick_updates_nl_parcel_from_live_endpoint(tracker):
    tracker.db.add_parcel(tracking_no=KNOWN, channel="NL", reference="T-1",
                          country="NL", consignee_name="Test", status="created")
    tracker.tick()

    parcel = tracker.db.get(KNOWN)
    assert parcel["status"] == "delivered"
    assert parcel["last_event_text"]
    assert parcel["delivered_date"]
    assert tracker.last_error is None


def test_tick_skips_parcels_gls_does_not_know(tracker):
    """Bilinmeyen numara statuyu bozmamali ve hata sayilmamali."""
    tracker.db.add_parcel(tracking_no=UNKNOWN, channel="NL", reference="T-2",
                          country="NL", consignee_name="Test", status="in_transit")
    tracker.tick()

    assert tracker.db.get(UNKNOWN)["status"] == "in_transit"
    assert tracker.last_error is None


def test_tick_queries_nl_in_batches(tracker, monkeypatch):
    """40 NL parcasi 40 istek degil 2 istek etmeli."""
    for i in range(40):
        tracker.db.add_parcel(tracking_no=f"3500000140{i:04d}", channel="NL",
                              reference=f"B-{i}", country="NL",
                              consignee_name="Test", status="created")

    calls = []
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: calls.append(body["parcelNumbers"]) or {"parcels": []})
    tracker.tick()

    assert len(calls) == 2


def test_tick_reports_batch_error_once(tracker, monkeypatch):
    """Grup halinde giden istek basarisiz olursa 20 satirlik hata yigini olusmamali."""
    for i in range(5):
        tracker.db.add_parcel(tracking_no=f"3500000141{i:04d}", channel="NL",
                              reference=f"E-{i}", country="NL",
                              consignee_name="Test", status="created")

    def boom(path, body):
        raise GLSNLError("sunucu hatasi", status=500)

    monkeypatch.setattr(providers._nl_tt, "_post", boom)
    tracker.tick()

    assert tracker.last_error is not None
    assert tracker.last_error.count(";") == 0   # tek hata kaydi


def test_tick_respects_max_per_tick(tracker, monkeypatch):
    """Panel MSSQL'den dolunca 4000+ aktif parca olur; GLS hiz siniri asilmamali."""
    for i in range(50):
        tracker.db.add_parcel(tracking_no=f"3500000142{i:04d}", channel="NL",
                              reference=f"L-{i}", country="NL",
                              consignee_name="Test", status="created")
    tracker.max_per_tick = 20

    calls = []
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: calls.append(body["parcelNumbers"]) or {"parcels": []})
    tracker.tick()

    assert len(calls) == 1                      # 50 degil 20 parca -> tek grup
    assert sum(len(c) for c in calls) == 20


def test_tick_rotates_through_parcels(tracker, monkeypatch):
    """Cevap vermeyen parca isaretlenmezse siranin basini tikar ve digerlerini ac birakir."""
    for i in range(4):
        tracker.db.add_parcel(tracking_no=f"3500000143{i:04d}", channel="NL",
                              reference=f"R-{i}", country="NL",
                              consignee_name="Test", status="created")
    tracker.max_per_tick = 2

    asked = []
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: asked.extend(body["parcelNumbers"]) or {"parcels": []})
    tracker.tick()
    tracker.tick()

    assert len(set(asked)) == 4                 # ikinci turda digerlerine gecildi


# ======================================================================
# Toplu uc ile teslimat detayi ucunun senkron olmamasi
# ======================================================================
# Canli olcum (2026-08-27): "dagitimda" gorunen 18 koliden 14'u ayri
# `delivery_details` ucuna gore ZATEN teslim edilmisti — biri
# (38120177007466) 2 GUN once, kullanicinin GLS'in kendi musteri sitesinde
# gordugu "duval" imzasiyla dogruladigi ayni olay. Toplu uc (`parcel_details`,
# tum turumuzun dayandigi kaynak) bu olayi cok daha GEC yaziyor.

def test_gls_confirmed_delivery_wins_over_the_stale_batch_feed(tracker, monkeypatch):
    """`delivery_details` teslim diyorsa, toplu ucun eski "OutForDelivery"
    yaniti ezilir — canli ornek 38120177007466 (kullanici ekran goruntusuyle
    dogruladi: "duval", 25 Agu 10:04, GLS'in kendi sitesinde)."""
    tracker.db.add_parcel(tracking_no=KNOWN, channel="NL", reference="T-1",
                          country="NL", consignee_name="Test", status="in_transit")
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: {"parcels": [
                            {"parcelNo": KNOWN, "state": "OutForDelivery", "events": []}]})
    monkeypatch.setattr(providers._nl_tt, "delivery_details",
                        lambda number, key=None: {"deliveryScanInfo": {
                            "isDelivered": True, "dateTime": "2026-08-25T10:04:57",
                            "signedBy": "duval"}})

    tracker.tick()

    parcel = tracker.db.get(KNOWN)
    assert parcel["status"] == "delivered"
    assert parcel["delivered_date"] == "2026-08-25T08:04:57Z"   # yerelden UTC'ye cevrildi
    assert "duval" in parcel["last_event_text"]


def test_gls_confirmed_delivery_still_captures_the_handover_date(tracker, monkeypatch):
    """`_live_delivery_scan` yolundan gelen teslimat `handed_over_at`i KAYBETMEMELI.

    Canli olcum (2026-08-28): tam bu yoldan "delivered"e gecen 173 kolide
    `UD_OkutmaTarihi` hep bos kalmisti — statu terminal oldugu icin bir daha
    hic sorulmuyor, bosluk KALICI oluyordu. Elle duzeltildi; bu test bir
    daha birikmesin diye.
    """
    tracker.db.add_parcel(tracking_no=KNOWN, channel="NL", reference="T-1",
                          country="NL", consignee_name="Test", status="in_transit")
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: {"parcels": [
                            {"parcelNo": KNOWN, "state": "OutForDelivery", "events": [
                                {"descriptionEN": HANDED, "date": "2026-08-20T09:00:00Z"},
                                {"descriptionEN": "The parcel has reached the parcel center.",
                                 "date": "2026-08-25T04:00:00Z"},
                            ]}]})
    monkeypatch.setattr(providers._nl_tt, "delivery_details",
                        lambda number, key=None: {"deliveryScanInfo": {
                            "isDelivered": True, "dateTime": "2026-08-25T10:04:57",
                            "signedBy": "duval"}})

    tracker.tick()

    assert tracker.db.get(KNOWN)["handed_over_at"] == "2026-08-20T09:00:00Z"


def test_out_for_delivery_stays_out_for_delivery_until_gls_actually_scans_it(tracker, monkeypatch):
    """`delivery_details` henuz teslim demiyorsa toplu ucun statusu gecerlidir."""
    tracker.db.add_parcel(tracking_no=KNOWN, channel="NL", reference="T-1",
                          country="NL", consignee_name="Test", status="in_transit")
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: {"parcels": [
                            {"parcelNo": KNOWN, "state": "OutForDelivery", "events": []}]})
    monkeypatch.setattr(providers._nl_tt, "delivery_details",
                        lambda number, key=None: {"deliveryScanInfo": {"isDelivered": False}})

    tracker.tick()

    assert tracker.db.get(KNOWN)["status"] == "out_for_delivery"


def test_delivery_details_reuses_the_batch_response_instead_of_a_second_lookup(tracker, monkeypatch):
    """`uniqueNo`/`jobDate` toplu yanitta zaten var — ikinci bir `parcel_detail`
    istegi (varsayilan `_pod_key` yolu) ATLANMALI.

    Canli olcum (2026-08-28): kontrol "in_transit"e genisleyince, bu atlama
    olmadan parca basina 2 istek atiliyor, tam bir tur 15 dakikayi asiyordu.
    """
    tracker.db.add_parcel(tracking_no=KNOWN, channel="NL", reference="T-1",
                          country="NL", consignee_name="Test", status="in_transit")
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: {"parcels": [
                            {"parcelNo": KNOWN, "state": "OutForDelivery",
                             "uniqueNo": "ZXVQWQGM", "jobDate": "2026-08-13T22:00:00Z",
                             "events": []}]})

    seen_keys = []
    monkeypatch.setattr(providers._nl_tt, "delivery_details",
                        lambda number, key=None: seen_keys.append(key) or {"deliveryScanInfo": {"isDelivered": False}})
    tracker.tick()

    assert seen_keys == [{"uniqueNo": "ZXVQWQGM", "jobDate": "2026-08-14T00:00:00"}]


def test_a_broken_delivery_details_call_does_not_break_the_tick(tracker, monkeypatch):
    """Teslimat-detayi sorgusu patlarsa tur DUSMEZ, toplu ucun sonucu kalir."""
    tracker.db.add_parcel(tracking_no=KNOWN, channel="NL", reference="T-1",
                          country="NL", consignee_name="Test", status="in_transit")
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: {"parcels": [
                            {"parcelNo": KNOWN, "state": "OutForDelivery", "events": []}]})

    def boom(number, key=None):
        raise GLSNLError("sunucu hatasi", status=500)
    monkeypatch.setattr(providers._nl_tt, "delivery_details", boom)

    tracker.tick()

    assert tracker.db.get(KNOWN)["status"] == "out_for_delivery"
    assert tracker.last_error is None


def test_an_in_transit_parcel_is_also_checked_against_delivery_details(tracker, monkeypatch):
    """`out_for_delivery`ye HIC ugramadan dogrudan teslim gelebilir.

    Canli olcum (2026-08-28, 4 farkli ulke/depo): toplu ucun kendisi 18-20
    saat geriden akiyordu — "dagitimda" adimi hic ARA gorunmeden, teslimat
    olayi gecmisiyle birlikte tek seferde geldi. Kullanici gercek zamanli
    istedigi icin ("sabahin korunde mesaj yagiyor") kapsam in_transit'e de
    genisletildi.
    """
    tracker.db.add_parcel(tracking_no=KNOWN, channel="NL", reference="T-1",
                          country="NL", consignee_name="Test", status="created")
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: {"parcels": [
                            {"parcelNo": KNOWN, "state": "InTransit", "events": []}]})
    monkeypatch.setattr(providers._nl_tt, "delivery_details",
                        lambda number, key=None: {"deliveryScanInfo": {
                            "isDelivered": True, "dateTime": "2026-08-27T10:04:57",
                            "signedBy": "test"}})

    tracker.tick()

    assert tracker.db.get(KNOWN)["status"] == "delivered"


def test_a_stuck_exception_is_checked_against_delivery_details(tracker, monkeypatch):
    """Kullanici 2026-09-04: sinir otesi (FR/ES/BE) koliler toplu ucun olay
    akisinda "depoda bekliyor" (exception) kalirken `deliveryScanInfo`
    teslim edildigini soyluyor ("gls api de teslim edildi diyor, siteye
    yansimiyor"). Exception koliler de canli teslim taramasindan gecmeli."""
    tracker.db.add_parcel(tracking_no=KNOWN, channel="NL", reference="T-1",
                          country="FR", consignee_name="Test", status="exception")
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: {"parcels": [{
                            "parcelNo": KNOWN, "state": "NotDelivered", "events": [
                                {"date": "2026-08-26T09:00:00Z",
                                 "descriptionEN": "The parcel is stored in the parcel center."}]}]})
    monkeypatch.setattr(providers._nl_tt, "delivery_details",
                        lambda number, key=None: {"deliveryScanInfo": {
                            "isDelivered": True, "dateTime": "2026-08-27T12:57:09",
                            "signedBy": "ROSSI"}})

    tracker.tick()

    row = tracker.db.get(KNOWN)
    assert row["status"] == "delivered"
    assert not (row.get("issue_text") or "")          # exception aciklamasi temizlendi


def test_a_not_yet_handed_over_parcel_is_not_checked_against_delivery_details(tracker, monkeypatch):
    """Maliyet BOSUNA buyutulmez: GLS'e daha teslim edilmemis koli sorgulanmaz."""
    tracker.db.add_parcel(tracking_no=KNOWN, channel="NL", reference="T-1",
                          country="NL", consignee_name="Test", status="created")
    monkeypatch.setattr(providers._nl_tt, "_post",
                        lambda path, body: {"parcels": [
                            {"parcelNo": KNOWN, "state": "Announced", "events": []}]})

    calls = []
    monkeypatch.setattr(providers._nl_tt, "delivery_details",
                        lambda number, key=None: calls.append(number) or {})
    tracker.tick()

    assert calls == []


# ======================================================================
# ParcelShop (GLS teslim noktasi) — kullanici bulgusu, 2026-08-31:
# "sorun gozuken ama aslinda sorun olmayanlar var ... parcelshopa teslim
# edilenler delivered gozukmeli exception degil ve otees iadeye donmus"
# Metinlerin hepsi CANLI GLS cevaplarindan alinmistir.
# ======================================================================

PS_DELIVERED = "The parcel has been delivered at the ParcelShop (see ParcelShop information)."
PS_COLLECTED = "Handing over the parcel to the recipient at the GLS ParcelShop."
PS_TIMEOUT = "The parcel has reached the maximum storage time in the ParcelShop."
PS_STORED = "The parcel is stored in the parcel center."


def test_a_parcel_shop_delivery_stays_delivered_after_later_storage_events():
    """Canli ornek 38120177007589: ParcelShop'a teslim edildikten SONRA GLS
    gunlerce "stored in the parcel center" yaziyor. Eskiden yalnizca SON
    olaya bakildigi icin koli "exception" gorunuyordu."""
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": HANDED, "date": "2026-08-14T17:47:00Z"},
        {"descriptionEN": PS_DELIVERED, "date": "2026-08-19T16:39:00Z"},
        {"descriptionEN": PS_STORED, "date": "2026-08-21T09:42:00Z"},
    ]))

    assert info["status"] == "delivered"
    assert info["delivered_date"] == "2026-08-19T16:39:00Z"
    assert info["issue_text"] == ""


def test_the_parcel_shop_storage_timeout_is_a_return_not_an_exception():
    """Canli ornek 38120177007589 (BGRES1N): musteri koliyi ALMADI, saklama
    suresi doldu — koli fiilen GONDERICIYE donuyor. GLS bunun icin ayrica
    "returned to sender" YAZMIYOR, bu yuzden panel "exception" diyordu."""
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": HANDED, "date": "2026-08-14T17:47:00Z"},
        {"descriptionEN": PS_DELIVERED, "date": "2026-08-19T16:39:00Z"},
        {"descriptionEN": PS_STORED, "date": "2026-08-21T09:42:00Z"},
        {"descriptionEN": PS_TIMEOUT, "date": "2026-08-26T22:13:00Z"},
    ]))

    assert info["status"] == "returned"


def test_a_collected_parcel_shop_delivery_is_delivered():
    """Canli ornek 38120177012859 — bu metin "has been delivered" ICERMEZ,
    ayrica taninmasi gerekir (bkz. DELIVERY_PHRASES)."""
    info = map_nl_tt_parcel(_parcel("Delivered", [
        {"descriptionEN": HANDED, "date": "2026-08-14T17:47:00Z"},
        {"descriptionEN": PS_COLLECTED, "date": "2026-08-20T10:00:00Z"},
    ]))

    assert info["status"] == "delivered"
    assert info["delivered_date"] == "2026-08-20T10:00:00Z"


def test_a_redirect_to_a_gls_point_is_not_a_problem():
    """Canli ornek 38120177022452/022445/022469: "Shipment locked/Delivery to
    a GLS Point" metni `BLOCKING_PHRASES`teki "shipment locked"a takiliyor ve
    koliler panelde "Sorunlu" gorunuyordu — oysa GLS'e gore daha yolun
    basindalar (`state=Announced`) ve bu yalnizca ParcelShop yonlendirmesi."""
    info = map_nl_tt_parcel(_parcel("Announced", [
        {"descriptionEN": "Consignee contacted/preadvice data", "date": "2026-08-19T06:46:00Z"},
        {"descriptionEN": "FDS Information - Delivery to a GLS ParcelShop",
         "date": "2026-08-19T10:31:00Z"},
        {"descriptionEN": "Shipment locked/Delivery to a GLS Point",
         "date": "2026-08-19T10:31:00Z"},
        {"descriptionEN": "Change completed for Delivery address", "date": "2026-08-19T10:41:00Z"},
    ]))

    assert info["status"] != "exception"
    assert info["issue_text"] == ""


def test_a_real_shipment_lock_is_still_a_problem():
    """Allowlist YALNIZCA GLS Point yonlendirmesini kapsar — baska bir
    sebeple kilitlenen gonderi hala sorunludur."""
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": HANDED, "date": "2026-08-14T17:47:00Z"},
        {"descriptionEN": "Shipment locked/Customs inspection", "date": "2026-08-19T10:31:00Z"},
    ]))

    assert info["status"] == "exception"
    assert "Shipment locked" in info["issue_text"]


def test_a_parcel_waiting_at_a_parcel_shop_keeps_being_scanned(tmp_path):
    """`active_parcels` 'delivered' olani BIR DAHA SORMAZ; ParcelShop'ta
    bekleyen koli ise sonuclanmamistir (alinabilir ya da iadeye donebilir).
    Bu kuyruk olmadan BGRES1N'in iadesi panele hic yansimazdi."""
    db = ShipmentsDB(path=tmp_path / "ps.db")
    db.add_parcel(tracking_no="38120177000001", channel="NL", consignee_name="c",
                  country="ES", status="out_for_delivery")
    db.update_status("38120177000001", "delivered", note=PS_DELIVERED)

    assert "38120177000001" not in [p["tracking_no"] for p in db.active_parcels()]
    assert [p["tracking_no"] for p in db.parcel_shop_pending()] == ["38120177000001"]


def test_a_collected_parcel_shop_parcel_is_no_longer_scanned(tmp_path):
    """Musteri koliyi aldiysa is bitmistir — kuyrugu sonsuza kadar sismemeli."""
    db = ShipmentsDB(path=tmp_path / "ps2.db")
    db.add_parcel(tracking_no="38120177000002", channel="NL", consignee_name="c",
                  country="ES", status="out_for_delivery")
    db.update_status("38120177000002", "delivered", note=PS_DELIVERED)
    db.update_status("38120177000002", "in_transit", note=PS_COLLECTED)
    db.update_status("38120177000002", "delivered", note=PS_COLLECTED)

    assert db.parcel_shop_pending() == []


def test_an_ordinary_delivery_is_not_in_the_parcel_shop_queue(tmp_path):
    """Kapiya teslim edilen normal koli bu kuyruga HIC girmemeli."""
    db = ShipmentsDB(path=tmp_path / "ps3.db")
    db.add_parcel(tracking_no="38120177000003", channel="NL", consignee_name="c",
                  country="FR", status="out_for_delivery")
    db.update_status("38120177000003", "delivered", note="The parcel has been delivered.")

    assert db.parcel_shop_pending() == []


def test_the_parcel_shop_queue_stops_watching_after_the_storage_window(tmp_path):
    """Kuyruk SONSUZA KADAR buyumemeli: GLS'in saklama suresi gecmisse koli
    mutlaka sonuclanmistir (bkz. PARCEL_SHOP_WATCH_DAYS)."""
    db = ShipmentsDB(path=tmp_path / "ps4.db")
    db.add_parcel(tracking_no="38120177000004", channel="NL", consignee_name="c",
                  country="ES", status="out_for_delivery")
    db.update_status("38120177000004", "delivered", note=PS_DELIVERED)

    db.update_tracking_info("38120177000004", delivered_date="2026-01-05T10:00:00Z")
    assert db.parcel_shop_pending() == []          # cok eski -> artik izlenmez

    db.conn.execute("UPDATE parcels SET delivered_date = datetime('now', '-2 days') "
                    "WHERE tracking_no = ?", ("38120177000004",))
    db.conn.commit()
    assert [p["tracking_no"] for p in db.parcel_shop_pending()] == ["38120177000004"]


def test_a_planned_retry_is_not_a_problem():
    """Canli ornek 38120177020458/020465: GLS "bugun olmadi, ERTESI IS GUNU
    tekrar denenecek" diyor. Metin "Not delivered" ile basladigi icin koliler
    panelde "Sorunlu" gorunuyordu — ustelik sorun METNI BOS'tu, cunku
    tanimlanabilir bir sorun olayi yoktu (kullanici bildirdi, 2026-08-31)."""
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": HANDED, "date": "2026-08-27T13:40:00Z"},
        {"descriptionEN": "The parcel is expected to be delivered during the day.",
         "date": "2026-08-28T06:09:00Z"},
        {"descriptionEN": "Not delivered - delivery planned next workingday",
         "date": "2026-08-28T17:46:00Z"},
    ]))

    assert info["status"] != "exception"
    assert info["issue_text"] == ""


def test_an_exception_always_has_an_identifiable_cause():
    """GLS'in `NotDelivered` state'i tek basina "sorunlu" demek DEGILDIR.

    Eskiden `NL_TT_STATES` onu koru korune "exception"a esliyordu ve panelde
    sorun metni BOS "Sorunlu" kayitlar olusuyordu. Artik tanimlanabilir bir
    sorun olayi yoksa statu olay metninden turetilir.
    """
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": HANDED, "date": "2026-08-27T13:40:00Z"},
        {"descriptionEN": "The parcel has reached the parcel center.",
         "date": "2026-08-28T06:09:00Z"},
    ]))

    assert not (info["status"] == "exception" and not info["issue_text"])


def test_a_real_failure_is_still_an_exception():
    """Guvenlik agi: GERCEK bir basarisizlik hala sorunlu olmali."""
    info = map_nl_tt_parcel(_parcel("NotDelivered", [
        {"descriptionEN": HANDED, "date": "2026-08-20T09:00:00Z"},
        {"descriptionEN": "The parcel could not be delivered as the consignee was absent.",
         "date": "2026-08-27T12:41:00Z"},
    ]))

    assert info["status"] == "exception"
    assert info["issue_text"]


def test_a_freshly_labelled_parcel_has_no_handover_date():
    """`handed_over_at` GLS koliyi FIILEN alinca dolmali — etiket basilinca
    degil. Bu alan teslimat suresi raporunu, "takilmis kargo" esigini ve
    ERP'ye yazilan `UD_OkutmaTarihi`yi besliyor; canli olcum (2026-09-01):
    4 Eylul'de cikacak 72 kolinin hepsinde 1 Eylul yaziyordu."""
    info = map_nl_tt_parcel(_parcel("Announced", [
        {"descriptionEN": "The parcel data was entered into the GLS IT system; "
                          "the parcel was not yet handed over to GLS.",
         "date": "2026-09-01T07:00:00Z"},
        {"descriptionEN": "The parcel was provided by the sender for collection by GLS.",
         "date": "2026-09-01T07:07:41Z"},
    ]))

    assert info["status"] == "created"
    assert info["handed_over_at"] == ""


def test_the_handover_date_appears_once_gls_actually_takes_it():
    info = map_nl_tt_parcel(_parcel("InTransit", [
        {"descriptionEN": "The parcel was provided by the sender for collection by GLS.",
         "date": "2026-09-01T07:07:41Z"},
        {"descriptionEN": HANDED, "date": "2026-09-04T09:15:00Z"},
    ]))

    assert info["status"] == "in_transit"
    assert info["handed_over_at"] == "2026-09-04T09:15:00Z"


def test_gls_state_received_does_not_mean_the_parcel_moved():
    """GLS `state=Received` bu asamada VERININ alindigini soyler, kolinin
    degil — `NL_TT_STATES` onu dogrudan `in_transit`e esliyordu.

    Canli olcum (2026-09-01): o gun basilan etiketlerde state `Received` idi
    ama olaylarin hepsi devralma oncesiydi; panel "Yolda" gosteriyordu."""
    info = map_nl_tt_parcel(_parcel("Received", [
        {"descriptionEN": "The parcel data was entered into the GLS IT system; "
                          "the parcel was not yet handed over to GLS.",
         "date": "2026-09-01T07:07:41Z"},
        {"descriptionEN": "The parcel was provided by the sender for collection by GLS.",
         "date": "2026-09-01T07:07:41Z"},
    ]))

    assert info["status"] == "created"
    assert info["handed_over_at"] == ""


def test_a_real_movement_event_restores_in_transit():
    """Guvenlik agi: gercek bir hareket olayi varsa statu geri alinmamali."""
    info = map_nl_tt_parcel(_parcel("Received", [
        {"descriptionEN": "The parcel was provided by the sender for collection by GLS.",
         "date": "2026-09-01T07:07:41Z"},
        {"descriptionEN": HANDED, "date": "2026-09-04T09:00:00Z"},
    ]))

    assert info["status"] == "in_transit"
    assert info["handed_over_at"] == "2026-09-04T09:00:00Z"


def test_delivery_is_never_downgraded_by_the_handover_rule():
    """Kural YALNIZCA yolda/dagitimda statusunu geri alir."""
    info = map_nl_tt_parcel(_parcel("Delivered", [
        {"descriptionEN": "The parcel has been delivered.", "date": "2026-09-05T10:00:00Z"},
    ]))
    assert info["status"] == "delivered"


def test_live_delivery_scan_is_capped_per_tick(tmp_path, monkeypatch):
    """Canli olcum (2026-09-03): 292 'in_transit' NL parcasi her turda ayri
    HTTP istegiyle sorgulaninca tur 4 dakika surdu ve site yavasladi.
    `LIVE_SCAN_MAX_PER_TICK` bunu sinirlar; kalanlar toplu ucun bilgisiyle
    gecer ve bir sonraki tura kalir."""
    from tracking import scheduler as sch

    monkeypatch.setattr(sch, "LIVE_SCAN_MAX_PER_TICK", 5)
    calls = []
    monkeypatch.setattr(sch, "_live_delivery_scan",
                        lambda client, tn, raw=None: calls.append(tn) or None)

    class FakeClient:
        def parcel_details(self, nums):
            return {n: {"parcelNo": n, "state": "InTransit", "events": [
                {"date": "2026-08-20T09:00:00Z",
                 "descriptionEN": "reached the parcel center"}
            ]} for n in nums}

    monkeypatch.setattr(sch.providers, "tracking_provider_for",
                        lambda ch: ("nl_tt", FakeClient()))

    db = ShipmentsDB(path=tmp_path / "livecap.db")
    parcels = []
    for i in range(30):
        tn = f"381201770{i:05d}"
        db.add_parcel(tracking_no=tn, channel="NL", reference="r",
                      consignee_name="c", country="DE")
        parcels.append({"tracking_no": tn, "channel": "NL"})

    list(Tracker(db=db)._nl_results(parcels, []))
    assert len(calls) == 5          # tavan asilmadi, kalan 25 toplu bilgiyle gecti
