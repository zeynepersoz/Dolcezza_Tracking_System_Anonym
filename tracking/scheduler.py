"""Arka plan takip taramasi.

APScheduler ile belirli aralikla aktif kargolari GLS API'lerinden sorgular
ve statulerini gunceller. Statu degistiginde SSE dinleyicilerine bildirir.

Hangi API'nin kullanilacagini gls_api/providers.py secer:
    ShipIT REST  -> /rs/tracking/parceldetails  (yanit koku: UnitDetail)
    T&T SOAP     -> GetTuDetail
    NL T&T REST  -> /api/parcel/v1/details      (tek istekte 20 parca)

NL kanali TOPLU sorgulanir: 4000 parca icin 4000 istek yerine 200 istek.

Statu taramasindan SONRA, teslim edilmis ama POD'u arsivlenmemis parcalarin
POD'u da cekilir (`pod_fetcher`). POD takip ucundan gelmez, ayri bir hosta
(apm.gls.nl) parca basina ayri istek gerektirir — bu yuzden tur basina cok daha
dusuk bir tavanla, `db.parcels_without_pod()` kuyrugundan islenir. Fonksiyon
disaridan enjekte edilir: `tracking/` katmani `web/`'i import etmez.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import timez
from gls_api import config, providers
from gls_api.shipit_client import ShipITError
from gls_api.nl_client import GLSNLError
from gls_api.nl_track_client import MAX_PARCELS_PER_CALL
from gls_api.tt_soap_client import TrackTraceError
from gls_api.tt_v1_client import GLSGroupError, MAX_TRACKIDS_PER_CALL
from erp import writeback
from tracking.db import ShipmentsDB, delivered_note, classify_event_text, RETURN_TEXT_PHRASES

log = logging.getLogger("tracker")

API_ERRORS = (ShipITError, GLSNLError, TrackTraceError, GLSGroupError)


# ---------- statu esleyicileri ----------

def status_from_event(text: str, delivered: bool = False) -> str:
    """Takip olayi metnini bizim statu evrenimize esler.

    Metin siniflandirmasinin TEK yeri `tracking.db.classify_event_text`dir —
    burada ayrica tutulan bir kelime listesi, statu gecmisi satirlariyla
    (ayni metni db.sync_events de siniflandirir) sessizce ayrisirdi (canli
    ornek: "stored in the parcel center" gecmiste "Exception" gosterirken sag
    paneldeki genel rozette "Out for Delivery" kalmisti).
    """
    if delivered:
        return "delivered"
    return classify_event_text(text)


def map_shipit_details(details: dict) -> dict:
    """ShipIT parceldetails yanitini ortak sozluge cevirir.

    Yanit koku `UnitDetail`'dir. Olay alan adlari kurulumdan kuruluma
    degisebildigi icin hem `EvtDscr` hem `Description` kabul edilir. Konum
    aciklamaya EKLENMEZ (bkz. `_event_text`).
    """
    unit = details.get("UnitDetail") or {}
    history = unit.get("History") or []
    delivered_date = unit.get("DeliveryDate") or unit.get("DeliveryDateTime") or ""

    event_at, note = "", ""
    if history:
        last = history[-1] or {}
        note = last.get("EvtDscr") or last.get("Description") or ""
        date, time = last.get("Date") or "", last.get("Time") or ""
        event_at = f"{date} {time}".strip()

    return {
        "status": status_from_event(note, delivered=bool(delivered_date)),
        "note": note,
        "event_at": event_at,
        "delivered_date": delivered_date,
    }


def map_tt_detail(detail: dict) -> dict:
    """T&T SOAP get_tu_detail cikitisini ayni ortak sozluge cevirir."""
    history = detail.get("history") or []
    delivered_at = detail.get("delivered_at") or ""

    event_at, note = "", ""
    if history:
        last = history[-1]
        note = last.get("description") or ""
        event_at = last.get("date") or ""

    signature = detail.get("signature") or ""
    if delivered_at and signature:
        note = f"Teslim edildi, imzalayan: {signature}"

    return {
        "status": status_from_event(note, delivered=bool(delivered_at)),
        "note": note,
        "event_at": event_at,
        "delivered_date": delivered_at,
    }


def map_fedex_result(result: dict) -> dict:
    """FedEx Track API `trackResults[i]` cikitisini ayni ortak sozluge cevirir.

    Statu icin ozel bir FedEx-kod sozlugu KURULMADI: `latestStatusDetail.
    description` zaten Ingilizce insan-okur metin ("Delivered", "Picked up",
    "Ready for pickup" ...) — ayni `status_from_event` (GLS icin yazilan
    anahtar-kelime siniflandirici) burada da calisiyor. Bilinmeyen bir ifade
    en kotu ihtimalle "in_transit"e duser, yanlis "delivered"/"exception"
    UYDURULMAZ.

    `scanEvents` FedEx'ten hangi sirada geliyor SEMADA belirtilmiyor; en son
    olay tarihe gore (metin karsilastirmasi degil) bulunur.
    """
    scan_events = result.get("scanEvents") or []
    last_event = max(scan_events, key=lambda e: e.get("date") or "", default={})
    note = last_event.get("eventDescription") or last_event.get("derivedStatus") or ""
    event_at = last_event.get("date") or ""

    if not note:
        # Olay gecmisi bossa (ornek: henuz taranmamis) en azindan genel
        # durumu goster — bomboş "note" kullanicinin ekraninda hicbir sey
        # anlatmaz.
        latest = result.get("latestStatusDetail") or {}
        note = latest.get("description") or latest.get("statusByLocale") or ""

    delivered_at = next(
        (d.get("dateTime") for d in (result.get("dateAndTimes") or [])
         if d.get("type") == "ACTUAL_DELIVERY"),
        "",
    )

    # FedEx yanitinda HIC link/url alani yok (dogrulandi, 2026-08-31: gercek
    # bir yanit derinlemesine tarandi) — kullanicinin FedEx'in kendi genel
    # takip sayfasi istegiyle burada KURULUYOR. `tracking_no` guvenilir kaynak
    # `trackingNumberInfo`dir — dis parametre olarak GECIRILMEZ, cunku bu
    # fonksiyon zaten tek argumanla (ham API yaniti) cagriliyor her yerde.
    tn = (result.get("trackingNumberInfo") or {}).get("trackingNumber") or ""
    tracking_url = f"https://www.fedex.com/fedextrack/?trknbr={tn}" if tn else ""

    return {
        "status": status_from_event(note, delivered=bool(delivered_at)),
        "note": note,
        "event_at": event_at,
        "delivered_date": delivered_at,
        "tracking_url": tracking_url,
        "raw_events": [
            {
                "date": e.get("date"),
                "description": e.get("eventDescription") or e.get("derivedStatus"),
                "depotName": ((e.get("scanLocation") or {}).get("city") or ""),
            }
            for e in scan_events if e.get("date")
        ],
    }


# GLS NL takip ucundaki `state` -> bizim statu evrenimiz. Liste eksiksiz
# degildir; bilinmeyen bir deger gelirse olay metnine duseriz (asagi bak).
NL_TT_STATES = {
    "delivered": "delivered",
    "inregion": "out_for_delivery",
    "indelivery": "out_for_delivery",
    "outfordelivery": "out_for_delivery",
    "announced": "created",
    "intransit": "in_transit",
    "received": "in_transit",
    "returned": "returned",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "notdelivered": "exception",
}

# "The parcel data have been deleted from the GLS IT system" IPTAL DEGILDIR.
# Bir sure once bu metin gorulunce statu `cancelled` yaziliyordu; `cancelled`
# takip kuyrugundan KALICI olarak dusuruldugu icin o parcalar bir daha hic
# sorulmadi. Canli olcum (2026-08-17): boyle isaretlenmis 14 parcanin 14'u de
# GLS'te YASIYORDU (7 dagitimda, 5 depoda, 1 teslim, 1 bolgede) ve hepsinin
# silme olayindan SONRA 3-13 olayi vardi. Ornek 38120177007435:
#   21 Tem duyuruldu -> 5 Agu "veriler silindi" -> 14 Agu GLS'e teslim edildi
#   (Amsterdam, isPhysical) -> 17 Agu alicida.
# GLS eski bir on-duyuruyu temizliyor, gonderiyi iptal etmiyor; ayni numara
# sonradan gercekten yola cikabiliyor. Bu yuzden statuyu GLS'in kendi `state`i
# belirler — bizim yerel kaydimiz GLS ne diyorsa onu soylemeli.

# GLS koliyi FIZIKSEL olarak teslim almadan ONCE de olay yazar. Bunlar hareket
# degildir: veri girisi, aliciyla iletisim (preadvice), teslimat bildirim
# e-postasi. Ozellikle preadvice sorun DEGILDIR — dagiticinin aliciyla teslimat
# icin anlastigi anlamina gelir (kullanici dogruladi). Canli olcum (2026-08,
# 518 parca): 163 parcada preadvice, 109'unda e-posta bildirimi var ve buyuk
# cogunlugu hala GLS'e verilmemis.
#
# "data have been deleted" de buraya girer: GLS'in kendi kayit temizligidir,
# koliye dokunulmamistir. Disarida kalirsa yeniden canlanan bir parcada
# "GLS'e teslim" ani silme gunu gorunurdu (canli 38120177007435: 5 Agu silme,
# 14 Agu gercek teslim).
PRE_HANDOVER_EVENTS = (
    "was entered into the gls",
    "consignee contacted",
    "preadvice",
    "email with delivery advice",
    "data have been deleted",
    # "Gonderici koliyi GLS'in almasi icin hazirladi" — koli HALA BIZDE.
    # Bunu teslim alma sayinca `handed_over_at` etiketin basildigi gune
    # yaziliyordu; canli olcum (2026-09-01): 4 Eylul'de cikacak 72 kolinin
    # hepsinde alan 1 Eylul gorunuyordu. Bu alan teslimat suresi raporunu,
    # "takilmis kargo" esigini ve ERP'ye yazilan `UD_OkutmaTarihi`yi besler.
    "provided by the sender for collection",
)

# Teslimati DURDURAN olaylar — koli yolda kalmistir, statu "exception" olur.
# Canli olay sozlugunden (518 parcanin tam gecmisi) turetildi; "Relabelled",
# "Check scan", "Change completed for" ve "reached/left the parcel center" gibi
# rutin metinler BILEREK disarida.
BLOCKING_PHRASES = (
    "could not be delivered",
    "cannot be delivered",
    "refused",
    "returned to sender",
    "traffic problems",
    "not to locate",
    "misrouted",
    "shipment locked",
    "request for more information",
    "customs",
    "not out for delivery",
    "stored in the parcel center",
    "stored - delivery planned",
)

# "Depoda bekliyor" (stored) VE alicinin/adresin ERISIM sorunlari
# (tatil/kapali resepsiyon/o gun evde yok) RUTIN/GECICIDIR — GLS zaten
# kendiliginden tekrar dener, cogu zaman sadece normal isleme suresidir.
# Canli olcum (2026-08-27): 38120177009255 ve 014419, "cannot be delivered
# as consignee is on holidays" / "reception is closed" / "Not out for
# delivery/Consignee absent" olaylarindan GUNLER SONRA (014419'da 16 gun)
# hala "exception" gorunuyordu — GLS state'i o zamandan beri defalarca
# InRegion'a donmus, yeni hicbir sorun olayi gelmemisti.
# Digerleri (refused / returned to sender / misrouted / not to locate /
# customs / shipment locked / traffic problems / request for more
# information) MUSTERI/nakliye kaynakli SERT bir basarisizliktir ve GLS
# `state`i "dagitimda" dese bile guvenilmemeli (canli ornek 38120177004885
# — "recipient refused acceptance" sonrasi state "InRegion" kalmisti ama
# koli sonunda iade oldu; rutin depo bekletmesiyle musteri reddini AYNI
# kefeye koymak yanlis olurdu). Bkz. map_nl_tt_parcel.
_SOFT_BLOCKING_PHRASES = (
    "stored in the parcel center",
    "stored - delivery planned",
    "could not be delivered",
    "cannot be delivered",
    "not out for delivery",
)
HARD_BLOCKING_PHRASES = tuple(
    p for p in BLOCKING_PHRASES
    if p not in _SOFT_BLOCKING_PHRASES
)

# Iade ifadeleri `tracking.db.RETURN_TEXT_PHRASES`de: koli gondericiye geri
# donuyor, `exception`in alt turu DEGIL. Ayrica bkz. `NL_TT_STATES["returned"]`.

# Hasar teslimati DURDURMAZ: GLS koliyi onarip yola devam ediyor (canli ornek
# 38120177012132 — "Inbound damaged" ardindan "Check scan Packaging improved").
# Bu yuzden statuyu degistirmez; koli GLS ne diyorsa odur ve ayrica sorunlu
# listesinde "hasar" turunde gorunur — icindeki giysi zarar gormus olabilir,
# musteriye sorulmasi gerekir. Statuyu degistirdiginde ERP'de "EXCEPTION"
# yaziyordu ama GLS'in kendi sitesi "bugun teslim" diyordu.
DAMAGE_PHRASES = (
    "damaged",
)

ISSUE_PHRASES = BLOCKING_PHRASES + DAMAGE_PHRASES

# Bir sorun ifadesini ICERSE BILE sorun SAYILMAYAN olaylar — allowlist, sorun
# taramasindan once bakilir (bkz. `_issue_event`).
#
# "Shipment locked/Delivery to a GLS Point" metni `BLOCKING_PHRASES`teki
# "shipment locked"a takiliyordu; oysa bu, kolinin alicinin talebiyle bir GLS
# ParcelShop'una YONLENDIRILMESIDIR — rutin bir teslimat duzenlemesi, basarisiz
# teslimat degil. Canli olcum (2026-08-31, kullanici bildirdi: "sorun gozuken
# ama aslinda sorun olmayanlar var"): 38120177022452/022445/022469 uc koli de
# GLS'e gore `state=Announced` (yani daha yolun basinda, hicbir aksilik yok)
# iken panelde "Sorunlu" gorunuyordu. Ustelik "shipment locked" bir HARD
# blocking ifadesi oldugu icin GLS'in canli sinyali bile bunu temizleyemiyordu.
NOT_AN_ISSUE_PHRASES = (
    "delivery to a gls point",
    "delivery to a gls parcelshop",
)

# Teslimat sayilan olaylar. ParcelShop'a birakilan koli de TESLIM EDILMISTIR
# (kullanici karari, 2026-08-31: "parcelshopa teslim edilenler delivered
# gozukmeli exception degil"). GLS bunun icin iki ayri metin kullaniyor:
#   "The parcel has been delivered at the ParcelShop (see ParcelShop information)."
#   "Handing over the parcel to the recipient at the GLS ParcelShop."
# Ilki zaten "has been delivered" iceriyor, ikincisi ICERMIYOR (canli ornek
# 38120177012859) — bu yuzden ayrica yaziliyor.
DELIVERY_PHRASES = (
    "has been delivered",
    "handing over the parcel to the recipient",
)

# Sorunu KAPATAN olaylar. GLS basarisiz teslimati ertesi gun tekrar dener; bir
# sorun olayindan SONRA bunlardan biri gelmisse koli yeniden normal akista
# demektir ve artik sorunlu degildir (canli olcum 2026-08-11: 26 "sorunlu"
# parcanin 13'u aslinda o gun dagitima cikmisti).
# "out for delivery" BILEREK yok: GLS'in "Not out for delivery/Consignee absent"
# metni bunu icerir ve sorunu yanlislikla kapatirdi.
RECOVERY_PHRASES = (
    "expected to be delivered",
    "has been delivered",
)


def _event_text(event: dict) -> str:
    """Olay metni — YALNIZCA aciklama; konum EKLENMEZ.

    Eskiden sona depo adi yapistiriliyordu ("... (Madrid ES)"). Konum parcanin
    kendi alanlarinda zaten var; aciklamaya karisinca ayni sorun her depoda
    ayri bir metin oluyordu ("could not be delivered (Madrid ES)" ile
    "... (Nancy FR0054)" iki farkli deger) — ne panelde gruplanabiliyor ne de
    Excel'de suzulebiliyordu. Metin ayrica ERP'ye (`UD_TasimaAciklama`) ve
    musteriye giden Excel'e gidiyor; oralarda depo kodunun isi yok.
    """
    return event.get("descriptionEN") or event.get("descriptionNL") or ""


def _handover_event(events: list[dict]) -> dict | None:
    """GLS'in koliyi fiziksel olarak aldigi ILK olay; hic yoksa None.

    Bastan aranir: "kargo ne zaman hareket etti" sorusunun cevabi ilk gercek
    tarama olayidir. Statuye bakilmaz cunku GLS `state` alanini koli yola
    ciktiktan sonra bile "Announced" birakabiliyor.
    """
    for event in events:
        low = (event.get("descriptionEN") or "").lower()
        if low and not any(phrase in low for phrase in PRE_HANDOVER_EVENTS):
            return event
    return None


def _issue_event(events: list[dict], phrases: tuple[str, ...] = ISSUE_PHRASES) -> dict | None:
    """HALA acik olan sorun olayi; yoksa None.

    Sondan basa yurunur ve once hangisiyle karsilasilirsa o kazanir: bir
    kurtarma olayi cikarsa sorun kapanmistir, sorun olayi cikarsa aciktir.
    Yon onemli — iki canli ornek ayni anda dogru cikmali:

      38120177014105: 10 Agu "teslim edilemedi" -> 11 Agu "bugun dagitima
        cikacak". Sorun kapandi; koli dagitimda, sorunlu degil.
      38120177004885: 4 Agu teslim -> 5 Agu "alici kabul etmedi" -> iade.
        Sondan basa giderken once "kabul etmedi"ye rastlanir, teslimat
        olayina degil; sorun ACIK kalir (dogrusu budur).
    """
    for event in reversed(events):
        low = (event.get("descriptionEN") or "").lower()
        # Allowlist ONCE: "Shipment locked/Delivery to a GLS Point" bir sorun
        # ifadesi ICERIR ama sorun DEGILDIR (bkz. NOT_AN_ISSUE_PHRASES).
        if any(phrase in low for phrase in NOT_AN_ISSUE_PHRASES):
            continue
        if any(phrase in low for phrase in RECOVERY_PHRASES):
            return None
        if any(phrase in low for phrase in phrases):
            return event
    return None


def _fix_mojibake(text: str) -> str:
    """GLS NL takip ucunun cift kodlanmis UTF-8'ini duzeltir.

    Canli veride teslim alanin adi 'Léonie' yerine 'LÃ©onie' geliyor:
    sunucu UTF-8 byte'larini latin-1 sanip bir kez daha UTF-8'e ceviriyor.
    Ayni yolu ters yonde yuruyerek geri aliriz; metin zaten dogruysa donusum
    basarisiz olur ve orijinali doneriz (or. Turkce 'Ş' latin-1'de yoktur).
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _last_index(events: list[dict], phrases: tuple[str, ...]) -> int:
    """`phrases`ten birini iceren SON olayin sirasi; yoksa -1.

    Teslimat ve iade olaylarini KARSILASTIRMAK icin: hangisi daha sonraysa
    kolinin gercek akibeti odur (bkz. `map_nl_tt_parcel`).
    """
    for index in range(len(events) - 1, -1, -1):
        low = (events[index].get("descriptionEN") or "").lower()
        if any(phrase in low for phrase in phrases):
            return index
    return -1


def _delivery_event(events: list[dict]) -> dict | None:
    """Teslimat olayi — sondan basa aranir (parca yeniden teslim edilebilir)."""
    index = _last_index(events, DELIVERY_PHRASES)
    return events[index] if index >= 0 else None


def _return_event(events: list[dict]) -> dict | None:
    """Iade olayi — sondan basa aranir, `_delivery_event` ile AYNI gerekce.

    GLS'in `state` alani teslimatta oldugu gibi iadede de bayatlayabiliyor
    (canli olcum, 2026-08-26: 38120177014396 gercekten iade edilmis ama GLS
    hala `state=OutForDelivery` diyordu; 014402/012897 icin `NotDelivered`).
    Iade olayindan SONRA gelen "Change completed for..." gibi rutin
    kayit-kapatma olaylari `state`i guncellemiyor ve son olay metni de iade
    ifadesi tasimiyor — bu yuzden `state`e GUVENILEMEZ, TUM gecmis taranir.
    """
    for event in reversed(events):
        low = (event.get("descriptionEN") or "").lower()
        if any(phrase in low for phrase in RETURN_TEXT_PHRASES):
            return event
    return None


def map_nl_tt_parcel(parcel: dict) -> dict:
    """GLS NL takip ucundaki bir parcayi ortak sozluge cevirir."""
    events = parcel.get("events") or []

    note, event_at = "", ""
    if events:
        note = _event_text(events[-1])
        event_at = events[-1].get("date") or ""

    # ERP'ye (erp_Box.UD_TasimaAciklama) GLS'in kendi metni gider; asagida `note`
    # teslimatta Turkce'ye cevriliyor, bu kopya cevrilmeden kalir.
    raw_note = note

    state = str(parcel.get("state") or "").strip().lower()

    # Teslimat mi iade mi? GLS'in `state` alani IKISINDE DE bayatlayabiliyor
    # (canli ornekler: 38120177004885 — 7 Agu teslim ama `state=InRegion`;
    # 38120177014396 — iade edilmis ama `state=OutForDelivery`), bu yuzden
    # OLAY GECMISI esastir ve hangisi DAHA SONRAYSA o kazanir.
    #
    # Sira karsilastirmasi SART — iki yon de canlida gorulmustur:
    #   teslim -> sonra iade  (004885: 4 Agu teslim, 5 Agu ret, sonra iade)
    #   iade   -> sonra teslim (yeniden gonderim; teslimat kazanir)
    #
    # Ayrica teslimat olayi SON OLAY OLMAK ZORUNDA DEGIL: ParcelShop'a
    # birakilan koliye GLS gunlerce "stored in the parcel center" yazmaya
    # devam ediyor (canli ornek 38120177007589). Eskiden yalnizca son olaya
    # bakiliyordu; o yuzden ParcelShop'a TESLIM EDILMIS koliler panelde
    # "exception" gorunuyordu (kullanici bildirdi, 2026-08-31).
    delivery_index = _last_index(events, DELIVERY_PHRASES)
    return_index = _last_index(events, tuple(RETURN_TEXT_PHRASES))

    delivered_date = ""
    if return_index > delivery_index:
        state = "returned"
    elif state == "delivered" or delivery_index >= 0:
        state = "delivered"
        delivered = events[delivery_index] if delivery_index >= 0 else {}
        # Teslim tarihi son olayin degil, TESLIMAT olayinin tarihidir.
        delivered_date = delivered.get("date") or event_at
        receiver = _fix_mojibake((delivered.get("details") or "").strip())
        if receiver:
            note = delivered_note(receiver)

    # Bilinmeyen bir `state` sessizce "in_transit"e sabitlenirse teslim edilmis
    # parca sonsuza kadar aktif gorunur; bu yuzden olay metnine duseriz.
    status = NL_TT_STATES.get(state) or status_from_event(note)

    # GLS'in SU ANKI `state`i acikca "dagitimda" diyorsa VE gecmisteki tek
    # sorun rutin bir "depoda bekliyor" ise (musteri kaynakli SERT bir ret/
    # iade DEGIL — bkz. HARD_BLOCKING_PHRASES), buna guvenilir. Canli olcum
    # (2026-08-27, kullanici bildirdi): 20 koli icin GLS state `InRegion`
    # diyordu, 8'i gecmiste bir "stored in the parcel center" olayi yuzunden
    # bizde hala "exception" gorunuyordu — net kurtarma olayi hic gelmemis
    # olsa bile GLS'in kendisi zaten "bugun dagitimda" diyor.
    is_live_ofd = state in ("inregion", "indelivery", "outfordelivery")

    # Sorun SON olayda degil, gecmisin ORTASINDA olabilir: canli ornek
    # 38120177004885 — 4 Agu teslim, 5 Agu "alici kabul etmedi", ardindan iade;
    # son olay masum "parca merkezden ayrildi". Bu yuzden tum gecmis taranir.
    # Teslim edilmis ya da iptal edilmis parca sorunlu sayilmaz.
    issue = (_issue_event(events)
            if status not in ("delivered", "cancelled", "returned") else None)
    issue_text = _event_text(issue) if issue else ""

    # Yalnizca RUTIN bir sorun varsa (sert bir musteri/nakliye basarisizligi
    # YOKSA) GLS'in canli "dagitimda" sinyaline guvenilir.
    if issue and is_live_ofd and not _issue_event(events, HARD_BLOCKING_PHRASES):
        issue = None
        issue_text = ""

    # Statuyu YALNIZCA teslimati durduran bir sorun degistirir. Hasar durdurmaz —
    # koli sorunlu listesinde gorunur ama GLS ne diyorsa durum odur.
    if issue and _issue_event(events, BLOCKING_PHRASES):
        status = "exception"

    # SEBEBI OLMAYAN "sorunlu" OLAMAZ. GLS'in `NotDelivered` state'i hem gercek
    # bir basarisizlik hem de "bugun olmadi, yarin tekrar denenecek" icin
    # kullaniliyor; `NL_TT_STATES` onu koru korune "exception"a esliyordu.
    # Sonuc: panelde sorun METNI BOS "Sorunlu" kayitlar (canli olcum
    # 2026-08-31: 38120177020458/020465 — son olay "Not delivered - delivery
    # planned next workingday", yani koli normal akista). Tanimlanabilir bir
    # sorun olayi yoksa statu OLAY METNINDEN turetilir.
    if status == "exception" and not issue:
        status = status_from_event(note)

    handover = _handover_event(events)

    # GLS'e HENUZ GECMEMIS koli "yolda" olamaz. GLS'in `state` alani bu asamada
    # "Received" diyor — ama bu VERININ alindigi anlamina gelir, kolinin degil;
    # `NL_TT_STATES` onu dogrudan `in_transit`e esliyordu.
    #
    # Canli olcum (2026-09-01, kullanici bildirdi: "bugun olusturulan koliler
    # neden in transit gorunuyor, bir kismi 4'unde cikacak" / "GLS alinca
    # okutulma gorunur"): o gun basilan etiketlerde `state=Received` idi ama
    # olaylarin HEPSI devralma oncesiydi ("...not yet handed over", "provided
    # by the sender for collection").
    #
    # Olcut `handover`dir: gercek bir hareket olayi yoksa (tum olaylar
    # PRE_HANDOVER_EVENTS'te) koli hala gondericidedir. Teslim/iade/sorun
    # statuleri BOZULMAZ — yalnizca "yolda"/"dagitimda" geri alinir.
    # `events` BOSSA kural UYGULANMAZ: hicbir olay yokken "devralinmadi"
    # sonucu cikarilamaz, elimizdeki tek sinyal `state`tir. Kural ancak
    # olaylar VAR ve hepsi devralma oncesiyse calisir.
    if events and handover is None and status in ("in_transit", "out_for_delivery"):
        status = "created"

    tracking_url = parcel.get("uri") or (f"https://www.gls-info.nl/track-and-trace?parcelno={parcel.get('parcelNo')}" if parcel.get('parcelNo') else "")
    return {
        "status": status,
        "note": note,
        "raw_note": raw_note,
        "event_at": event_at,
        "delivered_date": delivered_date,
        "handed_over_at": (handover or {}).get("date") or "",
        "issue_text": issue_text,
        "tracking_url": tracking_url,
        "raw_events": events,
    }


# GLS NL'in TOPLU takip ucu (`parcel_details`, `map_nl_tt_parcel`in girdisi) ile
# teslimat detayi ucu (`delivery_details`) AYNI ANDA senkron degil — canli
# olcum (2026-08-27/28): "dagitimda" VE "yolda" gorunen kolilerin bir kismi
# `delivery_details`e gore ZATEN teslim edilmisti — bazen saatler, bazen
# (38120177007466) 2 GUN, bazen (4 farkli ulke/depo, 2026-08-28) 18-20 SAAT
# once. Toplu ucun olay listesi teslimat taramasini GEC yaziyor; GLS'in kendi
# "dagitimda" adiminda hic gorunmeden dogrudan gecmisiyle geliyor.
_NL_LOCAL_TZ = ZoneInfo("Europe/Amsterdam")

# `_live_delivery_scan` parca basina ayri bir istektir ve artik SIRAYLA
# (tek tek) yapilir — bkz. Tracker._nl_results. Bir ara surumde kucuk bir
# is parcacigi havuzunda paralel kosuyordu; tek-vCPU sunucuda 4-6 es zamanli
# `requests` is parcacigi (TLS + JSON) GIL'i doverek asyncio event loop'unu
# ~20 sn ac birakti ve panel her turda dondu (kullanici 2026-09-03: "yine
# dondu site", "sira sira yapsin"). Sirali akista her istek arasinda GIL
# serbest kalir, loop sira alir.

# Bir turda EN FAZLA kac "in_transit" parcaya canli teslimat sorgusu atilir.
# "out_for_delivery" ve "exception" parcalar bu tavana TABI DEGIL — sayilari
# az, hepsi her tur taranir. `active_parcels` `last_checked_at`e gore sirali
# (en eskiler once), yani tavana takilan in_transit parcalar sonraki turda
# sirasi gelince taranir.
#
# 20 -> 100: 20 iken canli olcum (2026-09-04) — "kismi teslimatlar ... teslim
# edilmis olmasina ragmen siteye yansimiyor": 36 "partial" parcadan 27'si
# AYNI GUN teslim edilmisti ama ~270 in_transit parca icin 20/tur kotasi
# tam turu 3,5+ saatte bir cevirebiliyordu, aslinda pratikte yetismiyordu.
# 20'lik tavan asil donma sebebi (indekssiz sorgu + fsync firtinasi + paralel
# tarama) COZULMEDEN once konmustu; o ucu artik yok (bkz. commit'ler
# 7afba2b/synchronous=NORMAL, 186ea16/sirali tarama) — tavan asiri
# temkinliydi. 100'de tam devir ~2-3 tura (30-45 dk) iner, event loop yine
# donmez (sirali + DB okumalari is parcacigi havuzunda).
LIVE_SCAN_MAX_PER_TICK = 100


def _live_delivery_scan(client, tracking_no: str, raw: dict | None = None) -> dict | None:
    """`delivery_details`teki `deliveryScanInfo` gercekten teslim oldugunu
    soyluyorsa teslimat bilgisini `map_nl_tt_parcel` ciktisiyla ayni sozlukte
    doner; henuz degilse ya da sorgu patlarsa None.

    `raw`: TOPLU `parcel_details` yanitindaki bu parcaya ait ham sozluk
    (varsa). `delivery_details` normalde `uniqueNo`/`jobDate`i almak icin
    AYRI bir `parcel_detail()` istegi atar (`_pod_key`) — biz bunu zaten
    toplu cagriDAN biliyoruz, `raw`dan gecirilirse o ikinci istek atlanir.
    Canli olcum (2026-08-28): bu atlama olmadan "in_transit"e de genisleyen
    kontrol bir turu 15 dakikanin cok uzerine tasidi (parca basina 2 istek).

    `dateTime` alani DIGER butun zaman damgalarindan farkli olarak YEREL
    (Europe/Amsterdam) saattir, UTC degil ve sonunda "Z" yoktur — GLS'in
    musteri sitesindeki saatle (kullanicinin ekran goruntusu: "10:04") BIREBIR
    eslesiyor. Veritabani her yerde saat dilimsiz UTC tuttugu icin (bkz.
    tracking/db.py `_now()`), buraya yazilmadan once UTC'ye cevrilir.
    """
    key = None
    if raw and raw.get("uniqueNo") and raw.get("jobDate"):
        from gls_api.nl_track_client import _pod_job_date
        key = {"uniqueNo": raw["uniqueNo"], "jobDate": _pod_job_date(raw["jobDate"])}
    try:
        details = client.delivery_details(tracking_no, key=key)
    except Exception:
        return None
    info = (details or {}).get("deliveryScanInfo") or {}
    if not info.get("isDelivered"):
        return None
    local_str = info.get("dateTime") or ""
    try:
        stamp_utc = (datetime.fromisoformat(local_str)
                     .replace(tzinfo=_NL_LOCAL_TZ)
                     .astimezone(timezone.utc)
                     .replace(tzinfo=None)
                     .isoformat(timespec="seconds") + "Z")
    except ValueError:
        stamp_utc = ""
    receiver = (info.get("signedBy") or "").strip() or "-"
    note = delivered_note(receiver)
    # `raw`daki (toplu ucun ayni turdaki cevabi) olay gecmisinden okutma
    # tarihi de cikarilir — yoksa bu yoldan "delivered"e gecen bir koli
    # `handed_over_at`i HIC yakalayamaz (statu terminal oldugu icin bir daha
    # hic sorulmaz, kalici bosluk kalir). Canli olcum (2026-08-28): tam
    # boyle 173 kolide UD_OkutmaTarihi hep bos kalmisti, elle duzeltildi —
    # bu, tekrar birikmesin diye kalici cozum.
    handover = _handover_event((raw or {}).get("events") or [])
    return {
        "status": "delivered",
        "note": note,
        "raw_note": note,
        "event_at": stamp_utc,
        "delivered_date": stamp_utc,
        "handed_over_at": (handover or {}).get("date") or "",
        "issue_text": "",
        "tracking_url": (f"https://www.gls-info.nl/track-and-trace?parcelno={tracking_no}"),
        "raw_events": [],
    }


# GLS Track & Trace v1 `parcels[].status` -> bizim statu evrenimiz. Deger
# bosluksuz BUYUK harfe getirilip aranir ("Not delivered" -> "NOTDELIVERED").
# Liste eksiksiz degil; bilinmeyen bir deger gelirse olay metnine duseriz.
GLSG_TT_STATES = {
    "DELIVERED": "delivered",
    "DELIVEREDPS": "delivered",
    "DELIVEREDPARCELSHOP": "delivered",
    "PICKEDUP": "delivered",
    "OUTFORDELIVERY": "out_for_delivery",
    "FINALDELIVERY": "out_for_delivery",
    "INDELIVERY": "out_for_delivery",
    "INTRANSIT": "in_transit",
    "INWAREHOUSE": "in_transit",
    "INROUTE": "in_transit",
    "PREADVICE": "created",
    "DATARECEIVED": "created",
    "ANNOUNCED": "created",
    "NOTDELIVERED": "exception",
    "DELAYED": "exception",
    "CANCELLED": "cancelled",
    "CANCELED": "cancelled",
    "RETURNED": "returned",
    "RETURNTOSENDER": "returned",
}


def _glsg_str(value) -> str:
    """T&T v1 alani duz metin ya da {code/name/description} sozlugu olabiliyor."""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("description")
                   or value.get("code") or value.get("value") or "").strip()
    return str(value or "").strip()


def _glsg_event(raw: dict) -> dict:
    """T&T v1 olayini ortak olay sozlugu alanlarina indirger.

    `sync_events` ve `_handover_event`/`_issue_event`/`_delivery_event`
    yardimcilari `descriptionEN` + `date` bekliyor; canli T&T v1 bunlari
    `description` + `eventDateTime` olarak veriyor. `city`/`postalCode` cogunlukla
    bos, `country` dolu (or. "IE").
    """
    return {
        "date": _glsg_str(raw.get("eventDateTime") or raw.get("timestamp")
                          or raw.get("date") or raw.get("time")),
        "descriptionEN": _glsg_str(raw.get("description") or raw.get("descriptionEN")
                                   or raw.get("text")),
        "code": _glsg_str(raw.get("code") or raw.get("eventCode")),
        "depot": _glsg_str(raw.get("city") or raw.get("location")
                           or raw.get("depotName")),
        "country": _glsg_str(raw.get("country")),
    }


def map_glsg_tt_parcel(parcel: dict) -> dict:
    """GLS Track & Trace v1 (`simple/trackids`) parcasini ortak sozluge cevirir.

    `map_nl_tt_parcel` ile AYNI cikti sozlugu — `Tracker._apply` ikisini de
    ayni sekilde isler. Canli yanit TAM olay gecmisini `events` icinde YENIDEN
    ESKIYE sirali veriyor; burada eskiden yeniye cevrilir. Ust duzey alanlar:
      status        : "status"          (or. "DELIVERED")
      teslim ani    : "statusDateTime"  (ISO, zaman dilimi ofsetli)
      parca no      : "unitno" / "requested"
    """
    raw_events = [_glsg_event(e) for e in (parcel.get("events") or [])
                  if isinstance(e, dict)]
    raw_events.sort(key=lambda e: e["date"])          # canli yanit yeni->eski gelir

    state = _glsg_str(parcel.get("status") or parcel.get("statusInfo")).upper() \
        .replace(" ", "").replace("_", "").replace("-", "")
    status_dt = _glsg_str(parcel.get("statusDateTime") or parcel.get("statusTimestamp")
                          or parcel.get("statusDate"))

    if raw_events:
        note = raw_events[-1]["descriptionEN"]
        event_at = raw_events[-1]["date"]
    else:
        note = _glsg_str(parcel.get("statusText") or parcel.get("statusInfo")
                         or parcel.get("statusReason"))
        event_at = status_dt
    raw_note = note

    # Teslim: GLS `status` DELIVERED diyorsa ya da son olay teslimatsa.
    delivered_date = ""
    delivery = _delivery_event(raw_events)
    if GLSG_TT_STATES.get(state) == "delivered" or delivery \
            or classify_event_text(note) == "delivered":
        delivery = delivery or {}
        delivered_date = delivery.get("date") or status_dt or event_at
        receiver = (_glsg_str((parcel.get("signature") or {}).get("name"))
                    or _glsg_str(parcel.get("deliveredTo")))
        if receiver:
            note = delivered_note(receiver)
        status = "delivered"
    else:
        status = GLSG_TT_STATES.get(state) or status_from_event(note)

    # Sorun/teslim-alma tespiti YALNIZCA olay listesi geldiyse anlamli.
    issue = (_issue_event(raw_events)
             if raw_events and status not in ("delivered", "cancelled", "returned")
             else None)
    issue_text = _event_text(issue) if issue else ""
    if issue and _issue_event(raw_events, BLOCKING_PHRASES):
        status = "exception"
    handover = _handover_event(raw_events) if raw_events else None

    track_id = _glsg_str(parcel.get("trackId") or parcel.get("trackID")
                         or parcel.get("trackid") or parcel.get("requested"))
    tracking_url = _glsg_str(parcel.get("uri") or parcel.get("trackingUrl")) or (
        f"https://gls-group.com/track?match={track_id}" if track_id else "")

    return {
        "status": status,
        "note": note,
        "raw_note": raw_note,
        "event_at": event_at,
        "delivered_date": delivered_date,
        "handed_over_at": (handover or {}).get("date") or "",
        "issue_text": issue_text,
        "tracking_url": tracking_url,
        # Sadece GLS gercekten olay listesi verdiyse; yoksa update_status
        # tek sentetik olay yazsin (log_event=True yolu).
        "raw_events": raw_events or None,
    }


def hours_since(stamp: str | None) -> float | None:
    """ISO damgasindan bu yana gecen saat; damga yok ya da bozuksa None.

    `tracking/db.py:_now()` saat dilimsiz UTC yaziyor — karsilastirma da oyle
    yapilir, yoksa "naive/aware" karsilastirmasi TypeError verir.

    Gizli degil: ayni esigi `/health` ucu ve panel saglik seridi de olcuyor;
    ucu ayri ayri hesaplarsa biri digerinden farkli cevap verirdi.
    """
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is not None:
        then = then.astimezone(timezone.utc).replace(tzinfo=None)
    return (datetime.utcnow() - then).total_seconds() / 3600


# ---------- ana tarayici ----------

class Tracker:
    def __init__(
        self,
        db: ShipmentsDB,
        interval_minutes: int = 15,
        on_change: Optional[Callable[[dict], None]] = None,
        max_per_tick: int = 0,
        pod_fetcher: Optional[Callable[[dict], object]] = None,
        max_pod_per_tick: int = 0,
        alert_sender: Optional[Callable[[dict], object]] = None,
        alert_after_hours: int = 0,
        erp_syncer: Optional[Callable[[ShipmentsDB], dict]] = None,
        erp_sync_minutes: Optional[int] = None,
    ):
        self.db = db
        self.interval_minutes = interval_minutes
        self.on_change = on_change
        # Panel MSSQL'den dolunca 4000+ aktif parca oluyor; hepsini tek turda
        # sormak (20'serli -> 200+ istek) GLS'in saatlik sinirini asiyor. Turda
        # sabit sayida parca sorulur, sira `last_checked_at`e gore doner.
        self.max_per_tick = max_per_tick or config.TRACKER_MAX_PER_TICK
        # None ise POD adimi hic calismaz (testler, CLI kullanimi).
        self.pod_fetcher = pod_fetcher
        self.max_pod_per_tick = max_pod_per_tick or config.TRACKER_MAX_POD_PER_TICK
        # Uyari gondericisi de POD cekici gibi DISARIDAN verilir: mail `notify/`
        # katmaninda, `notify/digest.py` ise `tracking.db`yi import ediyor —
        # buradan `notify`ye uzanmak dairesel bagimlilik olurdu.
        self.alert_sender = alert_sender
        self.alert_after_hours = alert_after_hours or config.TRACKER_ALERT_HOURS
        # MSSQL'den koli cekici de disaridan verilir (None ise senkron isi hic
        # kurulmaz): testler ve CLI canli MSSQL'e uzanmasin.
        self.erp_syncer = erp_syncer
        # `or config...` KULLANILAMAZ — 0 burada gecerli bir deger ("kapat").
        self.erp_sync_minutes = (config.ERP_SYNC_INTERVAL_MINUTES
                                 if erp_sync_minutes is None else erp_sync_minutes)
        self.scheduler = AsyncIOScheduler()
        self.last_run_at: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_updated_count: int = 0
        self.last_pod_count: int = 0
        self.last_erp_count: int = 0
        self.last_erp_error: Optional[str] = None
        self.last_erp_sync_at: Optional[str] = None
        self.last_erp_sync_error: Optional[str] = None

    def start(self):
        self.scheduler.add_job(
            self._tick_wrapper,
            "interval",
            minutes=self.interval_minutes,
            id="tracker_tick",
            next_run_time=datetime.now(),  # ilk kosuyu hemen
        )
        self._schedule_erp_sync()
        self.scheduler.start()
        log.info("Tracker started (interval=%s dk, erp senkron=%s dk)",
                 self.interval_minutes, self.erp_sync_minutes or "kapali")

    def _schedule_erp_sync(self) -> None:
        """MSSQL senkronunu AYRI bir is olarak kurar (takip turunun icinde DEGIL).

        Takip 15 dakikada bir, senkron saatte bir calisir — ayni ritme
        zorlanmalari icin sebep yok ve senkron MSSQL'e binlerce satirlik bir
        sorgu; takip turunu uzatmamali.

        Takip isinin aksine `next_run_time` VERILMEZ: acilista hemen MSSQL'e
        gitmek, konteynerin her yeniden baslatilmasini agir bir sorgu yapardi.
        Acil durumda panelin "MSSQL'den Yenile" dugmesi zaten var.
        """
        if self.erp_syncer is None or self.erp_sync_minutes <= 0:
            return
        self.scheduler.add_job(self._erp_sync_wrapper, "interval",
                               minutes=self.erp_sync_minutes, id="erp_sync")

    def reconfigure(self, interval_minutes: int = 0, max_per_tick: int = 0,
                    max_pod_per_tick: int = 0,
                    erp_sync_minutes: Optional[int] = None) -> None:
        """Ayarlar sayfasindan gelen degisiklikleri CALISIRKEN uygular.

        Tavanlar her turda ozniteliken taze okundugu icin atama yeter; APScheduler
        aralik ise ise gomulu oldugundan acikca yeniden planlanmali. Zamanlayici
        henuz baslamamissa (CLI, testler) reschedule sessizce atlanir.
        """
        if max_per_tick:
            self.max_per_tick = max_per_tick
        if max_pod_per_tick:
            self.max_pod_per_tick = max_pod_per_tick
        if erp_sync_minutes is not None:
            self._reconfigure_erp_sync(erp_sync_minutes)
        if not interval_minutes or interval_minutes == self.interval_minutes:
            return
        old, self.interval_minutes = self.interval_minutes, interval_minutes
        if self.scheduler.running:
            self.scheduler.reschedule_job("tracker_tick", trigger="interval",
                                          minutes=interval_minutes)
        log.info("Tracker araligi %s -> %s dk", old, interval_minutes)

    def _reconfigure_erp_sync(self, minutes: int) -> None:
        """0'a dusurulunce is KALDIRILIR, 0'dan buyutulunce yeniden kurulur."""
        if minutes == self.erp_sync_minutes:
            return
        old, self.erp_sync_minutes = self.erp_sync_minutes, minutes
        if self.scheduler.running and self.erp_syncer is not None:
            if minutes > 0 and old > 0:
                self.scheduler.reschedule_job("erp_sync", trigger="interval",
                                              minutes=minutes)
            elif minutes > 0:
                self._schedule_erp_sync()
            else:
                self.scheduler.remove_job("erp_sync")
        log.info("ERP senkron araligi %s -> %s dk", old, minutes)

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass

    async def _tick_wrapper(self):
        try:
            await asyncio.to_thread(self.tick)
        except Exception as exc:
            log.exception("tick failed")
            self.last_error = str(exc)
            # `tick` kendi sonucunu yazamadan patladi; saatin islemesi icin
            # basarisiz tur burada kaydedilir.
            try:
                self.db.record_tick(ok=False, error=str(exc))
            except Exception:
                log.exception("saglik kaydi yazilamadi")
        # Tur basarili da olsa denetlenir: ariza bittiginde `record_tick`
        # `alerted_at`i temizler, bu cagri da bir sonraki arizaya hazir olur.
        try:
            await asyncio.to_thread(self.check_health)
        except Exception:
            log.exception("saglik denetimi basarisiz")

    def check_health(self) -> bool:
        """Tarama uzun suredir basarisizsa BIR KEZ uyari gonderir.

        Doner: uyari gonderildi mi.

        Esik saat cinsinden ve bilerek genis (varsayilan 6 saat = 24 tur): GLS
        hiz siniri (429) ve kisa kesintiler normaldir, her birinde mail atmak
        uyarilari okunmaz yapardi.

        Uygulama ayakta ama tarama bozuksa yakalar. Uygulamanin TAMAMEN olmesi
        bu yolla anlasilamaz — o durumda hicbir sey calismaz; onun icin
        `/health` ucu 503 doner (bkz. web/main.py).
        """
        if self.alert_sender is None or self.alert_after_hours <= 0:
            return False
        health = self.db.health()
        if health.get("alerted_at"):        # bu ariza icin zaten yazildi
            return False
        # Hic basarili tur olmadiysa ilk turdan beri gecen sure olculur; yoksa
        # daha ILK turundan bozuk gelen bir kurulum sonsuza dek sessiz kalirdi.
        stale_for = hours_since(health.get("last_success_at")
                                 or health.get("last_run_at"))
        if stale_for is None or stale_for < self.alert_after_hours:
            return False
        log.warning("Tarama %.1f saattir basarisiz — uyari gonderiliyor", stale_for)
        self.alert_sender({**health, "stale_hours": round(stale_for, 1),
                           "threshold_hours": self.alert_after_hours})
        self.db.mark_alerted()
        return True

    # ------------------------------------------------------------------
    def _fetch(self, channel: str, tracking_no: str) -> dict | None:
        """Tek parca takip bilgisi (NL disi kanallar).

        None: sorgulanabilir saglayici yok.
        """
        provider = providers.tracking_provider_for(channel)
        if provider is None:
            return None
        name, client = provider
        if name == "shipit":
            return map_shipit_details(client.parcel_details(tracking_no))
        if name == "fedex":
            # `track_one` sonuc bulamazsa None doner — GLS NL'in "bilinmeyen
            # numarayi sessizce dusur" davranisiyla ayni (bkz. _nl_results):
            # FedEx bu numarayi henuz tanimiyor olabilir, hata degildir.
            raw = client.track_one(tracking_no)
            return map_fedex_result(raw) if raw else None
        if name == "glsg_tt":
            found = client.track_simple([tracking_no])
            data = found.get(str(tracking_no).strip())
            return map_glsg_tt_parcel(data) if data else None
        return map_tt_detail(client.get_tu_detail(tracking_no))

    def _apply(self, parcel: dict, info: dict) -> bool:
        """Takip bilgisini kaydeder; statu degistiyse True."""
        tn = parcel["tracking_no"]
        self.db.update_tracking_info(
            tn,
            last_event_at=info["event_at"],
            last_event_text=info["note"],
            delivered_date=info["delivered_date"],
            explain=info.get("raw_note") or info["note"],
            handed_over_at=info.get("handed_over_at", ""),
            # Sorun cozulunce ALANIN BOSALMASI gerekir (bkz. _CLEARABLE_FIELDS);
            # digerlerinden farkli olarak bos deger de yazilir.
            issue_text=info.get("issue_text", ""),
            tracking_url=info.get("tracking_url", ""),
        )
        synced_events = False
        if info.get("raw_events"):
            try:
                self.db.sync_events(tn, info["raw_events"])
                synced_events = True
            except Exception:
                log.exception("Olay gecmisi senkronize edilemedi: %s", tn)
        old_status = parcel.get("status")
        # `sync_events` GLS'in TAM gecmisini zaten yazdiysa `update_status`
        # AYRICA sentetik bir olay yazmamali — bkz. ShipmentsDB.update_status.
        changed = self.db.update_status(tn, info["status"], note=info["note"] or "tracker",
                                         log_event=not synced_events)
        if changed and self.on_change:
            try:
                self.on_change({
                    "tracking_no": tn,
                    "old_status": old_status,
                    "new_status": info["status"],
                    "status": info["status"],
                    "channel": parcel["channel"],
                    "info": info,
                })
            except Exception:
                pass
        return changed

    def _nl_results(self, parcels: list[dict], errors: list[str]):
        """NL parcalari icin (parcel, info) ciftleri — 20'serli toplu sorgu.

        Canli teslimat sorgusu (`_live_delivery_scan`) TUR BASINA
        `LIVE_SCAN_MAX_PER_TICK` ile sinirli: tavana varinca kalan
        "in_transit"/"out_for_delivery" parcalar toplu ucun verdigi bilgiyle
        gecer, canli sorgu bir sonraki tura kalir.
        """
        if not parcels:
            return
        live_budget = LIVE_SCAN_MAX_PER_TICK
        provider = providers.tracking_provider_for(providers.CHANNEL_NL)
        if provider is None:
            return
        _, client = provider

        for i in range(0, len(parcels), MAX_PARCELS_PER_CALL):
            chunk = parcels[i:i + MAX_PARCELS_PER_CALL]
            try:
                found = client.parcel_details([p["tracking_no"] for p in chunk])
            except API_ERRORS as api_err:
                # Tum grup ayni istekte gitti; hatayi parca parca degil bir kez yaz.
                errors.append(f"NL grubu ({len(chunk)} parca, ilk {chunk[0]['tracking_no']}): {api_err}")
                continue

            results: dict[str, dict] = {}
            # Toplu ucun olay listesi teslimati saatler/GUNLER gec yazabilir
            # (bkz. _live_delivery_scan docstring). Ilk surumde yalnizca
            # "out_for_delivery" kontrol ediliyordu, ama canli olcum
            # (2026-08-28, 4 farkli ulke/depo): teslimat olayi hic ARA
            # "dagitimda" adimindan gecmeden dogrudan gecmisle birlikte
            # geldi — toplu ucun kendisi 18-20 SAAT geriden akiyordu (GLS'in
            # gece yarisi civari topluca yayinladigi anlasiliyor). Kullanici
            # gercek zamanli istedigi icin (2026-08-28: "sabahin korunde
            # mesaj yagiyor, oyle degildi") kapsam "in_transit"e de
            # genisletildi — GLS ne zaman gerceklesirse ayni tur icinde
            # yakalanir, ertesi sabaha kalmaz.
            #
            # BU kontrol PARCA BASINA ayri bir HTTP istegi gerektirir. Bir ara
            # surumde KUCUK bir is parcacigi havuzunda PARALEL yapiliyordu; tek
            # vCPU sunucuda 4-6 es zamanli `requests` is parcacigi (TLS + JSON)
            # GIL'i doverek asyncio event loop'unu ~20 sn ac birakti ve panel
            # her turda dondu (kullanici 2026-09-03: "yine dondu site",
            # "giremiyorum", "sira sira yapsin"). Artik SIRAYLA, tek tek
            # yapiliyor: her istek arasinda GIL serbest kaliyor, event loop
            # sira aliyor, panel akici kaliyor. Cok sayida parcayi asmamak icin
            # tavan var (asagi; out_for_delivery haric).
            pending = []
            for parcel in chunk:
                data = found.get(parcel["tracking_no"])
                if data is None:
                    # GLS bilinmeyen numarayi yanittan sessizce duser: parca bu
                    # hesaba ait degil ya da henuz GLS sistemine girmemis.
                    # Yine de "bakildi" isaretlenir, yoksa hic taninmayan bir parca
                    # sinirli tur kotasini her seferinde bastan isgal eder.
                    self.db.mark_checked(parcel["tracking_no"])
                    continue
                info = map_nl_tt_parcel(data)
                # "out_for_delivery" ve "exception": her zaman canli sorgula
                # (az sayida, yuksek deger).
                #   - OFD: teslim birazdan, ayni turda yakalansin.
                #   - exception: toplu ucun olay akisi "depoda bekliyor" derken
                #     `deliveryScanInfo.isDelivered` cogunlukla TRUE oluyor —
                #     ozellikle SINIR OTESI (FR/ES/BE) kolilerde son adim
                #     olay akisina geri yazilmiyor ama teslim tarama bilgisi
                #     var (kullanici 2026-09-04: "gls api de teslim edildi
                #     diyor, siteye yansimiyor"). Canli olcumde 4/5 takilan
                #     exception aslinda teslim edilmisti.
                # "in_transit": tur basina tavanli — bkz. LIVE_SCAN_MAX_PER_TICK.
                if info["status"] in ("out_for_delivery", "exception"):
                    pending.append((parcel, info, data))
                elif info["status"] == "in_transit" and live_budget > 0:
                    pending.append((parcel, info, data))
                    live_budget -= 1
                else:
                    results[parcel["tracking_no"]] = info

            # Sirayla: bir istek biter, sonraki baslar. Her tur bir sonraki
            # NL grubuna (chunk) gecmeden once bu grubun canli sorgulari biter.
            for p, info, data in pending:
                try:
                    live = _live_delivery_scan(client, p["tracking_no"], data)
                except Exception:
                    live = None
                results[p["tracking_no"]] = live if live is not None else info

            for parcel in chunk:
                info = results.get(parcel["tracking_no"])
                if info is not None:
                    yield parcel, info

    def _glsg_results(self, parcels: list[dict], errors: list[str]):
        """IE parcalari icin (parcel, info) ciftleri — T&T v1 10'arli toplu sorgu.

        NL'in 20'lik toplu ucundan sonra en verimli takip yolu: parca basi tek
        istek atan ShipIT/T&T SOAP yerine 10'arli gruplar. Bulunamayan numara
        (GLS `errorCode` ile duser) yine "bakildi" isaretlenir.

        GUNLUK KOTA: T&T v1'in 500 istek/gun siniri var ve tarayici gunde 96 kez
        calisiyor — kota tur ORTASINDA bitebilir. Bu yuzden hem tur basinda
        toptan bakilir (kota bitmisse tur hic baslamaz, 7 ayri hata satiri
        yazilmaz) hem de her grup icin ayrica: kotayi asan gruplar bugun
        sorulmaz, "bakildi" da ISARETLENMEZ — ertesi gun sirayla gelirler.
        """
        if not parcels:
            return
        provider = providers.tracking_provider_for(providers.CHANNEL_IE)
        if provider is None or provider[0] != "glsg_tt":
            return
        _, client = provider

        if client.budget_left() <= 0:
            log.warning("T&T v1 gunluk kotasi dolu — %d IE parcasi bu turda "
                        "sorulmadi (yarin devam)", len(parcels))
            return

        for i in range(0, len(parcels), MAX_TRACKIDS_PER_CALL):
            chunk = parcels[i:i + MAX_TRACKIDS_PER_CALL]
            try:
                found = client.track_simple([p["tracking_no"] for p in chunk])
            except GLSGroupError as api_err:
                if api_err.is_quota_error:
                    # Kota tam bu turda bitti: kalan gruplari hic denemeyelim.
                    log.warning("T&T v1 kotasi tur ortasinda doldu — kalan %d "
                                "IE parcasi yarina kaldi", len(parcels) - i)
                    return
                errors.append(f"IE grubu ({len(chunk)} parca, ilk "
                              f"{chunk[0]['tracking_no']}): {api_err}")
                continue
            except API_ERRORS as api_err:
                errors.append(f"IE grubu ({len(chunk)} parca, ilk "
                              f"{chunk[0]['tracking_no']}): {api_err}")
                continue
            for parcel in chunk:
                data = found.get(parcel["tracking_no"])
                if data is None:
                    self.db.mark_checked(parcel["tracking_no"])
                    continue
                yield parcel, map_glsg_tt_parcel(data)

    def _archive_pods(self) -> int:
        """POD'u eksik teslimatlarin POD'unu ceker; arsivlenen sayisini doner.

        Her teslimatin POD'u yoktur (GLS 204 doner) ve POD teslimattan saatler
        sonra olusabilir; bu yuzden sonuc ne olursa olsun parca "soruldu"
        isaretlenir — sira ilerlesin, ama parca kuyruktan tamamen dusmesin.
        """
        if self.pod_fetcher is None:
            return 0
        archived = 0
        for parcel in self.db.parcels_without_pod(limit=self.max_pod_per_tick):
            try:
                archived += bool(self.pod_fetcher(parcel))
            except Exception:
                # `archive_pod` zaten yutuyor; buradaki bariyer enjekte edilen
                # baska bir fonksiyonun turu durdurmasina karsi.
                log.exception("POD arsivlenemedi: %s", parcel["tracking_no"])
            self.db.mark_pod_attempted(parcel["tracking_no"])
        return archived

    def _push_to_erp(self) -> int:
        """Degisen durumlari ERP'ye (erp_Box) yazar; yazilan satir sayisini doner.

        Tur ICINDE calisir ki ERP kullanicisi kargoyu panelle ayni anda gorsun.
        MSSQL yapilandirilmamissa (mock, gelistirme) sessizce atlanir; bir ERP
        hatasi takip turunu DUSURMEZ, yalnizca loglanir ve satirlar (parmak izi
        yazilmadigi icin) bir sonraki tura kalir.
        """
        if config.ERP_WRITEBACK_ENABLED != "1" or not config.mssql_configured():
            return 0
        try:
            result = writeback.push(self.db)
        except Exception as exc:
            log.warning("ERP'ye yazilamadi: %s", exc)
            self.last_erp_error = str(exc)
            return 0
        self.last_erp_error = None
        written = result["etkilenen"]
        self.last_erp_count = written
        if written:
            log.info("ERP'ye yazildi: %d parca", written)
        # ERP'de karsiligi olmayan takip numarasi sessiz kalmamali: satir bir
        # sonraki tura kalir ve ayni uyari yeniden dusar.
        if result["ulasmayan"]:
            log.warning("ERP'de satiri olmayan %d takip numarasi (ornek: %s)",
                        len(result["ulasmayan"]), ", ".join(result["ulasmayan"][:3]))
        return written

    async def _erp_sync_wrapper(self):
        await asyncio.to_thread(self._sync_from_erp)

    def _sync_from_erp(self) -> int:
        """MSSQL'den yeni kolileri panele ceker; eklenen parca sayisini doner.

        HATA YUTAR. MSSQL'de Sentez'in eszamanli islemleri var ve bu sorgu
        deadlock kurbani secilebiliyor (canli, 2026-08-18: hata 1205). Boyle bir
        hata zamanlayiciyi durdurmamali — bir sonraki saatte kendiliginden
        duzelir; belirti `last_erp_sync_error`da durur.
        """
        if self.erp_syncer is None or not config.mssql_configured():
            return 0
        try:
            result = self.erp_syncer(self.db)
        except Exception as exc:
            log.warning("MSSQL senkronu basarisiz: %s", exc)
            self.last_erp_sync_error = str(exc)
            return 0
        self.last_erp_sync_error = None
        self.last_erp_sync_at = timez.utc_iso()
        log.info("MSSQL senkronu: %s eklendi, %s guncellendi",
                 result.get("added", 0), result.get("updated", 0))
        return result.get("added", 0)

    def tick(self):
        """Aktif kargolari tara, statulerini guncelle, eksik POD'lari arsivle."""
        parcels = self.db.active_parcels(limit=self.max_per_tick)
        # ParcelShop'ta ALINMAYI BEKLEYEN koliler `delivered` oldugu icin
        # `active_parcels`e girmez, ama akibetleri hala degisebilir: musteri
        # almazsa GLS onlari iadeye dondurur. Bu yuzden AYRICA sorulurlar
        # (bkz. ShipmentsDB.parcel_shop_pending; kullanici bildirdi 2026-08-31).
        seen = {p["tracking_no"] for p in parcels}
        parcels += [p for p in self.db.parcel_shop_pending(limit=self.max_per_tick)
                    if p["tracking_no"] not in seen]
        updated = 0
        # GLS'e gercekten ulasilip ulasilamadiginin olcusu. Hata SAYISI bu is
        # icin kullanilamaz: 540 parcanin icinde kalici bozuk tek bir kayit her
        # turu "basarisiz" gosterip surekli yanlis alarm uretirdi.
        answered = 0
        errors: list[str] = []

        # Kanal: kayitli deger guvenilmezse takip NUMARASINDAN cikarilir
        # (14 hane / "38120177" = NL, kalan = IE; FEDEX daima korunur). NL kendi
        # toplu ucune, IE yapilandirilmissa T&T v1'in toplu ucune; FEDEX ve
        # digerleri parca parca `single` doner.
        use_glsg = config.glsg_tt_configured()
        nl: list[dict] = []
        glsg: list[dict] = []
        single: list[dict] = []
        for p in parcels:
            channel = providers.channel_for(p["tracking_no"], p["channel"])
            if channel == providers.CHANNEL_NL:
                nl.append(p)
            elif channel == providers.CHANNEL_IE and use_glsg:
                glsg.append(p)
            else:
                single.append(p)

        for parcel, info in self._nl_results(nl, errors):
            answered += 1
            try:
                updated += self._apply(parcel, info)
            except Exception as exc:
                errors.append(f"{parcel['tracking_no']}: {exc}")

        for parcel, info in self._glsg_results(glsg, errors):
            answered += 1
            try:
                updated += self._apply(parcel, info)
            except Exception as exc:
                errors.append(f"{parcel['tracking_no']}: {exc}")

        for p in single:
            tn = p["tracking_no"]
            try:
                info = self._fetch(providers.channel_for(tn, p["channel"]), tn)
                if info is None:
                    # Yapilandirilmis saglayici yok. Isaretlenmezse `last_checked_at`
                    # NULL kalir ve bu parcalar her turda siranin basini isgal edip
                    # sorgulanabilir kanallari ac birakir.
                    self.db.mark_checked(tn)
                    continue
                answered += 1
                updated += self._apply(p, info)
            except ShipITError as api_err:
                detail = str(api_err)
                if api_err.status in (404, 490):
                    # ShipIT'te parca gun sonu raporu calismadan takipte gorunmez.
                    detail += " (gun sonu raporu / endofday henuz calismamis olabilir)"
                errors.append(f"{tn}: {detail}")
                self.db.mark_checked(tn)
            except API_ERRORS as api_err:
                errors.append(f"{tn}: {api_err}")
                self.db.mark_checked(tn)
            except Exception as exc:
                errors.append(f"{tn}: {exc}")
                self.db.mark_checked(tn)

        # Statu taramasindan SONRA: bu turda teslim edilenler de kuyruga girsin.
        pods = self._archive_pods()
        self._push_to_erp()

        self.last_run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.last_updated_count = updated
        self.last_pod_count = pods
        self.last_error = "; ".join(errors[:3]) if errors else None

        # Sorulacak parca yoksa tur basarilidir: yapacak is olmamasi ariza degil.
        self.db.record_tick(ok=answered > 0 or not parcels,
                            error=self.last_error or "")
        # `record_tick` ustteki tek satiri BIR SONRAKI basarili turda siler —
        # bu turdaki hata (bir GLS grubu basarisiz oldu ama digerleri gitti,
        # tur yine de "basarili" sayildi) kalici gecmise AYRICA yazilir; yoksa
        # 15 dakika sonra iz kalmaz (bkz. tracking/db.py:record_tick_error).
        for err in errors:
            self.db.record_tick_error(err)
        log.info("tick: %d parcels checked, %d updated, %d POD archived",
                 len(parcels), updated, pods)
