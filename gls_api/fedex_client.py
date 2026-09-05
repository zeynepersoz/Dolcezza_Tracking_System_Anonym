# -*- coding: utf-8 -*-
"""FedEx Track API istemcisi — SADECE takip (etiket/pickup bu sistemde yok,
kullanici karari 2026-08: Kanada/numune ve ara sira Irlanda gonderileri FedEx
uzerinden gidiyor ama etiketleri bu panelden basilmiyor).

GLS'in SOAP istemcilerinden (bkz. `tt_soap_client.py`) farkli olarak FedEx
REST + OAuth2 kullanir:
    1. POST {base}/oauth/token          -> access_token (1 saatlik, onbelleklenir)
    2. POST {base}/track/v1/trackingnumbers  -> takip sonuclari

Resmi OpenAPI semasindan (`developer.fedex.com`, kullanicinin indirdigi
`track.json`) dogrulanan alanlar disinda hicbir sey UYDURULMADI:
    output.completeTrackResults[].trackResults[]
      .latestStatusDetail.{code, derivedCode, statusByLocale, description}
      .scanEvents[].{date, eventDescription, eventType, scanLocation}
      .dateAndTimes[].{type, dateTime}   (type=ACTUAL_DELIVERY teslim tarihidir)

Sandbox (`https://apis-sandbox.fedex.com`) gercek kimlik bilgileriyle canli
test edildi (2026-08): token alindi, ornek takip numarasiyla gercek bir yanit
geldi ve bu sema ile birebir eslesti.
"""
from __future__ import annotations

import time

import requests

from . import config


class FedExError(Exception):
    """FedEx API hatasi. `status` HTTP kodu, `code` FedEx'in kendi hata kodu."""

    def __init__(self, message: str, status: int | None = None, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def is_auth_error(self) -> bool:
        return self.status in (401, 403)


class FedExClient:
    """Tek hesap icin FedEx Track API istemcisi. Token onbelleklenir (sure sonuna dek)."""

    def __init__(self, base_url: str = "", api_key: str = "", api_secret: str = "",
                account_number: str = "", timeout: int = 20):
        self.base_url = (base_url or config.FEDEX_BASE_URL).rstrip("/")
        self.api_key = api_key or config.FEDEX_API_KEY
        self.api_secret = api_secret or config.FEDEX_API_SECRET
        self.account_number = account_number or config.FEDEX_ACCOUNT_NUMBER
        self.timeout = timeout
        self._token: str = ""
        # `time.monotonic()`: sistem saati NTP'yle geri gitse bile token'i
        # erken/gec bayat sanmayalim (bkz. auth/ratelimit.py ayni gerekce).
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    def _fetch_token(self) -> str:
        try:
            resp = requests.post(
                f"{self.base_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.api_key,
                    "client_secret": self.api_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FedExError(f"FedEx token isteği başarısız: {exc}") from exc

        if resp.status_code != 200:
            raise FedExError(
                f"FedEx kimlik doğrulama hatası: HTTP {resp.status_code} - {resp.text[:200]}",
                status=resp.status_code,
            )
        data = resp.json()
        token = data.get("access_token") or ""
        if not token:
            raise FedExError("FedEx yanıtında access_token yok")
        # Suresi dolmadan 60 sn once yenile — tam sinirda bir istek atarken
        # token'in tam o anda bayatlamasi (yaris durumu) onlensin.
        self._token_expires_at = time.monotonic() + max(int(data.get("expires_in") or 0) - 60, 0)
        self._token = token
        return token

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        return self._fetch_token()

    def check_auth(self) -> None:
        """Yan etkisiz kimlik testi (`gls_api/providers.py:doctor`) — token
        alinabiliyor mu, gercek bir takip numarasi HARCAMADAN."""
        self._get_token()

    # ------------------------------------------------------------------
    def track(self, tracking_numbers: list[str], include_detailed_scans: bool = True) -> dict:
        """Verilen takip numaralarinin (en fazla 30) tam sonucunu doner.

        Donen sozluk `output.completeTrackResults` yapisidir (ham FedEx
        yaniti) — normalize etme `tracking/scheduler.py:map_fedex_result`de.
        """
        if not tracking_numbers:
            return {"completeTrackResults": []}
        if len(tracking_numbers) > 30:
            raise FedExError("FedEx Track API tek istekte en fazla 30 takip numarası kabul eder")

        token = self._get_token()
        payload = {
            "includeDetailedScans": include_detailed_scans,
            "trackingInfo": [
                {"trackingNumberInfo": {"trackingNumber": str(tn)}}
                for tn in tracking_numbers
            ],
        }
        try:
            resp = requests.post(
                f"{self.base_url}/track/v1/trackingnumbers",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-locale": "en_US",
                    "Authorization": f"Bearer {token}",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FedExError(f"FedEx takip isteği başarısız: {exc}") from exc

        if resp.status_code != 200:
            code = ""
            try:
                errors = resp.json().get("errors") or []
                code = errors[0].get("code", "") if errors else ""
            except ValueError:
                pass
            raise FedExError(
                f"FedEx takip hatası: HTTP {resp.status_code} - {resp.text[:200]}",
                status=resp.status_code, code=code,
            )
        return (resp.json().get("output") or {})

    def track_one(self, tracking_number: str) -> dict | None:
        """Tek takip numarası için `trackResults[0]`; sonuç yoksa `None`."""
        output = self.track([tracking_number])
        results = output.get("completeTrackResults") or []
        if not results:
            return None
        track_results = results[0].get("trackResults") or []
        return track_results[0] if track_results else None
