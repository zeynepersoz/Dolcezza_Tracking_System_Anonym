"""Alici adreslerini Sentez `Erp_Address`ten okur (salt okuma).

Kullanici karari (2026-08-20): yerel adres defteri yerine alici adresleri
CANLIDA dogrudan ERP'den gelir — `CurrentAccountId = 5353` GLS hesabinin altindaki
magaza/ajans adresleri. Mock/test'te yerel defter kullanilir (bkz.
`web/address_source.py`), boylece gelistirme makinesi MSSQL istemez.

ONEMLI — veri yapisi: `Erp_Address` adresleri SERBEST METINDIR. Posta kodu,
sokak, sehir AYRI kolonlarda DEGIL; hepsi `Line1/Line2/Line3` icinde. Ad
`Explanation`de, magaza kodu `AddressCode`te, ulke `CountryId` -> `Meta_Country`
uzerinden 2 harfli koda cozulur. 2308 kaydin hicbirinde yapilandirilmis posta
kodu YOK. Bu yuzden etiket akisi "duzenlenebilir on-doldurma"dir: kullanici
ERP'den magaza secer, form ad/kod/ulke + ham satirlari doldurur, kullanici posta
kodu/sokak/sehri TAMAMLAYIP gonderir. Toplu otomatik uretim yapilmaz.
"""
from __future__ import annotations

import re

from erp.client import ERPClient

# GLS hesabimizin ERP'deki cari kimligi — tum alici adresleri bu hesabin altinda.
DEFAULT_ACCOUNT_ID = "9999"

# `Line1` serbest metin: sokak adi + kapi numarasi TEK alanda gelir, ama sira
# ULKEYE gore degisir — Hollanda/Almanya numarayi SONA yazar ("Rechtstraat 41"),
# Fransa/Belcika BASA yazar ("15 Rue de Domfront"). GLS NL/ShipIT etiket
# formlari numarayi AYRI ister (`houseNo`/`StreetNumber`) ve BOS birakilirsa
# etiketi reddeder — canli veride dogrulandi (bkz. ekran goruntusu: GOURE1N
# "91 Rue Jean Chatel Bp 152," icin No alani bos kaliyordu).
#
# ONCE SONDAKI numara denenir: Hollanda sokak adlari sira sayisiyla
# BASLAYABILIR ("1e Weteringplantsoen 3") — once BASTAKI denenseydi "1e" yanlislikla
# numara sanilir, gercek sondaki "3" sokak adinda mahsur kalirdi. Sonda numara
# yoksa (Fransiz stili) BASTAKI denenir.
_TRAILING_HOUSE_NUMBER_RE = re.compile(r"^(.*\S)[,\s]+(\d+\s*[a-zA-Z]?(?:[-/]\d+\s*[a-zA-Z]?)?)$")
_LEADING_HOUSE_NUMBER_RE = re.compile(r"^(\d+\s*[a-zA-Z]?(?:[-/]\d+\s*[a-zA-Z]?)?)[,\s]+(\S.*)$")

# Sokak adindan (harf/nokta, RAKAMSIZ) HEMEN sonraki numara + varsa kalan
# tanim metni. Yalnizca "sondaki numara 5 haneyi asiyor" durumunda kurtarma
# olarak kullanilir (asagi). "Musterstrasse 42 Lagerhaus 2.OG Raum 2201/03"
# -> ("Musterstrasse", "42", "Lagerhaus 2.OG Raum 2201/03").
_NAME_THEN_NUMBER_RE = re.compile(
    r"^([^\d/][^\d]*?)\s+(\d{1,5}[a-zA-Z]?(?:[-/]\d{1,4}[a-zA-Z]?)?)(?:[\s,]+(.+\S))?\s*$"
)

# GLS NL `houseNo` alani en fazla 5 karakter (canli hata, 2026-09-03:
# "The value HouseNo cannot exceed 5 characters." -> PVSDE1N sevkiyati komple
# patladi). Daha uzun bir deger neredeyse her zaman yanlis ayrilmis bir oda/
# birim numarasidir.
_HOUSE_NO_MAX = 5


def split_house_number(line: str) -> tuple[str, str]:
    """Adres satirindaki kapi numarasini sokak adindan ayirir (bas ya da son).

    'Kerkstraat 15' -> ('Kerkstraat', '15')             (NL/DE: sonda)
    '15 Rue de Domfront' -> ('Rue de Domfront', '15')   (FR/BE: basta)
    'Musterstrasse 42 Lagerhaus 2.OG Raum 2201/03'
        -> ('Musterstrasse Lagerhaus 2.OG Raum 2201/03', '42')  (kurtarma)
    Numara YOKSA satir oldugu gibi doner, `house_number` bos kalir — kullanici
    formda tamamlar, uydurma numara YAZILMAZ.
    """
    line = (line or "").strip()
    match = _TRAILING_HOUSE_NUMBER_RE.match(line)
    if match:
        street, number = match.group(1).strip(), match.group(2).replace(" ", "").strip()
        if len(number) <= _HOUSE_NO_MAX:
            return street, number
        # Sondaki "numara" 5 haneyi asiyor: bu bir oda/birim no
        # ("... Raum 1606/07"), gercek kapi no degil. Sokak adindan HEMEN
        # sonraki numarayi ("Musterstrasse 42" -> "42") kurtar; kalan tanim
        # metni sokak alanina geri katilir (GLS "No" 5 karakter siniri).
        rescue = _NAME_THEN_NUMBER_RE.match(line)
        if rescue:
            head, number, tail = (rescue.group(1).strip(),
                                  rescue.group(2).replace(" ", "").strip(),
                                  (rescue.group(3) or "").strip())
            return (f"{head} {tail}".strip() if tail else head), number
        return street, number       # kurtaramadik: asiri uzun da olsa geri ver
    match = _LEADING_HOUSE_NUMBER_RE.match(line)
    if match:
        return match.group(2).strip(), match.group(1).replace(" ", "").strip()
    return line, ""

# `WITH (NOLOCK)` erp/contents.py ile ayni gerekce: salt okunur panel sorgusu
# ERP'nin yazma islemlerini deadlock'a (hata 1205) sokmasin. Ulke adi FK ile
# `Meta_Country`den cozulur (Erp_Address yalnizca sayisal `CountryId` tutar).
# InUse/IsDeleted suzgeci: silinmis ve kullanim disi kayitlar listeye gelmesin.
ADDRESSES_QUERY = """
SELECT a.RecId, a.AddressCode, a.Explanation,
       a.Line1, a.Line2, a.Line3,
       a.PostalCode, a.Phone, a.EMail,
       c.CountryCode
FROM Erp_Address a WITH (NOLOCK)
LEFT JOIN Meta_Country c WITH (NOLOCK) ON c.RecId = a.CountryId
WHERE a.CurrentAccountId = %s
  AND (a.InUse = 1 OR a.InUse IS NULL)
  AND (a.IsDeleted IS NULL OR a.IsDeleted = 0)
ORDER BY a.Explanation, a.AddressCode
"""


def _text(value) -> str:
    """None ve literal 'NULL' metnini bos sayar.

    Erp_Address.Explanation bazi satirlarda gercekten 'NULL' STRING'i tutuyor
    (None degil) — ad alaninda "NULL" yazmasin diye ikisi de temizlenir.
    """
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "null") else text


def _map_row(row: dict) -> dict:
    """MSSQL satiri -> etiket formunun/adres kaynaginin kullandigi sozluk.

    Alan adlari AddressIn / labels.html ile AYNI olmali (name, name2, street,
    city, country ...) — form bu adlarla on-doldurur. Serbest metin satirlari
    en yakin structured alana yerlestirilir; kullanici formda tamamlar:
      Explanation -> name        (magaza/ajans adi)
      AddressCode -> name2       (etikete basilan magaza kodu) + consignee_id
      Line1       -> street + house_number (adres 1. satir; sonundaki numara
                     `split_house_number` ile ayrilir, GLS "No" alanini bos
                     kabul etmiyor)
      Line2       -> city        (adres 2. satir, cogunlukla sehir/bolge)
      Line3       -> region      (adres 3. satir, cogunlukla posta+ulke metni)
      CountryCode -> country     (Meta_Country'den 2 harfli kod)
      Explanation -> contact     (ayri irtibat alani yok; ad tekrarlanir)
    postal_code BILE BILE bos birakilir: ERP'de yapilandirilmis posta kodu yok,
    kullanici formda girer (GLS IE/NL bunu dogrular).
    """
    code = _text(row.get("AddressCode"))
    street, house_number = split_house_number(_text(row.get("Line1")))
    return {
        "id": row.get("RecId"),
        "consignee_id": code,
        "name": _text(row.get("Explanation")),
        "name2": code,
        "name3": "",
        "company": "",
        "street": street,
        "house_number": house_number,
        "addition": "",
        "postal_code": _text(row.get("PostalCode")),
        "city": _text(row.get("Line2")),
        "region": _text(row.get("Line3")),
        "country": _text(row.get("CountryCode")).upper()[:2],
        # Irtibat = magaza adinin kendisi (kullanici istegi, 2026-09-01:
        # "name 1 de yazani contacta da yaz ki bos kalmasin"). ERP'de ayri bir
        # irtibat kisisi alani yok; etiketin sol alt kosesindeki "Contact:"
        # satiri zaten unvanin yankisidir (bkz. label_payload.split_long_name).
        # Onceden bos birakiliyordu ve ETIKET FORMUNDA da bos gorunuyordu.
        # GLS yukunde davranis DEGISMEZ: `address_to_nl` bos contact'i zaten
        # unvandan dolduruyordu ve `given == full` dalinda ayni sonucu verir —
        # 30 karakteri asan unvanlarda tasan kisim yine contact'a devrolur.
        "contact": _text(row.get("Explanation")),
        "phone": _text(row.get("Phone")),
        "mobile": "",
        "email": _text(row.get("EMail")),
    }


def fetch_addresses(client: ERPClient, account_id: str = DEFAULT_ACCOUNT_ID) -> list[dict]:
    """Hesabin altindaki tum alici adresleri (ic sozluk anahtarlariyla).

    Adsiz (Explanation bos) kayitlar da donebilir; cagiran taraf bunlari secim
    listesinde koduyla gosterir. Yazma YOK — erp/client.py yalnizca SELECT'e izin
    verir.
    """
    rows = client.query(ADDRESSES_QUERY, (str(account_id),))
    return [_map_row(row) for row in rows]
