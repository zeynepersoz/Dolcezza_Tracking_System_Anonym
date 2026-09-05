# -*- coding: utf-8 -*-
"""
MOCK GLS SUNUCUSU — üç gerçek kanalın *doğrulanmış* şemasını taklit eder.

Kimlik bilgisi olmadan geliştirme + test yapmayı sağlar. GLS'ten gerçek
kimlikler gelince .env'de `GLS_MODE=test` yapmak yeterli; istemci kodu DEĞİŞMEZ.

Taklit edilen kanallar:
    /shipit/rs/...            ShipIT REST      (etiket + takip + POD/PDF)
    /tt/services/Tracking     Track&Trace SOAP (takip + POD)
    /nl/...                   GLS Netherlands  (etiket)
    /nltt/...                 GLS NL Track&Trace REST (takip)
    /nlpod/...                GLS NL POD ucu   (teslimat fotoğrafı, BMP)

ÖNEMLİ — burada bilinçli olarak taklit edilen gerçek tuhaflıklar:
  * ShipIT hataları GÖVDESİZ döner; metin `message`/`error`/`args` başlıklarındadır
  * ShipIT iş kuralı reddi için standart dışı HTTP 490 kullanır
  * ShipIT'te bir parça `endofday` çalışmadan takipte GÖRÜNMEZ
  * GLS NL'de API anahtarı YOKTUR; kimlik JSON gövdesindedir
  * GLS NL'de etiket ve takip AYRI servistir; takipte alan adı `userName`
  * GLS NL takip ucu bilinmeyen numarayı hata vermeden yanıttan düşürür
  * GLS NL POD ucu kimlik SORMAZ; anahtar `uniqueNo` + `jobDate` çiftidir
  * GLS NL POD ucu `jobDate` eksikse hata değil 204 No Content döner
  * GLS NL POD içeriği "image/png" der ama BMP'dir ve 128 KB'a sıfırla doldurulur
  * Track&Trace şemasında `<Minut>` yazım hatası vardır

Çalıştırma:
    uvicorn gls_api.mock_server:app --port 8788
"""
from __future__ import annotations

import base64
import hashlib
import random
import struct
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="GLS Mock API (ShipIT + Track&Trace + NL)", version="1.0")

# Gecerli kabul edilecek ornek parca numaralari (SP26 formatinda 11 hane, NL 14 hane)
KNOWN_PARCELS = {"21569761233", "21569761234", "21569761246", "35000001406746"}

# ShipIT'te takipte gorunmek icin gun sonu raporu gerekir; olusturulan ama
# heniz endofday'e girmemis parcalar burada bekler (gercek davranisin taklidi).
PENDING_PARCELS: set[str] = set()

def _mini_pdf(*texts: str) -> bytes:
    """Her metin icin bir sayfa tasiyan, gecerli bir PDF uretir.

    Metin degiskeninin uzunlugu obje ofsetlerini kaydirdigi icin xref tablosu
    burada hesaplanir. Onceden hazirlanmis sabit bir govdeye replace() yapmak
    ofsetleri bozar ve gercek bir PDF okuyucusu (pypdf) dosyayi acamaz —
    toplu etiket akisi bu PDF'leri birlestirdigi icin bu onemli.

    Cok sayfa gerekiyor cunku GLS NL `Label/Create` bir sevkiyatin TUM kolilerini
    tek cok sayfali PDF'te doner (portalden inen TSOEL1N.pdf: 2 koli, 2 sayfa).
    """
    # obj1 katalog, obj2 sayfa agaci, obj3 font; sonra sayfa/icerik ciftleri.
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(texts)))
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[%s]/Count %d>>" % (kids.encode(), len(texts)),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    for i, text in enumerate(texts):
        safe = "".join(c for c in text if c.isalnum() or c in " -_.")[:40]
        stream = f"BT /F1 18 Tf 20 40 Td ({safe}) Tj ET\n".encode("ascii", "replace")
        objects.append(
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]"
            b"/Contents %d 0 R/Resources<</Font<</F1 3 0 R>>>>>>" % (5 + 2 * i))
        objects.append(b"<</Length %d>>stream\n%sendstream" % (len(stream), stream))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + body + b"endobj\n"

    startxref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (
        len(objects) + 1, startxref)
    return bytes(out)


def _pod_pdf(track_id: str) -> bytes:
    return _mini_pdf(f"GLS MOCK POD - {track_id}")


def _new_track_id() -> str:
    """Rastgele 11 haneli ShipIT/IE parca no (gercek portalda gozlenen bicim)."""
    return str(random.randint(20000000000, 29999999999))


def _new_track_id_nl() -> str:
    """Rastgele 14 haneli NL parca no."""
    return str(random.randint(30000000000000, 39999999999999))


def _mini_label_pdf(*texts: str) -> bytes:
    return _mini_pdf(*(f"GLS LABEL - {t}" for t in texts))


def _delivery_time(track_id: str) -> datetime:
    """Takip no'dan deterministik teslim zamani — ayni parca hep ayni sonucu versin."""
    offset = int(hashlib.sha256(track_id.encode()).hexdigest()[:4], 16) % 20 + 1
    return (datetime.now() - timedelta(days=offset)).replace(minute=30, second=0, microsecond=0)


# Gercek GLS portallarinda gozlenen olay metinleri ve depo kodlari.
_DEPOTS = ["Amsterdam NL", "Budapest HU0010", "Pomáz HU0053", "Cork IE0002", "Frankenberg DE"]
_EVENTS = [
    ("1", "The parcel data was entered into the GLS IT system; the parcel was not yet handed over to GLS.", 4),
    ("2", "The parcel was handed over to GLS.", 4),
    ("3", "The parcel has left the parcel center.", 3),
    ("4", "The parcel has reached the parcel center.", 1),
    ("5", "The parcel is expected to be delivered during the day.", 1),
    ("6", "The parcel has been delivered.", 0),
]


def _events_for(track_id: str) -> list[tuple[str, str, datetime, str]]:
    """(kod, aciklama, zaman, depo) — deterministik."""
    delivered = _delivery_time(track_id)
    rnd = random.Random(track_id)
    depot = rnd.choice(_DEPOTS)
    return [(code, desc, delivered - timedelta(days=days), depot)
            for code, desc, days in _EVENTS]


# ============================================================
# A) ShipIT REST
# ============================================================
def _shipit_error(status: int, message: str, code: str = "", args: str = "") -> Response:
    """ShipIT hatalari GOVDESIZ doner; metin HTTP basliklarindadir."""
    headers = {"message": message}
    if code:
        headers["error"] = code
    if args:
        headers["args"] = args
    return Response(status_code=status, headers=headers)


def _known(track_id: str) -> bool:
    return track_id in KNOWN_PARCELS and track_id not in PENDING_PARCELS


@app.post("/shipit/rs/tracking/parceldetails")
async def shipit_details(request: Request):
    body = await request.json()
    track_id = str(body.get("TrackID", ""))
    if track_id in PENDING_PARCELS:
        return _shipit_error(490, "Parcel not yet in tracking system.",
                             code="TRACKING.NOT_AVAILABLE", args=track_id)
    if not _known(track_id):
        return _shipit_error(490, f"Invalid field TrackID. Value {track_id} is not a valid value.",
                             code="VALIDATION.INVALID_FIELD", args=f"TrackID,{track_id}")

    events = _events_for(track_id)
    delivered = events[-1][2]
    return {
        "UnitDetail": {
            "TrackID": track_id,
            "Product": "PARCEL",
            "Weight": 3.0,
            "SignatureName": "J. DOE",
            "DeliveryDate": delivered.strftime("%Y-%m-%d"),
            "History": [{
                "Date": when.strftime("%Y-%m-%d"),
                "Time": when.strftime("%H:%M:%S"),
                "EvtDscr": desc,
                "EvtCode": code,
                "Address": {"City": depot, "CountryCode": "IE"},
            } for code, desc, when, depot in events],
        }
    }


@app.post("/shipit/rs/tracking/parcelpod")
async def shipit_pod(request: Request):
    body = await request.json()
    track_id = str(body.get("TrackID", ""))
    if not _known(track_id):
        return _shipit_error(490,
                             f"POD cannot be generated for {track_id}, the parcel is in the wrong state.",
                             code="POD.WRONG_STATE", args=track_id)
    return {"PODItem": {"TrackID": track_id,
                        "ImageData": base64.b64encode(_pod_pdf(track_id)).decode()}}


@app.post("/shipit/rs/tracking/parcels")
async def shipit_find_parcels(request: Request):
    """Tarih araliginda parca arar. Sonuc yoksa GLS BOS GOVDE doner (JSON degil)."""
    await request.json()
    visible = sorted(KNOWN_PARCELS - PENDING_PARCELS)
    if not visible:
        return Response(status_code=200)
    return {"Parcels": [{"TrackID": t, "Status": "DELIVERED"} for t in visible]}


@app.post("/shipit/rs/shipments")
async def shipit_shipments(request: Request):
    body = await request.json()
    shipment = body.get("Shipment") or body
    # SEMA (shipit-farm.yaml): `PrintingOptions` `ShipmentRequestData`nin
    # alanidir — `Shipment`in ICINDE degil, KARDESI. Mock onceden iceride
    # ariyordu, yani bizim HATALI yukumuzu dogruluyordu; gercek API bu haliyle
    # reddederdi. Iki yeri de kabul etmez, semadaki yeri esas alir.
    if not body.get("PrintingOptions"):
        return _shipit_error(490, "PrintingOptions is mandatory.",
                             code="VALIDATION.MISSING_FIELD", args="PrintingOptions")

    parcels, prints = [], []
    for _ in shipment.get("ShipmentUnit") or [{}]:
        tid = _new_track_id()
        KNOWN_PARCELS.add(tid)
        PENDING_PARCELS.add(tid)  # endofday calisana kadar takipte gorunmez
        parcels.append({"TrackID": tid, "ParcelNumber": tid,
                        "Location": {"CountryCode": "IE"}})
        prints.append({"LabelFormat": "PDF",
                       "Data": base64.b64encode(_mini_label_pdf(tid)).decode()})
    return {"CreatedShipment": {"ConsignmentId": _new_track_id(),
                                "ParcelData": parcels, "PrintData": prints}}


@app.post("/shipit/rs/shipments/allowedservices")
async def shipit_allowed_services(request: Request):
    await request.json()
    return {"AllowedServices": [{"ProductName": "PARCEL"}, {"ProductName": "EXPRESS"}]}


@app.post("/shipit/rs/shipments/cancel/{parcel_number}")
async def shipit_cancel(parcel_number: str):
    if parcel_number not in PENDING_PARCELS:
        return _shipit_error(490, "Parcel already in end-of-day report, cannot be cancelled.",
                             code="CANCEL.TOO_LATE", args=parcel_number)
    PENDING_PARCELS.discard(parcel_number)
    KNOWN_PARCELS.discard(parcel_number)
    return {"CancellationStatus": "CANCELLED", "ParcelNumber": parcel_number}


@app.post("/shipit/rs/shipments/endofday")
async def shipit_endofday():
    released = sorted(PENDING_PARCELS)
    PENDING_PARCELS.clear()
    return {"EndOfDay": {"ParcelCount": len(released), "Parcels": released}}


# ============================================================
# B) Track & Trace SOAP
# ============================================================
NS = "http://gls-group.eu/Tracking/"


def _soap(inner: str) -> Response:
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
           f'<soap:Body>{inner}</soap:Body></soap:Envelope>')
    return Response(xml, media_type="text/xml; charset=utf-8")


def _exit_code(code: int, description: str = "") -> str:
    return (f'<ExitCode xmlns="{NS}"><ErrorCode>{code}</ErrorCode>'
            f"<ErrorDscr>{escape(description)}</ErrorDscr></ExitCode>")


def _soap_datetime(when: datetime) -> str:
    # `Minut` GLS'in semasindaki yazim hatasi — bilincli olarak korunuyor.
    return (f"<Year>{when.year}</Year><Month>{when.month}</Month><Day>{when.day}</Day>"
            f"<Hour>{when.hour}</Hour><Minut>{when.minute}</Minut>")


def _soap_text(xml: str, tag: str) -> str:
    """Ad-alanindan bagimsiz basit deger cikarici (mock icin yeterli)."""
    for candidate in (f"<{tag}>", f":{tag}>"):
        start = xml.find(candidate)
        if start == -1:
            continue
        start += len(candidate)
        end = xml.find("<", start)
        return xml[start:end].strip()
    return ""


@app.post("/tt/services/Tracking")
async def tt_tracking(request: Request):
    xml = (await request.body()).decode("utf-8", "replace")
    reference = _soap_text(xml, "RefValue")[:11]

    if not _soap_text(xml, "UserName") or not _soap_text(xml, "Password"):
        return _soap(f'<TuDetailResponse xmlns="{NS}">{_exit_code(502, "Authentication failed")}'
                     "</TuDetailResponse>")

    if "GetTuPOD" in xml:
        if not _known(reference):
            return _soap(f'<TuPODResponse xmlns="{NS}">{_exit_code(999, "Image not found")}'
                         "</TuPODResponse>")
        data = base64.b64encode(_pod_pdf(reference)).decode()
        return _soap(f'<TuPODResponse xmlns="{NS}">{_exit_code(0)}'
                     f"<PODFile>{data}</PODFile>"
                     f"<PODFileName>{reference}.pdf</PODFileName></TuPODResponse>")

    if "GetTuList" in xml:
        rows = "".join(
            f"<TUList><RefNo>{t}</RefNo>"
            f"<InitialDateTime>{_soap_datetime(_delivery_time(t))}</InitialDateTime>"
            f"<EvtCodeNo>6</EvtCodeNo><CountryCode>IE</CountryCode>"
            f"<City>Dublin</City><ConsigneeName>J. DOE</ConsigneeName>"
            f"<CurrentStatus>DELIVERED</CurrentStatus></TUList>"
            for t in sorted(KNOWN_PARCELS - PENDING_PARCELS))
        return _soap(f'<TuListResponse xmlns="{NS}">{_exit_code(0)}{rows}</TuListResponse>')

    # GetTuDetail (varsayilan)
    if not _known(reference):
        return _soap(f'<TuDetailResponse xmlns="{NS}">{_exit_code(998, "No data found")}'
                     "</TuDetailResponse>")

    events = _events_for(reference)
    history = "".join(
        f"<History><Date>{_soap_datetime(when)}</Date><Code>{code}</Code>"
        f"<Desc>{escape(desc)}</Desc><LocationName>{escape(depot)}</LocationName>"
        f"<CountryName>Ireland</CountryName></History>"
        for code, desc, when, depot in events)
    return _soap(
        f'<TuDetailResponse xmlns="{NS}">{_exit_code(0)}'
        f"<TuNo>{reference}</TuNo><Product>PARCEL</Product><TuWeight>3.0</TuWeight>"
        f"<Signature>J. DOE</Signature>"
        f"<DeliveryDateTime>{_soap_datetime(events[-1][2])}</DeliveryDateTime>"
        f"<ConsigneeAddress><Name1>J. DOE</Name1><City>Dublin</City></ConsigneeAddress>"
        f"{history}</TuDetailResponse>")


# ============================================================
# C) GLS Netherlands REST  (API anahtari YOK — kimlik govdede)
# ============================================================
def _nl_error(status: int, message: str, errors: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "error": True, "status": str(status), "message": message, "errors": errors or {}})


async def _nl_auth(request: Request) -> tuple[dict, JSONResponse | None]:
    body = await request.json()
    if not body.get("username") or not body.get("password"):
        return body, _nl_error(401, "Invalid credentials")
    return body, None


@app.post("/nl/Authentication/ValidateLogin")
async def nl_validate_login(request: Request):
    _, error = await _nl_auth(request)
    if error:
        return error
    return {"error": False, "status": "200",
            "customers": [{"apiCustomerNo": "MOCK-API-1",
                           "subjects": [{"subjectName": "Mock Subject",
                                         "custNos": ["1234567"]}]}]}


@app.post("/nl/Label/Create")
async def nl_label_create(request: Request):
    body, error = await _nl_auth(request)
    if error:
        return error
    addresses = body.get("addresses") or {}
    if not addresses.get("deliveryAddress"):
        return _nl_error(400, "Validation failed",
                         {"addresses.deliveryAddress": ["This field is required."]})

    units = []
    for unit in body.get("units") or [{}]:
        unit_no = _new_track_id_nl()
        KNOWN_PARCELS.add(unit_no)
        units.append({
            "unitId": unit.get("unitId") or unit_no,
            "unitNo": unit_no,
            "uniqueNo": f"NL{unit_no}",
            "label": base64.b64encode(_mini_label_pdf(unit_no)).decode(),
            "routingData": {"finalCountry": "NL", "finalLocation": "Amsterdam"},
        })
    return {
        "error": False, "status": "200",
        # Gercek API'de ust seviyede birlesik bir `labels` alani YOK (2026-08-14
        # canli dogrulandi) — her koli kendi `label`'ini `units[i]` icinde
        # tasir, birlestirme cagiran tarafin isidir (bkz. dispatch/engine.py
        # `_write_pdf`). Eskiden burada uydurma bir `labels` alani donuluyordu,
        # bu da gercek API'de patlayan bir varsayimi testlerde gizliyordu.
        "units": units,
        "shipmentTrackingLink": f"https://gls-group.eu/track/{units[0]['unitNo']}",
    }


@app.post("/nl/Label/Confirm")
async def nl_label_confirm(request: Request):
    body, error = await _nl_auth(request)
    if error:
        return error
    if not body.get("unitNo"):
        return _nl_error(400, "Validation failed", {"unitNo": ["This field is required."]})
    return {"error": False, "status": "200", "message": "Confirmed"}


@app.post("/nl/Label/Delete")
async def nl_label_delete(request: Request):
    body, error = await _nl_auth(request)
    if error:
        return error
    if not body.get("unitNo"):
        return _nl_error(400, "Validation failed", {"unitNo": ["This field is required."]})
    # Gercek API'de koli Track & Trace'te KALIR, yalnizca surecleri durur — bu
    # yuzden mock da KNOWN_PARCELS'tan cikarmaz. Cikarsaydi testler "silinen koli
    # takipten kaybolur" gibi yanlis bir varsayimi dogrulamis olurdu.
    return {"error": False, "status": "200", "message": "Deleted"}


@app.post("/nl/ParcelShop/GetParcelShops")
async def nl_parcel_shops(request: Request):
    body, error = await _nl_auth(request)
    if error:
        return error
    amount = min(int(body.get("amountOfShops") or 3), 10)
    return {"error": False, "status": "200", "parcelShops": [
        {"parcelShopId": f"NL-{i:04d}", "name": f"Mock ParcelShop {i}",
         "street": "Damrak", "houseNo": str(i), "zipcode": body.get("zipCode") or "1012AB",
         "city": "Amsterdam", "countryCode": body.get("countryCode") or "NL",
         "distanceMeters": i * 250}
        for i in range(1, amount + 1)]}


# ============================================================
# C1) GLS NL Track & Trace REST  (etiket ucundan AYRI servis)
# ============================================================
# Iki gercek tuhaflik burada bilerek taklit ediliyor:
#   * kimlik alani `userName` (etiket ucunda `username`)
#   * bilinmeyen numara hata degil — yanittan sessizce dusuruluyor

# Gercek yanitta gozlenen `state` degerleri (olay kodundan turetiliyor).
_NL_TT_STATE_BY_CODE = {"6": "Delivered", "5": "InRegion", "1": "Announced"}


@app.post("/nltt/api/parcel/v1/details")
async def nl_tt_details(request: Request):
    body = await request.json()
    if not body.get("userName") or not body.get("password"):
        return _nl_error(401, "Invalid credentials")

    parcels = []
    for number in [str(n) for n in (body.get("parcelNumbers") or [])][:20]:
        if not _known(number):
            continue
        history = _events_for(number)
        events = [{
            "eventId": f"MOCK{code}{number[-4:]}",
            "eventNo": int(code),
            "descriptionEN": desc,
            "descriptionNL": desc,
            "country": "NL",
            "countryName": "Nederland",
            "depotName": depot,
            "date": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            # Teslimatta alicinin adi. Gercek API bunu CIFT KODLANMIS UTF-8
            # olarak veriyor; bkz. tracking/scheduler.py:_fix_mojibake.
            "details": "LÃ©onie" if code == "6" else "",
        } for code, desc, when, depot in history]

        parcels.append({
            "parcelNo": number,
            "custNo": "1234567",
            # uniqueNo + jobDate: POD ucunun anahtari (bkz. /nlpod/...).
            # jobDate burada UTC'dir; POD ucu Hollanda YEREL gece yarisini
            # bekler — gercek API'deki bu fark bilerek taklit ediliyor.
            "uniqueNo": _unique_no(number),
            "jobDate": (_job_date(number) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "state": _NL_TT_STATE_BY_CODE[history[-1][0]],
            "subState": "None",
            "product": "EuroBusinessParcel",
            "customerReference": f"REF-{number[-6:]}",
            "suppliedWeight": 3.0,
            "events": events,
            "uri": f"https://www.gls-info.nl/track-and-trace?parcelno={number}",
        })
    return {"parcels": parcels}


# ============================================================
# C1b) GLS NL POD ucu — apm.gls.nl taklidi
# ============================================================
def _unique_no(number: str) -> str:
    """Gercek API'deki 8 harflik `uniqueNo`; mock'ta numaradan turetilir."""
    seed = int(hashlib.sha1(number.encode()).hexdigest(), 16)
    return "".join("ABCDEFGHIJKLMNOPQRSTUVWXYZ"[(seed >> (i * 5)) % 26] for i in range(8))


def _job_date(number: str) -> datetime:
    """Parcanin is gunu — Hollanda yerel gece yarisi (POD ucunun bekledigi deger)."""
    first_event = _events_for(number)[0][2]
    return datetime(first_event.year, first_event.month, first_event.day)


def _mini_bmp(width: int = 200, height: int = 100) -> bytes:
    """Duz renkli, gecerli 32-bit BMP. Gercek POD ile ayni boyutta (80054 bayt)."""
    pixels = bytes([160, 190, 210, 255]) * (width * height)   # BGRA
    dib = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, len(pixels),
                      3780, 3780, 0, 0)
    return b"BM" + struct.pack("<IHHI", 14 + len(dib) + len(pixels), 0, 0,
                               14 + len(dib)) + dib + pixels


@app.post("/nlpod/api/tracktrace/v1/{parcel_no}/pod")
async def nl_pod(parcel_no: str, request: Request):
    body = await request.json()
    # Kimlik SORULMAZ; tek zorunlu alan uniqueNo.
    if not body.get("uniqueNo"):
        return JSONResponse({"status": 400, "title": "One or more validation errors occurred.",
                             "errors": {"UniqueNo": ["The UniqueNo field is required."]}},
                            status_code=400)
    # jobDate eksik/yanlis bicimde, numara bilinmiyor ya da uniqueNo tutmuyorsa
    # gercek uc HATA VERMEZ, sessizce 204 doner.
    if (not _known(parcel_no) or body["uniqueNo"] != _unique_no(parcel_no)
            or body.get("jobDate") != _job_date(parcel_no).strftime("%Y-%m-%dT00:00:00")):
        return Response(status_code=204)

    padded = _mini_bmp().ljust(131072, b"\x00")   # gercek uc 128 KB'a doldurur
    return {"data": {"content": base64.b64encode(padded).decode(),
                     "contentType": "image/png", "name": None}, "status": 1}


# ---------------------------------------------------------------------------
# FedEx Track API taklidi — gercek OpenAPI semasiyla (kullanicinin indirdigi
# `track.json`) dogrulandi, canli sandbox'ta AYNI alanlarla test edildi.
# ---------------------------------------------------------------------------
FEDEX_KNOWN_TRACKING = {"128667043726"}


@app.post("/fedex/oauth/token")
async def fedex_oauth_token():
    """Kimlik denetlenmez — mock modda her istek token alir (gercek FedEx sandbox
    da kimlik yanlissa 401 doner, ama testler burada onu ayrica dogrulamaz)."""
    return {"access_token": "mock-fedex-token", "token_type": "bearer",
            "expires_in": 3599, "scope": "CXS-TP"}


@app.post("/fedex/track/v1/trackingnumbers")
async def fedex_track(request: Request):
    body = await request.json()
    numbers = [t.get("trackingNumberInfo", {}).get("trackingNumber", "")
              for t in body.get("trackingInfo", [])]

    results = []
    for tn in numbers:
        if tn not in FEDEX_KNOWN_TRACKING:
            results.append({"trackingNumber": tn, "trackResults": [{
                "trackingNumberInfo": {"trackingNumber": tn},
                "error": {"code": "TRACKING.TRACKINGNUMBER.NOTFOUND",
                         "message": "Tracking number cannot be found."},
            }]})
            continue
        results.append({"trackingNumber": tn, "trackResults": [{
            "trackingNumberInfo": {"trackingNumber": tn, "carrierCode": "FDXG"},
            "latestStatusDetail": {
                "code": "HL", "derivedCode": "HL",
                "statusByLocale": "Ready for pickup",
                "description": "Ready for pickup",
            },
            "scanEvents": [
                {"date": "2026-08-20T09:00:00-05:00", "eventType": "PU",
                 "derivedStatusCode": "PU", "eventDescription": "Picked up"},
                {"date": "2026-08-21T14:00:00-05:00", "eventType": "HL",
                 "derivedStatusCode": "HL", "eventDescription": "Ready for pickup"},
            ],
            "dateAndTimes": [
                {"type": "ACTUAL_PICKUP", "dateTime": "2026-08-20T09:00:00-05:00"},
            ],
        }]})

    return {"transactionId": "mock-transaction-id",
            "output": {"completeTrackResults": results, "alerts": []}}


# ============================================================
# F) GLS Grup geçidi — OAuth 2.0 + Track & Trace v1 taklidi
# ============================================================
# Gerçek geçit (api.gls-group.net): client-credentials ile Bearer token alınır,
# sonra /track-and-trace-v1/... çağrılır. Yetkisiz uçlar GÖVDESİ HTML olan bir
# 401 "Authorization failed" döner (ShipIT-Farm'ın canlı davranışı) — burada
# taklit edilmez çünkü mock istemcinin T&T v1 entitlement'i "var" sayılır.

_GLSG_EVENT_CODES = [
    {"code": "INTIAL.NORMAL", "codeNo": "0.0",
     "description": "The parcel was handed over to GLS."},
    {"code": "INBOUD.NORMAL", "codeNo": "2.0",
     "description": "The parcel has reached the parcel center."},
    {"code": "OUTBOD.NORMAL", "codeNo": "1.0",
     "description": "The parcel has left the parcel center."},
    {"code": "OUTDEL.NORMAL", "codeNo": "11.0",
     "description": "The parcel is expected to be delivered during the day."},
    {"code": "DELIVD.NORMAL", "codeNo": "3.0",
     "description": "The parcel has been delivered."},
    {"code": "CANCEL.NORMAL", "codeNo": "43.0",
     "description": "The parcel data have been deleted from the GLS IT system."},
]


def _glsg_unauthorized() -> Response:
    """Gerçek geçidin yetkisiz yanıtı: HTML gövdeli 401."""
    return Response(status_code=401, media_type="text/html",
                    content="<html><head><title>Error</title></head>"
                            "<body>Authorization failed</body></html>")


def _glsg_token_ok(request: Request) -> bool:
    return (request.headers.get("authorization") or "").startswith("Bearer mock-glsg-")


@app.post("/glsg/oauth2/v2/token")
async def glsg_token(request: Request):
    form = await request.form()
    if form.get("grant_type") != "client_credentials" \
            or not form.get("client_id") or not form.get("client_secret"):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    # Gerçek JWT değil; mock istemci yalnızca access_token dizesine bakar.
    return {"access_token": f"mock-glsg-{int(datetime.now().timestamp())}",
            "token_type": "Bearer", "expires_in": 14400,
            "scope": form.get("scope") or ""}


@app.get("/glsg/track-and-trace-v1/tracking/events/codes")
async def glsg_event_codes(request: Request):
    if not _glsg_token_ok(request):
        return _glsg_unauthorized()
    return {"eventsCodes": _GLSG_EVENT_CODES}


@app.get("/glsg/track-and-trace-v1/tracking/simple/trackids/{track_ids:path}")
async def glsg_track_simple(track_ids: str, request: Request):
    """T&T v1'in TEK takip ucu (canli: `/tracking/trackids/` ve
    `/tracking/references/` -> 404 "No static resource").

    Canli dogrulanan sema (2026-09-02): parca basina `requested` + `unitno` +
    `status` + `statusDateTime` + TAM `events` listesi (YENIDEN ESKIYE sirali,
    her olay `code`/`city`/`postalCode`/`country`/`description`/`eventDateTime`).
    Bilinmeyen numara HATA DEGIL: ayni listede `errorCode` "E_404_01" doner.
    """
    if not _glsg_token_ok(request):
        return _glsg_unauthorized()

    parcels = []
    for raw in track_ids.split(",")[:10]:
        tid = raw.strip()
        if not tid:
            continue
        if not _known(tid):
            parcels.append({"requested": tid, "errorCode": "E_404_01",
                            "errorMessage": "Resource Not Found"})
            continue
        history = _events_for(tid)            # eski -> yeni
        last_when = history[-1][2]
        parcels.append({
            "requested": tid,
            "unitno": tid,
            "status": "DELIVERED",
            "statusDateTime": last_when.strftime("%Y-%m-%dT%H:%M:%S+0200"),
            "events": [{
                "code": {"1": "INTIAL.PREADVICE", "2": "INTIAL.NORMAL",
                         "3": "OUTBOD.NORMAL", "4": "INBOUD.NORMAL",
                         "5": "OUTDEL.NORMAL", "6": "DELIVD.NORMAL"}.get(code, "INBOUD.NORMAL"),
                "city": "",
                "postalCode": "",
                "country": "IE",
                "description": desc,
                "eventDateTime": when.strftime("%Y-%m-%dT%H:%M:%S+0200"),
            } for code, desc, when, _ in reversed(history)],   # yeni -> eski
        })
    return {"parcels": parcels}


@app.get("/")
def root():
    return {"service": "GLS Mock API",
            "channels": ["shipit", "tt", "nl", "fedex"],
            "known_parcels": sorted(KNOWN_PARCELS),
            "pending_endofday": sorted(PENDING_PARCELS)}
