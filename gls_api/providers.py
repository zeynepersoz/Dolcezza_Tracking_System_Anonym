# -*- coding: utf-8 -*-
"""
Sağlayıcı seçimi — GLS tek bir "API" vermez.

Müşteriden müşteriye hangi kanalın açıldığı değişir, bu yüzden .env'de hangisi
doluysa o kullanılır. Yetenek tablosu:

    Sağlayıcı        Etiket   Takip   POD
    ShipIT REST        ✓        ✓      ✓ (PDF)
    Track&Trace SOAP   —        ✓      ✓ (PDF/görüntü)
    GLS Netherlands    ✓        ✓      ✓ (BMP — teslimat fotoğrafı)

GLS Netherlands'ta üçü de AYRI host'tur: etiket api.gls.nl/v1/api, takip
api.gls.nl/tt/V1, POD apm.gls.nl. `get_nl()` etiket, `get_nl_tt()` takip+POD
içindir. Yönlendirme kanala göre değiştiği için `tracking_provider_for` ile
`pod_provider_for` ayrı ayrı sorulur.

`doctor()` her sağlayıcıya gerçek bir çağrı yapıp Türkçe teşhis döner —
kimlik bilgileri .env'e girildiğinde neyin çalıştığını satır satır söyler.
"""
from __future__ import annotations

import i18n

from . import config
from .fedex_client import FedExClient, FedExError
from .nl_client import GLSNLClient, GLSNLError
from .nl_track_client import GLSNLTrackClient
from .shipit_client import ShipITClient, ShipITError
from .tt_soap_client import TrackTraceClient, TrackTraceError
from .tt_v1_client import GLSGroupError, TrackTraceV1Client

# Kanal adlari (tracking/db.py:parcels.channel ile ayni)
CHANNEL_IE = "IE"
CHANNEL_NL = "NL"
CHANNEL_FEDEX = "FEDEX"

# NL parca numaralari 14 hane ve "38120177" (GLS NL musteri no) ile baslar;
# kalan her seri (or. "2156...") eski ShipIT / Irlanda hub hesabindandir = IE.
# `erp/sync.py` de ayni oneki kullanir.
_NL_TRACKING_PREFIX = "38120177"


def channel_for(tracking_no: str, stored: str = "") -> str:
    """Parcanin kanali: kayitli deger biliniyorsa o, degilse NUMARADAN cikarim.

    ERP senkronu ve Excel iceri-aktarimi kanali zaten yaziyor; bu, kanali bos
    ya da bilinmeyen kalmis eski/elle satirlar icin son emniyet — takip
    yonlendirmesi sessizce yanlis saglayiciya gitmesin. `FEDEX` gibi bilinen
    bir kanal DAIMA korunur (takip numarasi 11 haneli olsa bile IE'ye kaymasin).
    """
    s = (stored or "").strip().upper()
    if s in (CHANNEL_NL, CHANNEL_IE, CHANNEL_FEDEX):
        return s
    tn = "".join(ch for ch in str(tracking_no or "") if ch.isdigit())
    if tn.startswith(_NL_TRACKING_PREFIX) or len(tn) >= 13:
        return CHANNEL_NL
    return CHANNEL_IE

_shipit: ShipITClient | None = None
_tt: TrackTraceClient | None = None
_nl: GLSNLClient | None = None
_nl_tt: GLSNLTrackClient | None = None
_fedex: FedExClient | None = None
_glsg_tt: TrackTraceV1Client | None = None


def get_shipit() -> ShipITClient:
    global _shipit
    if _shipit is None:
        _shipit = ShipITClient()
    return _shipit


def get_tt() -> TrackTraceClient:
    global _tt
    if _tt is None:
        _tt = TrackTraceClient()
    return _tt


def get_nl() -> GLSNLClient:
    global _nl
    if _nl is None:
        _nl = GLSNLClient()
    return _nl


def get_nl_tt() -> GLSNLTrackClient:
    global _nl_tt
    if _nl_tt is None:
        _nl_tt = GLSNLTrackClient()
    return _nl_tt


def get_fedex() -> FedExClient:
    global _fedex
    if _fedex is None:
        _fedex = FedExClient()
    return _fedex


def get_glsg_tt() -> TrackTraceV1Client:
    global _glsg_tt
    if _glsg_tt is None:
        _glsg_tt = TrackTraceV1Client()
    return _glsg_tt


def reset_clients() -> None:
    """Onbellege alinmis istemcileri dusurur — kimlik degistiginde ZORUNLU.

    Istemciler taban URL ve kimligi `__init__` icinde `config`'ten okur ve burada
    modul globalinde tutulur. Panelden yeni bir sifre kaydedildiginde bu cagrilmazsa
    uygulama eski kimlikle calismaya devam eder ve kullanici "kaydettim ama
    degismedi" der.
    """
    global _shipit, _tt, _nl, _nl_tt, _fedex, _glsg_tt
    _shipit = _tt = _nl = _nl_tt = _fedex = _glsg_tt = None


# ---------------------------------------------------------------------------
# Yetenek yonlendirmesi
# ---------------------------------------------------------------------------
def tracking_provider_for(channel: str) -> tuple[str, object] | None:
    """(ad, istemci) — takip sorgulanabilecek sağlayıcı; yoksa None.

    NL kanalı kendi Track&Trace ucuna gider (`nl_tt`); ShipIT/TT'ye DÜŞMEZ,
    çünkü NL parça numaraları yalnızca NL hesabında tanımlıdır — başka bir
    sağlayıcıya sorulursa "bulunamadı" döner ve sessizce yanlış olur.

    NL dışı (IE) kanalda öncelik GLS Track & Trace v1'dedir (OAuth 2.0, 10'arlı
    toplu sorgu, canlı doğrulandı): parça başı tek istek atan ShipIT/T&T SOAP'a
    tercih edilir. POD orada YOKTUR — `pod_provider_for` bunu döndürmez.
    """
    if channel == CHANNEL_NL:
        if config.nl_tt_configured():
            return ("nl_tt", get_nl_tt())
        return None
    if channel == CHANNEL_FEDEX:
        if config.fedex_configured():
            return ("fedex", get_fedex())
        return None
    if config.glsg_tt_configured():
        return ("glsg_tt", get_glsg_tt())
    if config.shipit_configured():
        return ("shipit", get_shipit())
    if config.tt_configured():
        return ("tt", get_tt())
    return None


def pod_provider_for(channel: str) -> tuple[str, object] | None:
    """(ad, istemci) — POD görüntüsü çekilebilecek sağlayıcı; yoksa None.

    Takipten AYRI sorulur: NL kanalında POD üçüncü bir host'tan gelir ve
    takip yanıtındaki `uniqueNo`/`jobDate` ile anahtarlanır — bu yüzden aynı
    istemci ama farklı uç nokta (bkz. GLSNLTrackClient.parcel_pod).
    """
    if channel == CHANNEL_NL:
        if config.nl_tt_configured():
            return ("nl_tt", get_nl_tt())
        return None
    if channel == CHANNEL_FEDEX:
        # FedEx istemcisinde POD ucu yok (yalnizca Track API — bkz.
        # fedex_client.py modul basligi). ShipIT/TT'ye SESSIZCE dusmesin;
        # bir FedEx kolisi icin yanlis saglayicidan POD sorulmus olurdu.
        return None
    if config.shipit_configured():
        return ("shipit", get_shipit())
    if config.tt_configured():
        return ("tt", get_tt())
    return None


def label_provider_for(channel: str) -> tuple[str, object] | None:
    """(ad, istemci) — etiket üretebilecek sağlayıcı; yoksa None.

    Yapılandırılmamış kanal için *başka* bir sağlayıcıya düşer. Tek etiket
    akışında (kullanıcı sonucu gözüyle görür) kabul edilebilir; toplu üretimde
    değil — orada `strict_provider_for` kullanılır.
    """
    if channel == CHANNEL_FEDEX:
        # Bu sistemden FedEx etiketi BASILMAZ (kullanici karari — FedEx
        # tarafi kendi Ship Manager'inda olusturuluyor, biz yalnizca takip
        # ediyoruz). ShipIT/NL'e sessizce dusmesin.
        return None
    if channel == CHANNEL_NL and config.nl_configured():
        return ("nl", get_nl())
    if config.shipit_configured():
        return ("shipit", get_shipit())
    if config.nl_configured():
        return ("nl", get_nl())
    return None


class ChannelUnavailable(RuntimeError):
    """İstenen kanal yapılandırılmamış."""


def strict_provider_for(channel: str) -> tuple[str, object]:
    """Etiket sağlayıcısı — YEDEKLEME YOK, istenen kanal yoksa hata verir.

    Toplu üretim için: `label_provider_for` ShipIT yapılandırılmamışsa bir IE
    isteğini sessizce NL'e düşürür. Tek etikette fark edilir; 200 İrlanda
    gönderisi için Hollanda etiketi basmak felakettir.
    """
    channel = (channel or "").strip().upper()
    if channel == CHANNEL_NL:
        if config.nl_configured():
            return ("nl", get_nl())
        raise ChannelUnavailable(
            "NL kanalı yapılandırılmamış — .env'de NL_USERNAME/NL_PASSWORD gerekli")
    if channel == CHANNEL_IE:
        if config.shipit_configured():
            return ("shipit", get_shipit())
        raise ChannelUnavailable(
            "IE kanalı yapılandırılmamış — .env'de SHIPIT_* alanları gerekli")
    raise ChannelUnavailable(f"Bilinmeyen kanal: '{channel}' (NL veya IE olmalı)")


# ---------------------------------------------------------------------------
# Canli baglanti testi
# ---------------------------------------------------------------------------
def _check_shipit() -> tuple[bool, str]:
    """Yan etkisiz `allowedservices` çağrısı — parça oluşturmaz."""
    address = {"Name1": "Test", "Street1": "Test 1", "ZipCode": "1000",
               "City": "Test", "CountryCode": "NL"}
    try:
        get_shipit().allowed_services(address, address)
        return True, i18n.t("check.ok")
    except ShipITError as e:
        if e.status == 401:
            return False, i18n.t("check.shipit_auth")
        if e.status == 490:
            # Istek ulasti, kimlik gecti, sadece test adresi is kuralina takildi.
            return True, i18n.t("check.ok_test_address")
        return False, str(e)


def _check_tt() -> tuple[bool, str]:
    """Var olmayan bir referansla `GetTuDetail` — 998 (veri yok) = kimlik geçti."""
    try:
        get_tt().get_tu_detail("00000000000")
        return True, i18n.t("check.ok")
    except TrackTraceError as e:
        if e.is_no_data:
            return True, i18n.t("check.ok_test_ref")
        if e.is_auth_error:
            return False, i18n.t("check.tt_auth")
        return False, str(e)


def _check_nl() -> tuple[bool, str]:
    """`ValidateLogin` — yan etkisiz, ayrıca müşteri numarasını da doğrular."""
    try:
        data = get_nl().validate_login()
    except GLSNLError as e:
        if e.is_auth_error:
            return False, i18n.t("check.nl_auth")
        return False, str(e)

    cust_nos = [n
                for customer in data.get("customers") or []
                for subject in customer.get("subjects") or []
                for n in subject.get("custNos") or []]
    if not cust_nos:
        return True, i18n.t("check.nl_no_customer")
    return True, i18n.t("check.nl_ok", nos=", ".join(cust_nos[:5]))


def _check_nl_tt() -> tuple[bool, str]:
    """Var olmayan bir numarayla `details` — bos liste = uc nokta ve kimlik calisiyor."""
    try:
        get_nl_tt().parcel_details(["00000000000000"])
        return True, i18n.t("check.ok")
    except GLSNLError as e:
        if e.is_auth_error:
            return False, i18n.t("check.nl_auth")
        return False, str(e)


def _check_fedex() -> tuple[bool, str]:
    """OAuth token istegi — gercek bir takip numarasi harcamadan kimlik dogrulanir."""
    try:
        get_fedex().check_auth()
        return True, i18n.t("check.ok")
    except FedExError as e:
        if e.is_auth_error:
            return False, i18n.t("check.fedex_auth")
        return False, str(e)


def _check_glsg_tt() -> tuple[bool, str]:
    """`events/codes` — yan etkisiz. HTTP 200 = token alindi + T&T v1 entitlement var.

    401/403: token gecerli ama bu uc App ID'ye acik degil (ya da App kimligi
    hatali) — ShipIT-Farm'in canli ortamda dondugu hatanin aynisi.
    """
    try:
        codes = get_glsg_tt().event_codes()
    except GLSGroupError as e:
        if e.is_auth_error:
            return False, i18n.t("check.glsg_auth")
        return False, str(e)
    return True, i18n.t("check.glsg_ok", count=len(codes))


_PROVIDERS = [
    ("shipit", "GLS ShipIT REST (etiket + takip + POD)",
     config.shipit_configured, _check_shipit),
    ("tt", "GLS Track&Trace SOAP (takip + POD)",
     config.tt_configured, _check_tt),
    ("nl", "GLS Netherlands REST (etiket)",
     config.nl_configured, _check_nl),
    ("nl_tt", "GLS Netherlands Track&Trace (takip — POD yok)",
     config.nl_tt_configured, _check_nl_tt),
    ("fedex", "FedEx Track API (takip — etiket/POD yok)",
     config.fedex_configured, _check_fedex),
    ("glsg_tt", "GLS Track & Trace v1 (OAuth 2.0 — takip, POD yok)",
     config.glsg_tt_configured, _check_glsg_tt),
]


def doctor() -> list[dict]:
    """Her sağlayıcı için {name, title, configured, ok, detail}.

    `configured` False ise ağa çıkılmaz (ok=None).
    """
    results = []
    for name, title, is_configured, check in _PROVIDERS:
        if not is_configured():
            results.append({"name": name, "title": title, "configured": False,
                            "ok": None, "detail": i18n.t("check.not_configured")})
            continue
        ok, detail = check()
        results.append({"name": name, "title": title, "configured": True,
                        "ok": ok, "detail": detail})
    return results
