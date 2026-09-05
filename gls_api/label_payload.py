# -*- coding: utf-8 -*-
"""Ic adres sozlugunu GLS'in bekledigi etiket govdelerine cevirir.

Tek etiket (web/routers/labels.py) ve toplu etiket (dispatch/engine.py) akislari
ayni eslemeyi kullansin diye burada duruyor — iki yerde birbirinden sapan iki
kopya olusmasin.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Formdaki servis adlari ShipIT adlandirmasindadir; GLS NL ayni servisleri
# farkli anahtarlarla ister. Karsiligi olmayanlar NL'de sessizce atlanir.
NL_SERVICE_KEYS = {"SaturdayDelivery": "saturdayService"}

# GLS NL `houseNo` alani en fazla 5 karakter ("The value HouseNo cannot exceed
# 5 characters." -> V009, sevkiyati komple reddeder). Kaynak ayristirmasi
# (`erp.addresses.split_house_number`) bunu zaten kurtariyor; bu SON savunma:
# elle girilen ya da kurtarilamayan bir deger tum toplu isi patlatmasin
# (canli olay 2026-09-03: PVSDE1N "1606/07").
_NL_HOUSE_NO_MAX = 5


def _clamp_house_no(house_no: str, house_ext: str) -> tuple[str, str]:
    """`houseNo`yu 5 karaktere sigdirir; tasan kismi `houseNoExt`e devreder."""
    house_no = (house_no or "").strip()
    house_ext = (house_ext or "").strip()
    if len(house_no) <= _NL_HOUSE_NO_MAX:
        return house_no, house_ext
    m = re.match(r"\s*(\d{1,5})\s*(.*)$", house_no)
    if m and m.group(1):
        overflow = (m.group(2).strip() + " " + house_ext).strip()
        log.warning("houseNo '%s' 5 karakteri asti -> No='%s', Ext='%s'",
                    house_no, m.group(1), overflow)
        return m.group(1), overflow
    log.warning("houseNo '%s' 5 karakteri asti, sayisal bas yok -> kirpildi", house_no)
    return house_no[:_NL_HOUSE_NO_MAX], house_ext

# GLS NL alanlari uzunluk asiminda ETIKETI KIRPMAZ, 400 ile REDDEDER
# ("The value Name1 cannot exceed 30 characters."). Sinirlar 2026-08-17'de canli
# ucta olculdu. Musterilerimizin 261'inden 28'inin unvani 30 karakteri asiyor
# (orn. "Lili Mode-Boutique am Goldenen Reiter") — kirpmazsak o sevkiyatlar
# toplu etikette tek tek patlar. Adres kesilmesin diye once evrak alanlari
# (name/contact) kirpilir; sokak/sehir zaten sinirin altinda kaliyor.
NL_MAX = {"name1": 30, "name2": 30, "name3": 30, "contact": 30,
          "street": 40, "city": 30}
NL_REFERENCE_MAX = 20


def _fit(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


def split_long_name(full: str) -> tuple[str, str]:
    """Unvani `name1` + `contact` olarak ikiye boler.

    Etiketin sol alt kosesinde "Contact:" satiri zaten unvanin yankisi olarak
    basiliyor. Unvan 30 karaktere sigiyorsa iki alana da tamami yazilir; sigmi-
    yorsa BASTAN KIRPMAK YERINE tasan kismi contact'a devrederiz — boylece
    "Lili Mode-Boutique am Goldenen Reiter" etikette eksiksiz okunur.

    Bolme KELIME sinirindan yapilir; ilk 30 karakterde bosluk yoksa (tek uzun
    kelime) sert kesilir, cunku alternatifi GLS'in etiketi reddetmesidir.
    """
    full = (full or "").strip()
    limit = NL_MAX["name1"]
    if len(full) <= limit:
        return full, full

    cut = full.rfind(" ", 0, limit + 1)
    if cut <= 0:
        cut = limit
    return full[:cut].strip(), _fit(full[cut:], NL_MAX["contact"])


# GLS'in `PickupAddress` icin ZORUNLU tuttugu alanlar. Bos gonderirsek istek
# "V009: ... The Name1 field is required.; ... Street ...; ... City ...;
# ... ZipCode ..." diye reddedilir (canli olcum, 2026-09-01: varsayilan
# gonderici hic ayarlanmamisti, uc sevkiyat da bu yuzden patladi ve kullanici
# yalnizca "gecersiz veri iceriyor" gordu). Cagiran taraf bunu ONCEDEN sorar.
SHIPPER_REQUIRED_FIELDS = ("name1", "street", "city", "zipCode")


def missing_shipper_fields(shipper: dict | None) -> list[str]:
    """Gonderici adresinde GLS'in zorunlu tuttugu eksik alanlar; tamsa bos liste.

    Adres HENUZ GLS semasina cevrilmeden once cagrilabilsin diye donusumu
    kendisi yapar — cagiran tarafta ikinci bir eslestirme kurali olmasin.
    """
    if not shipper:
        return list(SHIPPER_REQUIRED_FIELDS)
    mapped = address_to_nl(shipper)
    return [f for f in SHIPPER_REQUIRED_FIELDS if not (mapped.get(f) or "").strip()]


def address_to_nl(addr: dict) -> dict:
    """GLS NL `PickupAddress`/`DeliveryAddress` semasi (camelCase, C# SDK ile birebir)."""
    full = (addr.get("company") or addr.get("name") or "").strip()
    name1, from_name = split_long_name(full)
    # Adres defterinde gercek bir irtibat kisisi varsa o korunur; yoksa (ya da
    # cagiran unvanin kendisini verdiyse) unvanin devami yazilir.
    given = (addr.get("contact") or "").strip()
    contact = from_name if not given or given == full else given
    house_no, house_ext = _clamp_house_no(addr.get("house_number"), addr.get("addition"))
    return {
        "name1": name1,
        "name2": _fit(addr.get("name2"), NL_MAX["name2"]),
        "name3": _fit(addr.get("name3"), NL_MAX["name3"]),
        "street": _fit(addr.get("street"), NL_MAX["street"]),
        "houseNo": house_no,
        "houseNoExt": house_ext,
        "zipCode": addr.get("postal_code") or "",
        "city": _fit(addr.get("city"), NL_MAX["city"]),
        "countryCode": (addr.get("country") or "NL")[:2].upper(),
        "contact": _fit(contact, NL_MAX["contact"]),
        "phone": addr.get("phone") or addr.get("mobile") or "",
        "email": addr.get("email") or "",
    }


def address_to_shipit(addr: dict) -> dict:
    """GLS ShipIT `Address` semasi — alan adlari resmi OpenAPI dosyasindan.

    Adlar SESSIZ VERI KAYBI kaynagiydi: bu builder hic calistirilmamisti
    (ShipIT canlida hic yapilandirilmadi) ve dort alan semayla uyusmuyordu —
    `ZIPCode`/`eMail`/`Phone`/`Mobile` yerine semada `Zipcode` / `Email` /
    `FixedLinePhonenumber` / `MobilePhoneNumber` var. Yanlis adlar hata
    vermeden ATILIRDI: etiket posta kodsuz basardi.

    `ConsigneeID` buraya AIT DEGIL — semada `Consignee`nin `Address` ile
    KARDES alani (bkz. `build_shipit_payload`).
    """
    return {
        "Name1": addr.get("company") or addr.get("name") or "",
        "Name2": addr.get("name2") or "",
        "Name3": addr.get("name3") or "",
        "Street": addr.get("street") or "",
        "StreetNumber": addr.get("house_number") or "",
        "Zipcode": addr.get("postal_code") or "",
        "City": addr.get("city") or "",
        "CountryCode": (addr.get("country") or "IE")[:2].upper(),
        "ContactPerson": addr.get("contact") or "",
        "Email": addr.get("email") or "",
        "FixedLinePhonenumber": addr.get("phone") or "",
        "MobilePhoneNumber": addr.get("mobile") or "",
    }


def _reference_lines(reference: str, note1: str = "", note2: str = "") -> list[str]:
    """Etikete basilacak referans satirlari: referans + Not1 + Not2 (bos olanlar atlanir).

    Kullanici karari (2026-08-20): Not1/Not2 GLS etiketine ek referans satiri
    olarak basilir. Hicbiri yoksa GLS'in bos referansi reddetmemesi icin
    "WEB-LABEL"e duselir.
    """
    lines = [(reference or "").strip(), (note1 or "").strip(), (note2 or "").strip()]
    lines = [line for line in lines if line]
    return lines or ["WEB-LABEL"]


def build_nl_payload(shipper: dict, consignee: dict, reference: str,
                     package_count: int = 1, weight_kg: float = 1.0,
                     services: list[str] | None = None,
                     units: list[dict] | None = None,
                     note1: str = "", note2: str = "") -> dict:
    """GLS NL `Label/Create` govdesi.

    `units` verilirse koli basina AYRI agirlik (ve toplu akista `unitId`)
    gonderilir; verilmezse `package_count` adet esit agirlikli koli uretilir.
    `unitId` toplu akista Sentez koli barkodudur (`Erp_Box.BoxCode`): GLS yanitinda
    aynen geri geldigi icin hangi takip numarasinin hangi koliye ait oldugu
    tahmin edilmeden bilinir.

    Not1/Not2: NL tek `reference` alani veriyor (cok satir yok), bu yuzden
    referans+notlar bosluklu birlestirilip 20 karaktere kirpilir — bilincli
    kayip; ShipIT'te her biri ayri satir olur.
    """
    if units is None:
        units = [{"weight": weight_kg} for _ in range(package_count)]
    joined = " ".join(_reference_lines(reference, note1, note2))
    return {
        # Etiketin "Ref." satiri. GLS 20 karakteri asarsa etiketi REDDEDER, ve
        # birlestirilmis sevkiyatta referans "fatura1+fatura2" oldugu icin iki
        # uzun fatura numarasi bu siniri asabiliyor.
        "reference": _fit(joined, NL_REFERENCE_MAX) or "WEB-LABEL",
        "shiptype": "p",
        "addresses": {
            "pickupAddress": address_to_nl(shipper),
            "deliveryAddress": {**address_to_nl(consignee), "addresseeType": "b"},
        },
        # `unitType` GONDERILMEZ: gercek GLS NL API'si "parcel"/"Parcel" ne
        # gelirse gelsin V009 hatasiyla reddediyor (2026-08-14 canli dogrulandi,
        # mock sunucu bu alani hic denetlemedigi icin sorun testte gorunmuyordu).
        "units": list(units),
        "services": {NL_SERVICE_KEYS[s]: True
                     for s in (services or []) if s in NL_SERVICE_KEYS},
    }


def build_shipit_payload(shipper: dict, consignee: dict, reference: str,
                         package_count: int = 1, weight_kg: float = 1.0,
                         services: list[str] | None = None,
                         units: list[dict] | None = None,
                         note1: str = "", note2: str = "",
                         contact_id: str = "") -> dict:
    """GLS ShipIT `shipments` govdesi.

    `units` NL ile ayni ic bicimdedir (`{"weight": ...}`) — iki kanal tek formdan
    besleniyor, cagiran tarafta bicim ayrimi olmasin diye.

    Not1/Not2: `ShipmentReference` zaten bir DIZI — referans + Not1 + Not2 ayri
    satirlar olarak etikete basilir (bos olanlar atlanir).
    """
    if units is None:
        units = [{"weight": weight_kg} for _ in range(package_count)]
    shipment = {
        "Product": "PARCEL",
        # `ContactID` semada ZORUNLU ve hangi GLS hesabina basilacagini O secer
        # (GLS Ireland, 2026-09-01: "the account that you create a label on
        # depends on the actual ContactID"). Tek API anahtari birden fazla
        # hesabi kapsadigi icin dogru hesap YALNIZCA bu alanla ayirt edilir.
        # Bos birakilabilir: istemci yapilandirilmis ContactID ile doldurur
        # (bkz. ShipITClient.create_parcels). Bu modul BILEREK `config`e
        # bagimsizdir — saf yuk uretici, testte ortam gerektirmez.
        "Shipper": {"ContactID": contact_id or "",
                    "Address": address_to_shipit(shipper)},
        "Consignee": {"Address": address_to_shipit(consignee)},
        "ShipmentUnit": [{"Weight": u.get("weight")} for u in units],
        "ShipmentReference": _reference_lines(reference, note1, note2),
    }
    # `ConsigneeID` adresin ICINDE degil, `Consignee`nin kendi alani (sema).
    consignee_id = (consignee.get("consignee_id") or "").strip()
    if consignee_id:
        shipment["Consignee"]["ConsigneeID"] = consignee_id
    if services:
        # Sema: ShipmentService -> Service -> ServiceName. Onceki hali
        # `{"ServiceType": ...}` idi; boyle bir alan semada YOK.
        shipment["Service"] = [{"Service": {"ServiceName": s}} for s in services]
    return {
        "Shipment": shipment,
        # `ShipmentRequestData`da ZORUNLU; hic gonderilmiyordu.
        "PrintingOptions": {"ReturnLabels": {"TemplateSet": "NONE",
                                             "LabelFormat": "PDF"}},
    }
