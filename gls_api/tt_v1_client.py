# -*- coding: utf-8 -*-
"""
GLS Track & Trace v1 REST istemcisi — SADECE takip (etiket/POD YOK).

Klasik kanallardan (ShipIT / T&T SOAP / GLS NL) farkli olarak GLS grubunun
ORTAK GECIDINDEN gecer ve OAuth 2.0 client-credentials ister:

    1) POST {GLSG_TOKEN_URL}
         grant_type=client_credentials
         &client_id=<App API Key>&client_secret=<App Secret>&scope=all
       -> {"access_token": "<JWT>", "token_type": "Bearer", "expires_in": 14400}

    2) GET {GLSG_BASE_URL}/track-and-trace-v1/tracking/...
         Authorization: Bearer <JWT>
         Accept: application/json

Kimlik: dev-portal.gls-group.net'te olusturulan "App"in API Key + Secret'i.
`.env`'de `GLSG_CLIENT_ID` / `GLSG_CLIENT_SECRET`.

CANLI DOGRULAMA (2026-09-02, api.gls-group.net — uretim, gercek IE numaralari):
  * GET /track-and-trace-v1/tracking/events/codes            -> HTTP 200
        {"eventsCodes":[{"code":"DELIVD.NORMAL","codeNo":"3.0",
                         "description":"The parcel has been delivered."}, ...]}
  * GET /track-and-trace-v1/tracking/simple/trackids/{id1,..} -> HTTP 200
        {"parcels":[
          { "requested": "21569979623",
            "unitno":    "21569979623",
            "status":    "DELIVERED",
            "statusDateTime": "2026-07-29T09:40:00+0200",   # ISO, ZAMAN DILIMI OFSETLI (Z degil)
            "events": [   # YENIDEN ESKIYE dogru sirali
              { "code": "DELIVD.NORMAL", "city": "", "postalCode": "", "country": "IE",
                "description": "The parcel has been delivered.",
                "eventDateTime": "2026-07-29T09:40:00+0200" }, ... ] },
          { "requested": "...", "errorCode": "E_404_01",
            "errorMessage": "Resource Not Found" }   # bilinmeyen numara: HATA DEGIL
        ]}
  * GET /track-and-trace-v1/tracking/trackids/{...}          -> HTTP 404
        "No static resource ..." — BU UC YOK (Spring varsayilan 404'u).
  * GET /track-and-trace-v1/tracking/references/{...}        -> HTTP 404  (yok)
  Yani T&T v1'in kullanilabilir yuzeyi iki uc: `events/codes` ve `simple/trackids`.
  `simple` TAM olay gecmisini de veriyor — ayri bir "detay" ucu gerekmiyor.

  Ayni token'la /shipit-farm/v1/... -> HTTP 401 "Authorization failed":
  ShipIT-Farm urununun bu App ID'ye entitlement'i henuz acilmadi (POD oradan
  gelecek; GLS'e acmasi icin talep gonderildi). Bkz. docs/IT_TALEP_NOTU.md (D).

SINIRLAR (GLS onboarding formu + GLS Ireland e-postasi):
  * Istek basina en fazla 10 takip numarasi  -> `MAX_TRACKIDS_PER_CALL`
  * Gunluk 500 istek           -> `config.GLSG_DAILY_LIMIT` (panelden artirilabilir)
  10 numara/istek oldugu icin bu kanal, GLS NL'in 20'lik toplu ucundan sonra
  en verimli takip yoludur; parca basi tek istek atan T&T SOAP'a tercih edilir.

GUNLUK KOTA KORUMASI — `budget_left()` / `_spend()`:
  Tarayici 15 dakikada bir calisir (gunde 96 tur). Her turda N aktif IE parcasi
  10'arli bolunur, yani gunluk istek ~ 96 * ceil(N/10):
        N=20 -> 192   N=50 -> 480   N=63 -> 672   (SINIR 500)
  Olculdu (2026-09-02, FA26): 63 IE parcasi var. Koruma olmadan kota daha ilk
  gun asilir ve GLS 429 vermeye baslar. Bu yuzden istemci gun basina sayac
  tutar; kota bitince istek ATILMAZ, `GLSGroupError(status=429)` yukselir ve
  `tracking/scheduler.py:_glsg_results` o turu sessizce atlar (parcalar
  "bakilmadi" kalir, ertesi gun sirayla yeniden sorulur).

  Sayac UTC gun sinirinda sifirlanir; GLS'in penceresi belgelenmemis, UTC en
  makul varsayim. Sayac SURECE aittir — konteyner yeniden baslarsa sifirlanir
  (bilincli: kalici sayac icin ayri bir depo gerekirdi ve yeniden baslatma
  nadirdir; asil koruma zaten tavanin altinda kalmak).

Bilinmeyen numara HATA DEGILDIR: HTTP yine 200'dur, ilgili parca nesnesinde
`errorCode` (E_404_01) doner. Bu yuzden `track_simple` bir liste degil
`{trackId: parca}` sozlugu doner — cagiran hangisinin cevaplanmadigini
gorebilsin (gls_api/nl_track_client.py ile ayni desen).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from . import config

# GLS'in tek istekte kabul ettigi azami takip numarasi (onboarding + e-posta).
MAX_TRACKIDS_PER_CALL = 10

# Tum T&T v1 yollarinin ortak oneki (GLSG_BASE_URL host'tur, yol degil).
_TT_V1_PREFIX = "/track-and-trace-v1"

# Token omru dolmadan bu kadar saniye once yenile — saat kaymasi + istek suresi
# payi. Gozlenen `expires_in` = 14400 (4 saat).
_TOKEN_REFRESH_SLACK = 300


class GLSGroupError(Exception):
    """GLS grup gecidi hatasi. `status` varsa HTTP kodudur."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def is_auth_error(self) -> bool:
        # 401/403: token alindi ama uc nokta bu App ID'ye acik degil
        # (entitlement eksik) YA DA istemci kimligi hatali. Ikisi de "yetki"
        # sinifidir; `providers.doctor()` ayni mesaji gosterir.
        return self.status in (401, 403)

    @property
    def is_quota_error(self) -> bool:
        """Gunluk kota bitti (bizim sayacimiz) ya da GLS 429 dedi."""
        return self.status == 429


class TrackTraceV1Client:
    def __init__(self, base_url=None, token_url=None, client_id=None,
                 client_secret=None, scope=None):
        self.base_url = (base_url or config.GLSG_BASE_URL).rstrip("/")
        self.token_url = token_url or config.GLSG_TOKEN_URL
        self.client_id = client_id or config.GLSG_CLIENT_ID
        self.client_secret = client_secret or config.GLSG_CLIENT_SECRET
        # None -> config'ten oku; "" -> scope hic gonderilmesin (bilerek).
        self.scope = config.GLSG_SCOPE if scope is None else scope
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._last_call = 0.0
        self._token = ""
        self._token_expires_at = 0.0
        # Gunluk kota sayaci (bkz. modul basligi). `_day` UTC tarihidir.
        self._day = ""
        self._calls_today = 0

    # ------------------------------------------------------------------
    # Gunluk kota
    # ------------------------------------------------------------------
    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def budget_left(self) -> int:
        """Bugun kalan istek hakki. Gun degistiyse sayac sifirlanir.

        Cagiran (scheduler) bir tur baslamadan once bakip turu tumden
        atlayabilsin diye ayri bir islev: kota bitmisken 10 ayri istek
        denemesi 10 ayri hata satiri uretirdi.
        """
        if self._day != self._today():
            self._day, self._calls_today = self._today(), 0
        return max(int(config.GLSG_DAILY_LIMIT) - self._calls_today, 0)

    def _spend(self) -> None:
        """Bir istek harcar; kota bittiyse ISTEK ATMADAN hata yukseltir."""
        if self.budget_left() <= 0:
            raise GLSGroupError(
                f"GLS Track & Trace v1 gunluk istek kotasi doldu "
                f"({config.GLSG_DAILY_LIMIT}/gun) — yarin devam edilecek",
                status=429)
        self._calls_today += 1

    # ------------------------------------------------------------------
    def _bearer(self, force: bool = False) -> str:
        """Gecerli Bearer token — omru dolana kadar onbelleklenir.

        `scp` claim'i bos donse bile (canli gozlem: scope=all verilse dahi bos)
        T&T v1 token'i kabul ediyor; scope'un asil etkisi ShipIT-Farm tarafinda.
        """
        if not force and self._token and time.time() < self._token_expires_at:
            return self._token

        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.scope:
            data["scope"] = self.scope

        try:
            resp = self.session.post(self.token_url, data=data,
                                     timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise GLSGroupError(f"Ağ hatası (token: {self.token_url}): {e}")

        if resp.status_code != 200:
            body = (resp.text or "").strip()[:200]
            hint = (" — GLSG_CLIENT_ID/GLSG_CLIENT_SECRET hatalı"
                    if resp.status_code in (400, 401) else "")
            raise GLSGroupError(
                f"{self.token_url} -> HTTP {resp.status_code}: "
                f"{body or '(hata metni yok)'}{hint}",
                status=resp.status_code)

        try:
            payload = resp.json()
        except ValueError:
            raise GLSGroupError(f"Token yanıtı JSON değil: {resp.text[:200]}")

        token = payload.get("access_token")
        if not token:
            raise GLSGroupError(
                f"Token yanıtında access_token yok: {str(payload)[:200]}")

        # `expires_in` saniye cinsinden; yoksa temkinli bir saat varsay.
        ttl = int(payload.get("expires_in") or 3600)
        self._token = token
        self._token_expires_at = time.time() + max(ttl - _TOKEN_REFRESH_SLACK, 30)
        return token

    def _get(self, path: str, _retry: bool = True) -> dict:
        """`{_TT_V1_PREFIX}{path}` -> JSON. 401'de token'i bir kez yenileyip dener.

        Gunluk kota BURADA harcanir (token istegi degil, veri istegi sayilir).
        401 yenilemesi ikinci bir istek atar ve o da sayilir — GLS tarafinda da
        oyle gorunur.
        """
        self._spend()
        wait = config.RATE_LIMIT_SECONDS - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

        url = f"{self.base_url}{_TT_V1_PREFIX}{path}"
        try:
            resp = self.session.get(
                url,
                headers={"Authorization": f"Bearer {self._bearer()}"},
                timeout=config.REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise GLSGroupError(f"Ağ hatası ({url}): {e}")

        # Token suresi tam sinirda dolmus olabilir — bir kez zorla yenile.
        if resp.status_code == 401 and _retry:
            self._bearer(force=True)
            return self._get(path, _retry=False)

        if resp.status_code != 200:
            body = (resp.text or "").strip()[:300]
            if resp.status_code in (401, 403):
                body += (" — token geçerli ama bu uç App ID'ye açık değil "
                         "(entitlement) ya da GLSG_CLIENT_* hatalı")
            elif resp.status_code == 429:
                body += (f" — GLS istek sınırı aştığımızı söylüyor "
                         f"(bizim sayacımız: bugün {self._calls_today} istek, "
                         f"tavan {config.GLSG_DAILY_LIMIT})")
            raise GLSGroupError(f"{url} -> HTTP {resp.status_code}: "
                                f"{body or '(hata metni yok)'}",
                                status=resp.status_code)

        try:
            return resp.json()
        except ValueError:
            raise GLSGroupError(f"Geçersiz JSON yanıtı ({url}): {resp.text[:200]}")

    # ==================================================================
    def event_codes(self) -> dict[str, dict]:
        """{code: {"codeNo", "description"}} — GLS'in tüm olay kodu sözlüğü.

        Yan etkisiz ve hesaba bağlı değil; `providers.doctor()` bunu
        bağlantı + kimlik + T&T v1 entitlement testi olarak kullanır (canlı
        doğrulandı: bu uç 200 dönüyorsa token ve yetki tamam demektir).
        """
        data = self._get("/tracking/events/codes")
        out: dict[str, dict] = {}
        for row in data.get("eventsCodes") or []:
            code = str(row.get("code") or "").strip()
            if code:
                out[code] = {"codeNo": str(row.get("codeNo") or ""),
                             "description": row.get("description") or ""}
        return out

    def track_simple(self, track_ids) -> dict[str, dict]:
        """`{trackId: parça}` — listeyi 10'arlı bölerek `simple/trackids` ile sorgular.

        T&T v1'in TEK takip ucu budur (`/tracking/trackids/` ve
        `/tracking/references/` prod'da 404 "No static resource" döner).

        Bulunamayan numara sözlükte YER ALMAZ: yanıtta ilgili parça nesnesi
        `errorCode` (E_404_01) ile döner, HTTP yine 200'dür (gls_api/nl_track_client.py
        ile aynı desen). Başarılı parçanın tam alan şeması henüz canlı görülmedi;
        `scheduler.map_glsg_tt_parcel` defansif okur.
        """
        ids = [str(t).strip() for t in track_ids if str(t).strip()]
        found: dict[str, dict] = {}
        for i in range(0, len(ids), MAX_TRACKIDS_PER_CALL):
            chunk = ids[i:i + MAX_TRACKIDS_PER_CALL]
            data = self._get("/tracking/simple/trackids/" + ",".join(chunk))
            for parcel in data.get("parcels") or []:
                if parcel.get("errorCode"):
                    continue
                # Canli yanit: `unitno` (asil parca no) + `requested` (istenen).
                key = str(parcel.get("unitno")
                          or parcel.get("requested")
                          or parcel.get("trackId")
                          or parcel.get("trackID") or "").strip()
                if key:
                    found[key] = parcel
        return found
