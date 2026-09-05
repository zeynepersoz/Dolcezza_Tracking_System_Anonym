# -*- coding: utf-8 -*-
"""
GLS Irlanda (ShipIT/IE), GLS Hollanda (NL) ve ShipIT'in ayni REST semasini
paylastigi diger Avrupa ulkeleri (FR/GR/IT/PT/ES/SE) icin ulkeye ozgu
posta kodu dogrulamasi.

Posta kodu bicimleri:
  - Eircode (IE): tek harf yonlendirme kodu + 2 karakter + bosluk + 4 karakterlik
    benzersiz tanimlayici. Ornek: "D02 XY45". Harfler A,C-F,H,K,N,P,R,T,V-Y kumesinden
    gelir (B,G,I,J,L,M,O,Q,S,U,Z routing key'de kullanilmaz) ama pratikte asiri
    kisitlayici bir regex sahada gecerli adresleri reddedebiliyor; bu yuzden
    "harf + 2 alfanumerik + bosluk(opsiyonel) + 4 alfanumerik" bicimini kontrol ediyoruz.
  - NL posta kodu: 4 hane + bosluk(opsiyonel) + 2 harf. Ornek: "1234 AB".
  - FR: 5 hane. Ornek: "75008".
  - GR: 3 hane + bosluk(opsiyonel) + 2 hane. Ornek: "104 32".
  - IT: 5 hane. Ornek: "00144".
  - PT: 4 hane + tire + 3 hane. Ornek: "1000-001".
  - ES: 5 hane. Ornek: "28001".
  - SE: 3 hane + bosluk(opsiyonel) + 2 hane. Ornek: "111 22".

Takip numarasi bicimleri (gercek GLS Group ShipIT / GLS NL portallarindan
gozlemlenen bicimlerle uyumlu):
  - IE / ShipIT (GLS Grup TrackID): 11 haneli sayisal (ornek: 22562472349).
  - NL ParcelNumber: 14 haneli sayisal, musteri numarasi onekiyle baslar
    (ornek: 35000001406746, 38120177007589).
  Ikisi de sayisal (alfanumerik degil) — gercek portallarda gozlenen bicime
  gore daraltildi.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

EIRCODE_RE = re.compile(r"^[A-Za-z]\d[A-Za-z0-9]\s?[A-Za-z0-9]{4}$")
NL_POSTAL_RE = re.compile(r"^\d{4}\s?[A-Za-z]{2}$")

# ShipIT'in ayni REST semasini paylastigi diger Avrupa ulkeleri icin standart
# posta kodu bicimleri (bkz. docs/URUNLESME_YOL_HARITASI.md §2.1). SP27
# sevkiyatlarinda kullanilan ulkeler; IE/NL disinda ilk adaptasyon bu altisi.
FR_POSTAL_RE = re.compile(r"^\d{5}$")            # ornek: 75008
GR_POSTAL_RE = re.compile(r"^\d{3}\s?\d{2}$")     # ornek: 104 32
IT_POSTAL_RE = re.compile(r"^\d{5}$")             # ornek: 00144
PT_POSTAL_RE = re.compile(r"^\d{4}-\d{3}$")       # ornek: 1000-001
ES_POSTAL_RE = re.compile(r"^\d{5}$")             # ornek: 28001
SE_POSTAL_RE = re.compile(r"^\d{3}\s?\d{2}$")     # ornek: 111 22

# Ulke -> (regex, insan-okur hata mesaji, ornek)
COUNTRY_POSTAL_RULES: dict[str, tuple[re.Pattern, str]] = {
    "IE": (EIRCODE_RE, "Eircode bicimine uymuyor (ornek: D02 XY45)"),
    "NL": (NL_POSTAL_RE, "Hollanda posta kodu bicimine uymuyor (ornek: 1234 AB)"),
    "FR": (FR_POSTAL_RE, "Fransa posta kodu bicimine uymuyor (ornek: 75008)"),
    "GR": (GR_POSTAL_RE, "Yunanistan posta kodu bicimine uymuyor (ornek: 104 32)"),
    "IT": (IT_POSTAL_RE, "Italya posta kodu bicimine uymuyor (ornek: 00144)"),
    "PT": (PT_POSTAL_RE, "Portekiz posta kodu bicimine uymuyor (ornek: 1000-001)"),
    "ES": (ES_POSTAL_RE, "Ispanya posta kodu bicimine uymuyor (ornek: 28001)"),
    "SE": (SE_POSTAL_RE, "Isvec posta kodu bicimine uymuyor (ornek: 111 22)"),
}

# Takip numarasi bicimleri — gercek portallarda gozlenen: IE 11 hane, NL 14 hane, ikisi de sayisal.
TRACKING_NO_RE = re.compile(r"^\d{8,14}$")
IE_TRACKING_RE = re.compile(r"^\d{11}$")
NL_TRACKING_RE = re.compile(r"^\d{14}$")

CHANNEL_TRACKING_RULES: dict[str, re.Pattern] = {
    "IE": IE_TRACKING_RE,
    "NL": NL_TRACKING_RE,
}

# Gercek portallarda gorulen adlandirilmis servis ornekleri (ShipIT "Services" listesi).
# Sozlesmeye gore hesap basina degisir; burada bilinen/desteklenen kucuk bir kume tutuyoruz.
KNOWN_SERVICES = ("FlexDeliveryService", "SaturdayDelivery", "GuaranteedDelivery")


class PostalCodeError(ValueError):
    pass


def normalize_country(country: str) -> str:
    return (country or "").strip().upper()[:2]


def validate_postal_code(country: str, postal_code: str) -> str:
    """Ulkeye ozgu bicimi dogrular; gecerliyse (trim edilmis) posta kodunu doner.

    Bilinmeyen/desteklenmeyen ulkeler icin sadece bos-olmama ve makul uzunluk
    kontrolu yapilir (asiri kisitlayici olmamak icin) — IE ve NL disindaki
    ulkeler bu sistemin henuz tam adaptasyonunun disinda.
    """
    postal_code = (postal_code or "").strip()
    country = normalize_country(country)

    rule = COUNTRY_POSTAL_RULES.get(country)
    if rule is None:
        if postal_code and len(postal_code) > 12:
            raise PostalCodeError("Posta kodu cok uzun")
        return postal_code

    pattern, message = rule
    if not postal_code or not pattern.match(postal_code):
        raise PostalCodeError(f"{message} — girilen: '{postal_code}'")
    return postal_code.upper() if country == "IE" else postal_code.upper()


def validate_tracking_no(channel: str, tracking_no: str) -> str:
    """Kanala (IE/NL) ozgu takip numarasi bicimini dogrular."""
    tracking_no = (tracking_no or "").strip()
    pattern = CHANNEL_TRACKING_RULES.get(normalize_country(channel), TRACKING_NO_RE)
    if not pattern.match(tracking_no):
        raise ValueError(f"Gecersiz takip numarasi bicimi: '{tracking_no}'")
    return tracking_no


class AddressIn(BaseModel):
    """Adres defteri kaydi icin girdi dogrulamasi.

    Alan yapisi GLS NL (printship.gls.nl) ve GLS Group ShipIT web
    portallarindaki gercek adres formuyla uyumlu: Name/Name2/Name3, Street +
    House Number + Addition ayri alanlar, Address Type (business/private),
    Consignee ID (GLS'e kayitli alici referans kodu), Phone + Mobile ayri.
    """

    name: str
    name2: str = ""
    name3: str = ""
    company: str = ""
    address_type: str = "business"
    street: str = ""
    house_number: str = ""
    addition: str = ""
    postal_code: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    consignee_id: str = ""
    contact: str = ""
    phone: str = ""
    mobile: str = ""
    email: str = ""

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Ad alani zorunlu")
        return v

    @field_validator("address_type")
    @classmethod
    def _address_type_ok(cls, v: str) -> str:
        v = (v or "business").strip().lower()
        if v not in ("business", "private"):
            raise ValueError("Adres tipi 'business' veya 'private' olmali")
        return v

    @field_validator("country")
    @classmethod
    def _country_fmt(cls, v: str) -> str:
        return normalize_country(v)

    @field_validator("email")
    @classmethod
    def _email_fmt(cls, v: str) -> str:
        v = (v or "").strip()
        if v and ("@" not in v or " " in v or v.startswith("@") or v.endswith("@")):
            raise ValueError("Gecersiz e-posta bicimi")
        return v

    @model_validator(mode="after")
    def _postal_matches_country(self) -> "AddressIn":
        # Sadece IE/NL icin bicim zorunlu; posta kodu bos birakilmissa (ulke henuz
        # secilmemis olabilir) burada zorlamiyoruz — GLS API cagrisi asamasinda
        # zaten Eircode/NL kontrolu tekrar calisir (bkz. validate_postal_code).
        if self.country in COUNTRY_POSTAL_RULES and self.postal_code:
            self.postal_code = validate_postal_code(self.country, self.postal_code)
        return self


class LabelCreateIn(BaseModel):
    """Etiket olusturma formu icin dogrulama (kanal secimine gore posta kodu kontrolu).

    Not: gonderici (shipper) formda secilmez — gercek ShipIT/NL portallarinda
    oldugu gibi sozlesmeye bagli sabit bir adres olarak Adres Defteri'nde
    isaretlenir (AddressBook.get_default_shipper()); burada sadece alici
    (consignee_id) adres defterinden secilir.
    """

    channel: str
    consignee_id: int = 0  # sadece provenance icin; gercek alici verisi ayrica AddressIn ile dogrulanir
    reference: str = ""
    # Koli SAYISI ayri bir alan degil: her kolinin kendi agirligi var, sayi da
    # listenin uzunlugudur. Tek agirligi koli sayisina yaymak yanlis navlun
    # demekti (toplu akis bunu `dispatch/engine.py`de zaten koli basina yolluyor).
    weights: list[float] = [1.0]
    services: list[str] = []

    @field_validator("services")
    @classmethod
    def _services_known(cls, v: list[str]) -> list[str]:
        unknown = [s for s in v if s not in KNOWN_SERVICES]
        if unknown:
            raise ValueError(f"Bilinmeyen servis: {', '.join(unknown)}")
        return v

    @field_validator("channel")
    @classmethod
    def _channel_ok(cls, v: str) -> str:
        v = normalize_country(v)
        if v not in ("NL", "IE"):
            raise ValueError("Kanal NL veya IE olmali")
        return v

    @field_validator("weights")
    @classmethod
    def _weights_ok(cls, v: list[float]) -> list[float]:
        if not (1 <= len(v) <= 99):
            raise ValueError("Koli sayisi 1-99 arasinda olmali")
        if any(not (0.01 <= w <= 999) for w in v):
            raise ValueError("Agirlik 0.01-999 kg arasinda olmali")
        return v
