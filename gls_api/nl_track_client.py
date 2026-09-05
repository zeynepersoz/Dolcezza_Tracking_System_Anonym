# -*- coding: utf-8 -*-
"""
GLS Netherlands Track & Trace REST istemcisi — takip + POD.

Etiket API'siyle (nl_client.py) aynı kimlik bilgilerini kullanır ama ayrı bir
servistir ve iki kritik farkı vardır:

    etiket : https://api.gls.nl/v1/api   gövde alanı `username`
    takip  : https://api.gls.nl/tt/V1    gövde alanı `userName`   <-- büyük N

Uç nokta:
    POST /api/parcel/v1/details
        {"userName": "...", "password": "...", "parcelNumbers": ["...", ...]}
    -> {"parcels": [{"parcelNo", "uniqueNo", "jobDate", "state", "events": [...]}]}

Tek istekte en fazla 20 parça numarası kabul edilir; bu yüzden `parcel_details`
listeyi kendisi böler.

Bilinmeyen numara HATA DEĞİLDİR: GLS onu yanıttan sessizce düşürür. Bu yüzden
dönüş bir liste değil, `{parcelNo: parça}` sözlüğüdür — çağıran hangi parçanın
cevaplanmadığını görebilsin.

POD — ÜÇÜNCÜ BİR HOST (tracking.gls.nl portalının kullandığı uç):

    POST https://apm.gls.nl/api/tracktrace/v1/{parcelNo}/pod
        {"uniqueNo": "...", "jobDate": "..."}
    -> {"data": {"content": "<base64>", "contentType": "image/png"}}

Üç tuzağı vardır:
  1. Kimlik doğrulaması yok; anahtar `uniqueNo` + `jobDate` çiftidir ve bunlar
     yalnızca yukarıdaki `details` yanıtından öğrenilebilir. `jobDate`
     GÖNDERİLMEZSE uç nokta hata değil **204 No Content** döner.
  2. `contentType` her zaman "image/png" der ama içerik öyle olmayabilir; canlı
     veride BMP, JPEG ve PNG üçü de görüldü. Biçim sihirli baytlardan tespit
     edilir (imza PNG, teslimat fotoğrafı BMP/JPEG).
  3. İçerik sabit bir bloğa (128 KB / 64 KB) **sıfırla doldurulmuş** gelir;
     fazlalık biçimin kendi uzunluk bilgisiyle kırpılır (`_trim_padding`).

Takip yanıtında POD/imza alanı YOKTUR (canlı veriyle doğrulandı); teslim alanın
adı yalnızca teslimat olayının `details` alanında düz metin geçer.

TESLİMAT BİLGİLERİ — aynı host, ama bu sefer OTURUMLU:

    POST https://apm.gls.nl/api/account/v1/login
        {"userName": "...", "password": "..."}      (etiket/takip kimliğiyle aynı)
    -> Belirteç yanıt GÖVDESİNDE DEĞİL, `Authorization` YANIT BAŞLIĞINDA döner;
       gövde yalnızca {"result": 1} der.

    POST https://apm.gls.nl/api/tracktrace/v1/{parcelNo}/details
        {"uniqueNo": "...", "jobDate": "...", "culture": "en-GB"}
    -> alıcı ünvanı + açık adres + ürün/servis + ağırlık + ölçü + teslim alan

`culture` ZORUNLUDUR (yoksa 400). "tr-TR" kabul edilir ama GLS'te Türkçe çeviri
yoktur: ülke adı ve olay açıklamaları BOŞ döner — bu yüzden "en-GB" kullanılır.

Bu uç, tt/V1'de bulunmayan alıcı ADINI verir. (Kimliksiz
`/postalcode/{zip}/details` ucu adresi verir ama adı vermez.)
"""
from __future__ import annotations

import base64
import re
import struct
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from . import config
from .nl_client import GLSNLError

# GLS'in tek istekte kabul ettigi azami parca sayisi.
MAX_PARCELS_PER_CALL = 20

# 429'da beklenecek sure bulunamazsa kullanilacak varsayilan (GLS penceresi dakikalik).
_RATE_LIMIT_FALLBACK = 60.0
# "Rate limit exceeded. Try again in 56 seconds." — Retry-After basligi her zaman gelmiyor.
_RETRY_AFTER_TEXT = re.compile(r"(\d+)\s*second", re.IGNORECASE)


def _retry_after_seconds(resp, data: dict) -> float:
    """429 yanitindan bekleme suresi. Once baslik, sonra hata metni, sonra varsayilan."""
    header = (resp.headers.get("Retry-After") or "").strip()
    if header.isdigit():
        return min(float(header) + 1, 120.0)
    match = _RETRY_AFTER_TEXT.search(data.get("message") or resp.text or "")
    if match:
        return min(float(match.group(1)) + 1, 120.0)
    return _RATE_LIMIT_FALLBACK

DETAILS_PATH = "/api/parcel/v1/details"

# apm.gls.nl `details` ucunun zorunlu `culture` alani. GLS'te Turkce ceviri
# yoktur — "tr-TR" 200 doner ama ulke adi ve olay metinleri BOS gelir.
POD_CULTURE = "en-GB"

# Belirtec 2 saat gecerli; bitimine yakin yenilemek icin kisa tutuluyor.
_TOKEN_TTL_SECONDS = 50 * 60

# Sihirli bayt -> uzanti. contentType guvenilmez oldugu icin icerige bakiyoruz.
_MAGIC = (
    (b"BM", "bmp"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"%PDF", "pdf"),
    (b"II*\x00", "tif"),
    (b"MM\x00*", "tif"),
)


def _media_type(data: bytes) -> str:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    return "bin"


def _pod_job_date(job_date: str) -> str:
    """Takip yanıtındaki `jobDate`'i POD ucunun beklediği biçime çevirir.

    İki uç aynı alanı FARKLI temsil eder ve bu sessizce yanlış olur:
        takip -> "2026-07-07T22:00:00Z"      (UTC)
        POD   -> "2026-07-08T00:00:00"       (Hollanda yerel, gece yarısı)

    Yanlış gönderilirse hata değil 204 No Content döner, yani "POD yok" gibi
    görünür. Değer aslında bir TARİH'tir; Amsterdam'da gece yarısına denk gelir.
    """
    text = job_date.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo("Europe/Amsterdam"))
    return parsed.strftime("%Y-%m-%dT00:00:00")


def _trim_padding(data: bytes) -> bytes:
    """Sondaki sifir dolgusunu atar.

    Uc nokta icerigi sabit bir bloga (128 KB / 64 KB) sifirla doldurup gonderiyor;
    canli veride BMP 80054 -> 131072, JPEG 35492 -> 65536 olarak geldi. Pillow
    fazlaligi tolere ediyor ama arsivde saklamanin anlami yok. Biçimin kendi
    uzunluk bilgisi kullanilir — kor "sondaki sifirlari kes" yapilirsa mesru
    olarak sifirla biten PNG/TIFF verisi bozulabilir.
    """
    if data.startswith(b"BM") and len(data) >= 6:
        declared = struct.unpack("<I", data[2:6])[0]
        return data[:declared] if 0 < declared <= len(data) else data
    if data.startswith(b"\xff\xd8\xff"):
        # JPEG mutlaka EOI (FFD9) ile biter; sonrasi dolgudur.
        end = data.rfind(b"\xff\xd9")
        return data[:end + 2] if end > 0 else data
    return data


class GLSNLTrackClient:
    def __init__(self, base_url=None, username=None, password=None, pod_base_url=None):
        self.base_url = (base_url or config.NL_TT_BASE_URL).rstrip("/")
        self.pod_base_url = (pod_base_url or config.NL_POD_BASE_URL).rstrip("/")
        self.username = username or config.NL_USERNAME
        self.password = password or config.NL_PASSWORD
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        # `tracking/scheduler.py:_live_delivery_scan` bu session'i bir turda
        # ONLARCA istek icin PARALEL kullaniyor. `requests`in varsayilan havuzu
        # 10; asilinca her fazladan istek yeni bir TLS el sikismasi yapiyor
        # (canli log: "Connection pool is full, discarding connection:
        # apm.gls.nl"). TLS el sikismasi CPU'dur ve tek cekirdekli sunucuda
        # asyncio event loop'unu ac birakiyordu (kullanici: "site cok yavas").
        _adapter = requests.adapters.HTTPAdapter(pool_connections=24, pool_maxsize=24)
        self.session.mount("https://", _adapter)
        self.session.mount("http://", _adapter)
        self._last_call = 0.0
        self._token = ""
        self._token_at = 0.0

    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict, _retries: int = 1) -> dict:
        wait = config.RATE_LIMIT_SECONDS - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

        url = f"{self.base_url}/{path.lstrip('/')}"
        # `userName` — etiket API'sindeki `username` degil.
        body = {"userName": self.username, "password": self.password, **payload}
        try:
            resp = self.session.post(url, json=body, timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise GLSNLError(f"Ağ hatası ({url}): {e}")

        try:
            data = resp.json()
        except ValueError:
            data = {}

        if resp.status_code == 429 and _retries > 0:
            # GLS dakikalik bir pencere uygular ve ne kadar bekleneceğini söyler
            # ("Try again in 56 seconds"). Beklemeden vazgeçersek 4000 parçalık
            # bir taramanın büyük kısmı sessizce güncellenmeden kalır.
            time.sleep(_retry_after_seconds(resp, data))
            return self._post(path, payload, _retries - 1)

        if resp.status_code != 200:
            message = data.get("message") or (resp.text or "").strip()[:300]
            if resp.status_code == 401:
                message += " — NL_USERNAME/NL_PASSWORD hatalı"
            raise GLSNLError(
                f"{url} -> HTTP {resp.status_code}: {message or '(hata metni yok)'}",
                status=resp.status_code)
        return data

    # ==================================================================
    def parcel_details(self, parcel_numbers) -> dict[str, dict]:
        """{parcelNo: parça} — listeyi 20'şerli bölerek sorgular.

        Yanıtta dönmeyen numaralar sözlükte YER ALMAZ (müşteri hesabına ait
        olmayan veya henüz GLS sistemine girmemiş parçalar).
        """
        numbers = [str(n).strip() for n in parcel_numbers if str(n).strip()]
        found: dict[str, dict] = {}
        for i in range(0, len(numbers), MAX_PARCELS_PER_CALL):
            chunk = numbers[i:i + MAX_PARCELS_PER_CALL]
            data = self._post(DETAILS_PATH, {"parcelNumbers": chunk})
            for parcel in data.get("parcels") or []:
                key = str(parcel.get("parcelNo") or "").strip()
                if key:
                    found[key] = parcel
        return found

    def parcel_detail(self, parcel_number: str) -> dict | None:
        """Tek parça — bulunamazsa None."""
        return self.parcel_details([parcel_number]).get(str(parcel_number).strip())

    # ==================================================================
    def _pod_key(self, number: str) -> dict:
        """POD ve teslimat-bilgisi uçlarının ortak anahtarı: {uniqueNo, jobDate}.

        Her ikisi de yalnızca tt/V1 takip yanıtından öğrenilebilir.
        """
        detail = self.parcel_detail(number)
        if detail is None:
            raise GLSNLError(f"Parça GLS Netherlands hesabında bulunamadı: {number}")

        unique_no = str(detail.get("uniqueNo") or "").strip()
        job_date = str(detail.get("jobDate") or "").strip()
        if not unique_no or not job_date:
            raise GLSNLError(
                f"POD için gereken alanlar takip yanıtında yok ({number}): "
                f"uniqueNo={unique_no or '-'} jobDate={job_date or '-'}")
        return {"uniqueNo": unique_no, "jobDate": _pod_job_date(job_date)}

    def _pod_post(self, path: str, payload: dict, headers=None):
        url = f"{self.pod_base_url}/{path.lstrip('/')}"
        try:
            return self.session.post(url, json=payload, headers=headers,
                                     timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise GLSNLError(f"Ağ hatası ({url}): {e}")

    def _bearer(self) -> str:
        """apm.gls.nl oturum belirteci — takip/etiket kimliğiyle alınır, önbelleklenir.

        Belirteç yanıt gövdesinde değil `Authorization` YANIT BAŞLIĞINDA gelir;
        gövde sadece {"result": 1} der.
        """
        if self._token and time.time() - self._token_at < _TOKEN_TTL_SECONDS:
            return self._token
        resp = self._pod_post("/api/account/v1/login",
                              {"userName": self.username, "password": self.password})
        token = resp.headers.get("Authorization", "").strip()
        if resp.status_code != 200 or not token:
            raise GLSNLError(
                f"apm.gls.nl oturumu açılamadı -> HTTP {resp.status_code}"
                f"{'' if token else ' (Authorization başlığı gelmedi)'}",
                status=resp.status_code)
        self._token, self._token_at = token, time.time()
        return token

    def delivery_details(self, parcel_number: str, key: dict | None = None) -> dict:
        """Alıcı ünvanı/adresi, ürün, servis, ağırlık, ölçü, teslim alan.

        tt/V1 bunları vermez (`addresses` boş, `deliveryName` boş gelir); bu uç
        oturum ister ve `culture` alanı olmadan 400 döner.
        """
        number = str(parcel_number).strip()
        body = {**(key or self._pod_key(number)), "culture": POD_CULTURE}
        resp = self._pod_post(f"/api/tracktrace/v1/{number}/details", body,
                              headers={"Authorization": self._bearer()})
        if resp.status_code == 401:
            # Belirtec suresi dolmus olabilir; bir kez yenileyip tekrar dene.
            self._token = ""
            resp = self._pod_post(f"/api/tracktrace/v1/{number}/details", body,
                                  headers={"Authorization": self._bearer()})
        if resp.status_code != 200:
            raise GLSNLError(f"Teslimat bilgileri alınamadı ({number}) -> "
                             f"HTTP {resp.status_code}: "
                             f"{(resp.text or '').strip()[:300] or '(hata metni yok)'}",
                             status=resp.status_code)
        return resp.json() or {}

    def parcel_pod(self, parcel_number: str, key: dict | None = None) -> tuple[bytes, str]:
        """(POD baytları, uzantı). POD henüz yoksa GLSNLError yükseltir."""
        number = str(parcel_number).strip()
        # Kimlik gonderilmez — bu uc nokta userName/password kabul etmez.
        resp = self._pod_post(f"/api/tracktrace/v1/{number}/pod",
                              key or self._pod_key(number))

        # 204: parca teslim edilmemis ya da surucu fotograf/imza almamis.
        if resp.status_code == 204:
            raise GLSNLError(f"POD henüz oluşmamış ({number})", status=204)
        if resp.status_code != 200:
            raise GLSNLError(f"{self.pod_base_url}/api/tracktrace/v1/{number}/pod -> "
                             f"HTTP {resp.status_code}: "
                             f"{(resp.text or '').strip()[:300] or '(hata metni yok)'}",
                             status=resp.status_code)

        content = ((resp.json() or {}).get("data") or {}).get("content")
        if not content:
            raise GLSNLError(f"POD yanıtı boş ({number})")

        data = _trim_padding(base64.b64decode(content))
        return data, _media_type(data)

    def parcel_pod_bundle(self, parcel_number: str) -> tuple[bytes, str, dict]:
        """(POD baytları, uzantı, teslimat bilgileri) — belge üretimi için.

        `_pod_key` bir kez hesaplanıp iki uca da verilir; aksi halde tt/V1
        gereksiz yere iki kez sorgulanır (ve hız sınırına takılır).

        Teslimat bilgileri ALINAMAZSA hata verilmez: POD görüntüsü esas çıktıdır,
        bilgiler yalnızca belgeyi zenginleştirir — oturum açılamadı diye POD'u
        büsbütün kaybetmek yanlış olur.
        """
        number = str(parcel_number).strip()
        key = self._pod_key(number)
        data, media_type = self.parcel_pod(number, key)
        try:
            info = self.delivery_details(number, key)
        except GLSNLError:
            info = {}
        return data, media_type, info
