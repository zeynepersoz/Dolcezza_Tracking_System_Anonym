# -*- coding: utf-8 -*-
"""
GLS Track & Trace SOAP istemcisi — sadece TAKİP + POD (etiket üretmez).

Uç nokta grup çapında sabittir:
    https://www.gls-group.eu/276-I-PORTAL-WEBSERVICE/services/Tracking

Kimlik: gls-group.eu **Uni-Portal** kullanıcı adı/şifresi (ShipIT kimliğinden farklıdır).

Kullanılan işlemler:
    GetTuDetail  -> takip geçmişi, teslim tarihi, imza adı
    GetTuPOD     -> Teslimat Kanıtı (PODFile = Base64, PODFileName uzantıyı verir)
    GetTuList    -> tarih aralığında parça listesi

`zeep` gibi ağır bir SOAP bağımlılığı YOK: istekler elle kurulur, yanıtlar
`xml.etree` ile ad-alanından bağımsız (localname) ayrıştırılır. Böylece GLS
kurulumdan kuruluma namespace değiştirse bile kod çalışmaya devam eder.

GLS'in şemasındaki yazım hataları bilinçli olarak korunur:
  - `<Minut>` (dakika alanı — "Minute" değil)

Not: Toplu `GetTusDetails` işlemi WSDL'de mevcuttur ama istek şeması kurulumdan
kuruluma değiştiği için burada uygulanmadı; `get_tu_detail` döngüsü kullanılır.
"""
from __future__ import annotations

import base64
import time
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import requests

from . import config

NS = "http://gls-group.eu/Tracking/"

# ExitCode/ErrorCode esleme (referans SDK: Webit\GlsTracking\Model\ExitCode)
EXIT_OK = 0
EXIT_AUTH_ERROR = 502
EXIT_NO_DATA = 998
EXIT_IMAGE_NOT_FOUND = 999

_EXIT_MESSAGES = {
    EXIT_AUTH_ERROR: "Kimlik doğrulama hatası — TT_USERNAME/TT_PASSWORD hatalı "
                     "(gls-group.eu Uni-Portal kullanıcısı olmalı)",
    EXIT_NO_DATA: "Bu takip numarası için veri yok (parça henüz sisteme girmemiş olabilir)",
    EXIT_IMAGE_NOT_FOUND: "İmza görüntüsü yok — parça teslim edilmemiş ya da bu ülkede "
                          "POD paylaşımı hesabınıza açılmamış olabilir",
}


class TrackTraceError(Exception):
    """Track&Trace hatası. `exit_code` GLS ErrorCode'u (0/502/998/999)."""

    def __init__(self, message: str, exit_code: int | None = None):
        super().__init__(message)
        self.exit_code = exit_code

    @property
    def is_no_data(self) -> bool:
        return self.exit_code == EXIT_NO_DATA

    @property
    def is_auth_error(self) -> bool:
        return self.exit_code == EXIT_AUTH_ERROR


def _local(tag: str) -> str:
    """'{ns}Name' -> 'Name' (ad-alanından bağımsız eşleme)."""
    return tag.rsplit("}", 1)[-1]


def _find(node, name: str):
    """Alt ağaçta localname'i `name` olan ilk düğüm."""
    for child in node.iter():
        if _local(child.tag) == name:
            return child
    return None


def _find_all(node, name: str) -> list:
    return [c for c in node.iter() if _local(c.tag) == name]


def _text(node, name: str, default: str = "") -> str:
    found = _find(node, name)
    return (found.text or default).strip() if found is not None and found.text else default


def _datetime_str(node) -> str:
    """GLS DateTime yapısı -> 'YYYY-MM-DD HH:MM'. Yoksa boş string.

    Dakika alanının adı GLS şemasında `Minut` (yazım hatası) — bilinçli.
    """
    if node is None:
        return ""
    y, m, d = _text(node, "Year"), _text(node, "Month"), _text(node, "Day")
    if not y or not m or not d:
        return ""
    stamp = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    hour, minute = _text(node, "Hour"), _text(node, "Minut")
    if hour:
        stamp += f" {int(hour):02d}:{int(minute or 0):02d}"
    return stamp


class TrackTraceClient:
    def __init__(self, endpoint=None, username=None, password=None, language="EN"):
        self.endpoint = (endpoint or config.TT_ENDPOINT).rstrip("/")
        self.username = username or config.TT_USERNAME
        self.password = password or config.TT_PASSWORD
        self.language = language
        self.session = requests.Session()
        self._last_call = 0.0

    # ------------------------------------------------------------------
    @staticmethod
    def _ref(reference: str) -> str:
        """RefValue 11 haneye kırpılır — GLS takip numaraları 11 hanedir ve
        barkoddan okunan 12./13. hane kontrol basamağıdır (referans SDK ile aynı)."""
        return str(reference).strip()[:11]

    def _envelope(self, operation: str, body_inner: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            f'xmlns:gls="{NS}">'
            "<soapenv:Body>"
            f"<gls:{operation}>"
            "<gls:Credentials>"
            f"<gls:UserName>{escape(self.username)}</gls:UserName>"
            f"<gls:Password>{escape(self.password)}</gls:Password>"
            "</gls:Credentials>"
            f"{body_inner}"
            "<gls:Parameters>"
            "<gls:ParamCode>LangCode</gls:ParamCode>"
            f"<gls:ParamValue>{escape(self.language)}</gls:ParamValue>"
            "</gls:Parameters>"
            f"</gls:{operation}>"
            "</soapenv:Body></soapenv:Envelope>"
        )

    def _call(self, operation: str, body_inner: str):
        wait = config.RATE_LIMIT_SECONDS - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

        xml = self._envelope(operation, body_inner)
        try:
            resp = self.session.post(
                self.endpoint,
                data=xml.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8",
                         "SOAPAction": f'"{operation}"'},
                timeout=config.REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise TrackTraceError(f"Ağ hatası ({self.endpoint}): {e}")

        if resp.status_code != 200:
            raise TrackTraceError(
                f"{self.endpoint} -> HTTP {resp.status_code}: {resp.text[:300]}"
            )

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            raise TrackTraceError(f"SOAP yanıtı ayrıştırılamadı: {e} / {resp.text[:200]}")

        fault = _find(root, "Fault")
        if fault is not None:
            raise TrackTraceError(
                "SOAP Fault: " + (_text(fault, "faultstring") or _text(fault, "Reason") or "?")
            )

        exit_node = _find(root, "ExitCode")
        code = int(_text(exit_node, "ErrorCode", "0") or 0) if exit_node is not None else 0
        if code != EXIT_OK:
            description = _text(exit_node, "ErrorDscr") if exit_node is not None else ""
            hint = _EXIT_MESSAGES.get(code, "")
            detail = " — ".join(p for p in (description, hint) if p) or "bilinmeyen hata"
            raise TrackTraceError(f"GLS Track&Trace ({operation}) ExitCode {code}: {detail}",
                                  exit_code=code)
        return root

    # ==================================================================
    def get_tu_detail(self, reference: str) -> dict:
        """Takip geçmişi + teslim bilgisi.

        Dönüş: {"tracking_no", "delivered_at", "picked_up_at", "signature",
                "product", "weight_kg", "consignee", "history": [{date, code,
                description, location, country, reason}]}
        """
        root = self._call("GetTuDetail",
                          f"<gls:RefValue>{escape(self._ref(reference))}</gls:RefValue>")

        history = []
        for evt in _find_all(root, "History"):
            history.append({
                "date": _datetime_str(_find(evt, "Date")),
                "code": _text(evt, "Code"),
                "description": _text(evt, "Desc"),
                "location": _text(evt, "LocationName") or _text(evt, "LocationCode"),
                "country": _text(evt, "CountryName"),
                "reason": _text(evt, "ReasonName"),
            })
        history.sort(key=lambda e: e["date"])

        weight = _text(root, "TuWeight")
        consignee = _find(root, "ConsigneeAddress")
        return {
            "tracking_no": _text(root, "TuNo") or self._ref(reference),
            "national_ref": _text(root, "NationalRef"),
            "delivered_at": _datetime_str(_find(root, "DeliveryDateTime")),
            "picked_up_at": _datetime_str(_find(root, "PickupDateTime")),
            "signature": _text(root, "Signature"),
            "product": _text(root, "Product"),
            "weight_kg": float(weight) if weight else None,
            "consignee": _text(consignee, "Name1") if consignee is not None else "",
            "history": history,
        }

    def get_tu_pod(self, reference: str) -> tuple[bytes, str]:
        """Teslimat Kanıtı. Dönüş: (dosya_baytları, dosya_adı).

        Dosya adının uzantısı PDF ya da görüntü (PNG/TIFF) olabilir — çağıran
        taraf uzantıya bakarak medya tipini belirler.
        """
        root = self._call("GetTuPOD",
                          f"<gls:RefValue>{escape(self._ref(reference))}</gls:RefValue>")
        data_b64 = _text(root, "PODFile")
        if not data_b64:
            raise TrackTraceError(f"POD dosyası boş döndü: {reference}",
                                  exit_code=EXIT_IMAGE_NOT_FOUND)
        filename = _text(root, "PODFileName") or f"{self._ref(reference)}.pdf"
        return base64.b64decode(data_b64), filename

    def get_tu_list(self, date_from: str, date_to: str, customer_ref: str = "") -> list[dict]:
        """Tarih aralığındaki parçalar (YYYY-MM-DD). Veri yoksa boş liste."""
        inner = (
            f"<gls:DateFrom>{escape(date_from)}</gls:DateFrom>"
            f"<gls:DateTo>{escape(date_to)}</gls:DateTo>"
        )
        if customer_ref:
            inner += f"<gls:CustomRef>{escape(customer_ref)}</gls:CustomRef>"
        try:
            root = self._call("GetTuList", inner)
        except TrackTraceError as e:
            if e.is_no_data:
                return []
            raise

        return [{
            "tracking_no": _text(row, "RefNo"),
            "created_at": _datetime_str(_find(row, "InitialDateTime")),
            "event_code": _text(row, "EvtCodeNo"),
            "reason_code": _text(row, "EvtReasonNo"),
            "country": _text(row, "CountryCode"),
            "zip_code": _text(row, "ZipCode"),
            "city": _text(row, "City"),
            "consignee_name": _text(row, "ConsigneeName"),
            "status": _text(row, "CurrentStatus"),
        } for row in _find_all(root, "TUList")]
