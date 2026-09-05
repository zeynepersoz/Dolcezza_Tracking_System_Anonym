# -*- coding: utf-8 -*-
"""
GLS ShipIT REST istemcisi — etiket + takip + POD (PDF).

Uç noktalar (taban URL müşteriye özel olarak GLS'ten gelir, ör.
`https://<host>.gls-group.eu:8443/backend`):

    POST {base}/rs/shipments                       -> parça oluştur + etiket (PDF)
    POST {base}/rs/shipments/allowedservices       -> adres için izinli servisler
    POST {base}/rs/shipments/cancel/{parcelNumber} -> basılmamış parçayı iptal et
    POST {base}/rs/shipments/endofday              -> gün sonu raporu (manifesto)
    POST {base}/rs/tracking/parcels                -> tarih aralığında parça ara
    POST {base}/rs/tracking/parceldetails          -> takip geçmişi + teslim + imza
    POST {base}/rs/tracking/parcelpod              -> Teslimat Kanıtı (Base64 PDF)

Kimlik doğrulama: HTTP Basic (kullanıcı GLS Field IT tarafından verilir).
Zorunlu başlıklar: Accept/Content-Type = application/glsVersion1+json

DİKKAT — ShipIT'in iki alışılmadık davranışı:
 1) Hata durumunda gövde BOŞ gelir; hata metni `message` / `error` / `args`
    HTTP *başlıklarındadır*. Gövdeyi JSON diye ayrıştırmak boşuna.
 2) GLS'e özgü HTTP 490 kodu kullanılır (standart dışı) — "iş kuralı reddi".

Ayrıca: bir parça `endofday` (gün sonu raporu) çalıştırılmadan takip sisteminde
görünmez. "Parça bulunamadı" hatalarının en yaygın sebebi budur.
"""
import base64
import time
from pathlib import Path
from urllib.parse import quote

import requests

from . import config


class ShipITError(Exception):
    """ShipIT hatası. `status` HTTP kodu, `code` GLS hata anahtarı, `gls_args` GLS argümanları."""

    def __init__(self, message: str, status: int | None = None,
                 code: str = "", gls_args: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code
        self.gls_args = gls_args


class ShipITClient:
    def __init__(self, base_url=None, username=None, password=None, contact_id=None):
        self.base_url = (base_url or config.SHIPIT_BASE_URL).rstrip("/")
        self.auth = (username or config.SHIPIT_USERNAME,
                     password or config.SHIPIT_PASSWORD)
        self.contact_id = contact_id if contact_id is not None else config.SHIPIT_CONTACT_ID
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/glsVersion1+json, application/json",
            "Content-Type": "application/glsVersion1+json",
        })
        self._last_call = 0.0

    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict | None = None) -> dict:
        # Nazik hız sınırı: sunucuyu yormamak kurumsal iyi niyetin parçası
        wait = config.RATE_LIMIT_SECONDS - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

        url = f"{self.base_url}{path}"
        try:
            resp = self.session.post(url, json=payload if payload is not None else {},
                                     auth=self.auth, timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise ShipITError(f"Ağ hatası ({url}): {e}")

        if resp.status_code not in (200, 201):
            raise self._error_from(resp, url)

        if not resp.content:
            # Basarili ama govdesiz (or. bos arama sonucu) — bos sozluk esdeger.
            return {}
        try:
            return resp.json()
        except ValueError:
            raise ShipITError(f"Geçersiz JSON yanıtı ({url}): {resp.text[:200]}",
                              status=resp.status_code)

    @staticmethod
    def _error_from(resp: requests.Response, url: str) -> ShipITError:
        """ShipIT hata gövdesi boştur; asıl bilgi HTTP başlıklarındadır."""
        h = resp.headers
        message = h.get("message") or ""
        code = h.get("error") or ""
        gls_args = h.get("args") or ""
        if not message:
            # Nadiren govdede metin gelebilir (vekil sunucu/hata sayfasi) — yine de bak.
            message = (resp.text or "").strip()[:300]

        detail = message or "(sunucu hata metni vermedi)"
        if gls_args:
            detail = f"{detail} [{gls_args}]"
        if code:
            detail = f"{code}: {detail}"

        if resp.status_code == 401:
            detail += " — SHIPIT_USERNAME/SHIPIT_PASSWORD hatalı olabilir"
        elif resp.status_code == 490:
            # GLS'e ozgu, standart disi kod: istek bicimsel olarak dogru ama is kurali reddetti.
            detail += " — GLS iş kuralı reddi (HTTP 490)"

        return ShipITError(f"{url} -> HTTP {resp.status_code}: {detail}",
                           status=resp.status_code, code=code, gls_args=gls_args)

    # ==================================================================
    # Takip
    # ==================================================================
    def parcel_details(self, track_id: str) -> dict:
        """Takip geçmişi + teslim edildiyse teslim tarihi ve imza.

        Yanıt kökü `UnitDetail`'dir (eski dokümanlardaki `TUDetail` DEĞİL).
        """
        return self._post("/rs/tracking/parceldetails", {"TrackID": str(track_id)})

    def parcel_pod(self, track_id: str) -> bytes:
        """Teslimat Kanıtı PDF'ini ham byte olarak döndürür.

        Yanıt: {"PODItem": {"ImageData": "<base64 PDF>", ...}}
        """
        data = self._post("/rs/tracking/parcelpod", {"TrackID": str(track_id)})
        item = data.get("PODItem") or {}
        image_b64 = item.get("ImageData")
        if not image_b64:
            raise ShipITError(
                f"POD dönmedi ({track_id}) — parça henüz teslim edilmemiş olabilir "
                f"ya da gün sonu raporu (endofday) çalışmamıştır. Yanıt: {str(data)[:200]}"
            )
        return base64.b64decode(image_b64)

    def find_parcels(self, date_from: str, date_to: str, **extra) -> dict:
        """Tarih aralığında parça arar (YYYY-MM-DD). Sonuç yoksa boş sözlük döner.

        Parçalar `end_of_day_report()` çalıştırılana kadar burada görünmez.
        """
        payload = {"DateFrom": date_from, "DateTo": date_to, **extra}
        return self._post("/rs/tracking/parcels", payload)

    # ==================================================================
    # Etiket / sevkiyat
    # ==================================================================
    @staticmethod
    def _printing_options(label_format: str = "PDF", template_set: str = "NONE") -> dict:
        """ShipIT'te ZORUNLU. Eksikse sunucu etiket üretmez (ya da 490 döner)."""
        return {"ReturnLabels": {"TemplateSet": template_set, "LabelFormat": label_format}}

    def create_parcels(self, shipment: dict, label_format: str = "PDF") -> dict:
        """Sevkiyat oluşturur, parça numaralarını ve etiketi (Base64) döndürür.

        `shipment` gövdesi ShipIT `Shipment` şemasına uyar:
            {"Shipment": {"ShipperAccount": {...}, "Shipper": {...},
                          "Consignee": {...}, "ShipmentUnit": [{"Weight": 2.5}], ...}}
        `PrintingOptions` yoksa (zorunlu olduğu için) otomatik eklenir.
        """
        # SEMA (shipit-farm.yaml, ShipmentRequestData):
        #   PrintingOptions -> `Shipment` ile KARDES, ICINDE degil
        #   ContactID       -> `Shipment.Shipper.ContactID`
        # Onceki hali ikisini de yanlis yere koyuyordu ve semada HIC OLMAYAN
        # bir `ShipperAccount` alani uyduruyordu. Bu kod canlida hic
        # calismadigi icin (ShipIT yapilandirilmamisti) fark edilmemisti.
        body = dict(shipment)
        body.setdefault("PrintingOptions", self._printing_options(label_format))
        inner = body.get("Shipment")
        if isinstance(inner, dict):
            inner = dict(inner)
            shipper = dict(inner.get("Shipper") or {})
            if not (shipper.get("ContactID") or "").strip() and self.contact_id:
                shipper["ContactID"] = self.contact_id
            if shipper:
                inner["Shipper"] = shipper
            body["Shipment"] = inner
        return self._post("/rs/shipments", body)

    def create_parcel_labels_pdf(self, shipment: dict) -> bytes:
        """Kısayol: sevkiyatı yaratır ve ilk PrintData'yı PDF byte olarak döner."""
        result = self.create_parcels(shipment)
        try:
            data_b64 = result["CreatedShipment"]["PrintData"][0]["Data"]
        except (KeyError, IndexError, TypeError):
            raise ShipITError(f"ShipIT yanıtında PrintData yok: {str(result)[:300]}")
        return base64.b64decode(data_b64)

    def allowed_services(self, shipper_address: dict, consignee_address: dict) -> dict:
        """Verilen gönderici/alıcı çifti için izinli ürün ve servisler.

        Hafif ve yan etkisiz olduğu için bağlantı/kimlik testi olarak da kullanılır
        (bkz. gls_api/providers.py:doctor).
        """
        return self._post("/rs/shipments/allowedservices", {
            "Shipment": {
                "Shipper": {"Address": shipper_address},
                "Consignee": {"Address": consignee_address},
            }
        })

    def cancel_parcel(self, parcel_number: str) -> dict:
        """Henüz gün sonu raporuna girmemiş bir parçayı iptal eder."""
        return self._post(f"/rs/shipments/cancel/{quote(str(parcel_number), safe='')}")

    def end_of_day_report(self, **extra) -> dict:
        """Gün sonu raporu (manifesto). ÇALIŞTIRILMADAN parçalar takipte görünmez."""
        return self._post("/rs/shipments/endofday", extra or {})

    # ------------------------------------------------------------------
    def save_pod(self, track_id: str, target_path: Path) -> Path:
        pdf_bytes = self.parcel_pod(track_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(pdf_bytes)
        return target_path
