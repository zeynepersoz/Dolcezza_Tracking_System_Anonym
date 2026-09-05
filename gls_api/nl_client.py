# -*- coding: utf-8 -*-
"""
GLS Netherlands REST istemcisi — SADECE etiket (takip/POD uç noktası yoktur).

Taban URL:
    canlı : https://api.gls.nl/v1/api
    test  : https://api.gls.nl/test/v1/api

Uç noktalar (hepsi POST, `?api-version=1.0` zorunlu):
    Authentication/ValidateLogin  -> hesabın geçerliliği + müşteri numaraları
    Label/Create                  -> etiket üretir (Base64 PDF/ZPL) + parça numaraları
    Label/Confirm                 -> etiketi gönderildi olarak onaylar
    ParcelShop/GetParcelShops     -> yakındaki GLS ParcelShop'lar

Kimlik doğrulama: HTTP başlığı YOK. `username`/`password` her isteğin JSON
gövdesine konur. (Eski kodda kullanılan `Ocp-Apim-Subscription-Key` diye bir
şey yoktur — kaldırıldı.)

DİKKAT — takip ve POD:
    Bu API'de parça sorgulama uç noktası YOKTUR; takip ve POD ayrı hostlardan
    gelir (bkz. gls_api/nl_track_client.py). `parcel_details()` bilerek yoktur.

Alan adları camelCase'tir ve GLS'in resmî C# SDK'sıyla birebir aynıdır.
`None` değerli alanlar gövdeden atılır (SDK: NullValueHandling.Ignore) —
sunucu boş string ile null'ı farklı yorumluyor.
"""
from __future__ import annotations

import time

import requests

from . import config

# GLS NL sifre alani 20 karakterle sinirlidir (portalda da boyle uretilir).
MAX_PASSWORD_LENGTH = 20

# Tek istekte istenebilecek azami ParcelShop sayisi.
MAX_PARCEL_SHOPS = 10


class GLSNLError(Exception):
    """GLS NL hatası.

    `status` HTTP kodu, `api_status` gövdedeki `status` metni,
    `errors` GLS'in alan bazlı hata sözlüğü ({"zipCode": ["..."]}).
    """

    def __init__(self, message: str, status: int | None = None,
                 api_status: str = "", errors: dict | None = None):
        super().__init__(message)
        self.status = status
        self.api_status = api_status
        self.errors = errors or {}

    @property
    def is_auth_error(self) -> bool:
        return self.status == 401

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429


def _prune(value):
    """None alanları özyinelemeli olarak atar (GLS SDK'sıyla aynı davranış)."""
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


class GLSNLClient:
    def __init__(self, base_url=None, username=None, password=None, customer_no=None):
        self.base_url = (base_url or config.NL_BASE_URL).rstrip("/")
        self.username = username or config.NL_USERNAME
        self.password = password or config.NL_PASSWORD
        self.customer_no = customer_no if customer_no is not None else config.NL_CUSTOMER_NO
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self._last_call = 0.0

    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        wait = config.RATE_LIMIT_SECONDS - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

        url = f"{self.base_url}/{path.lstrip('/')}"
        body = _prune({"username": self.username, "password": self.password, **payload})
        try:
            resp = self.session.post(url, json=body, timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise GLSNLError(f"Ağ hatası ({url}): {e}")

        try:
            data = resp.json()
        except ValueError:
            data = {}

        if resp.status_code != 200 or data.get("error"):
            raise self._error_from(resp, data, url)
        return data

    def _error_from(self, resp: requests.Response, data: dict, url: str) -> GLSNLError:
        message = data.get("message") or (resp.text or "").strip()[:300]
        errors = data.get("errors") or {}
        if errors:
            # {"zipCode": ["Ongeldige postcode"]} -> "zipCode: Ongeldige postcode"
            detail = "; ".join(f"{k}: {', '.join(v)}" for k, v in errors.items())
            message = f"{message} — {detail}" if message else detail

        if resp.status_code == 401:
            message += " — NL_USERNAME/NL_PASSWORD hatalı"
            if len(self.password) > MAX_PASSWORD_LENGTH:
                message += (f" (şifreniz {len(self.password)} karakter; GLS NL en fazla "
                            f"{MAX_PASSWORD_LENGTH} karakter kabul eder)")
        elif resp.status_code == 429:
            message += " — istek sınırı aşıldı, biraz bekleyip tekrar deneyin"

        return GLSNLError(f"{url} -> HTTP {resp.status_code}: {message or '(hata metni yok)'}",
                          status=resp.status_code,
                          api_status=data.get("status") or "",
                          errors=errors)

    # ==================================================================
    def validate_login(self) -> dict:
        """Hesabı doğrular. Dönüş: {"customers": [{"apiCustomerNo", "subjects": [...]}]}

        Yan etkisiz olduğu için bağlantı/kimlik testi olarak da kullanılır
        (bkz. gls_api/providers.py:doctor).
        """
        return self._post("Authentication/ValidateLogin?api-version=1.0", {})

    def resolve_customer_no(self) -> str:
        """Yapılandırılmış müşteri numarası; yoksa ValidateLogin'den ilkini alır.

        Sonuç `self.customer_no`'ya yazılır — sonraki çağrılar ağa çıkmaz.
        """
        if self.customer_no:
            return self.customer_no
        customers = self.validate_login().get("customers") or []
        for customer in customers:
            for subject in customer.get("subjects") or []:
                for cust_no in subject.get("custNos") or []:
                    if cust_no:
                        self.customer_no = cust_no
                        return cust_no
        raise GLSNLError("Hesapta müşteri numarası bulunamadı — .env'de NL_CUSTOMER_NO verin")

    # ==================================================================
    def create_label(self, request: dict, label_type: str = "pdf") -> dict:
        """Etiket üretir.

        `request` GLS NL `CreateLabelRequest` şemasına uyar (camelCase):
            {"reference": "SP26-1", "shiptype": "p",
             "addresses": {"deliveryAddress": {"name1": "...", "street": "...",
                                               "houseNo": "1", "zipCode": "1012AB",
                                               "city": "Amsterdam", "countryCode": "NL",
                                               "addresseeType": "b"}},
             "units": [{"weight": 2.5}],
             "services": {...}}

        `username`/`password`/`customerNo`/`labelType` eksikse tamamlanır.
        Dönüş: {"units": [{"unitNo", "label": "<base64, TEK koli>", ...}],
                "shipmentTrackingLink": "..."}
        Üst seviyede birleşik bir `labels` alanı YOKTUR (2026-08-14 canlıda
        doğrulandı) — her koli kendi PDF'ini `units[i]["label"]` içinde taşır;
        birden çok koliyi tek PDF'te birleştirmek çağıranın işidir.
        """
        payload = dict(request)
        payload.setdefault("labelType", label_type)
        if not payload.get("customerNo"):
            payload["customerNo"] = self.resolve_customer_no()
        return self._post("Label/Create?api-version=1.0", payload)

    def confirm_label(self, unit_no: str, shipping_date: str | None = None,
                      shiptype: str = "p") -> dict:
        """Etiketi gönderildi olarak onaylar (GLS tarafında sevkiyata girer).

        `shiptype` zorunludur (2026-08-14 canlıda doğrulandı — eksikse
        "V008: Shiptype is unknown"); `Label/Create` ile aynı değer ("p").
        """
        return self._post("Label/Confirm?api-version=1.0",
                          {"unitNo": str(unit_no), "shippingDate": shipping_date,
                           "shiptype": shiptype})

    def delete_label(self, unit_no: str) -> dict:
        """Etiketi iptal eder — koli GLS'te artık hiçbir sürece girmez.

        Yanlışlıkla basılan (özellikle test) etiketleri kapatmanın TEK yoludur:
        API ile üretilen sevkiyatlar Print&Ship ekranında hiç görünmez, bu yüzden
        elle silinemezler (GLS Servicedesk, kayıt M2608 0415 / 2026-08-17).

        Koli Track & Trace'te GÖRÜNMEYE DEVAM EDER; silinmez, yalnızca durdurulur.
        Bu yüzden dönen "başarılı" yanıtı takip ekranından teyit etmeye çalışmayın.

        `Label/Confirm` ile aynı imza: yalnızca `unitNo` (2026-08-17'de canlı uçta
        doğrulandı — GLS yazılı bir tanım vermedi, uç geçersiz kimlikle sorgulanıp
        bulundu). Onaylanmış/yola çıkmış koliler için GLS hata döndürür.

        GERİ ALINAMAZ: iptal edilen etiket geri açılamaz, koli gönderilecekse
        yeni etiket basılması gerekir.
        """
        return self._post("Label/Delete?api-version=1.0", {"unitNo": str(unit_no)})

    def get_parcel_shops(self, zip_code: str, country_code: str = "NL",
                         amount: int = MAX_PARCEL_SHOPS, **extra) -> list[dict]:
        """Verilen posta koduna en yakın GLS ParcelShop'lar (en fazla 10)."""
        data = self._post("ParcelShop/GetParcelShops?api-version=1.0", {
            "zipCode": zip_code,
            "countryCode": country_code,
            "amountOfShops": min(int(amount), MAX_PARCEL_SHOPS),
            **extra,
        })
        return data.get("parcelShops") or []
