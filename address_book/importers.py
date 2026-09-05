# -*- coding: utf-8 -*-
"""
Adres defteri içe aktarıcıları — CSV / XLSX kaynaklardan.

Desteklenen kaynaklar:
  - Jenerik CSV/XLSX (sütun eşlemesi kullanıcıdan istenir — GUI tarafında)
  - Sentez Live "Cari Kart Listesi" dışa aktarımı
  - BlueCherry "Customer Master" dışa aktarımı
  - Sentez ODBC (Faz 2, kimlik gelince aktif)

Not: Sentez ve BlueCherry sütun adları tipik ihracat şablonlarına dayanır;
kullanıcı gerçek dosyayı verdiğinde eşleme küçük dokunuşlarla uyarlanabilir.
"""
import csv
import json
import re
from pathlib import Path
from typing import Iterable

import i18n

try:
    import pandas as pd  # xlsx desteği zaten var
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False


# Farklı kaynaklardaki sütun adlarını iç şemamıza eşleyen sözlük.
# Not: Anahtarlar case-insensitive karşılaştırma için normalize edilir.
COLUMN_ALIASES = {
    # iç ad            olası dış adlar
    "name":         ["name", "musteri", "müşteri", "ad", "cari adı", "cari_adi",
                     "customer name", "customer_name", "company name"],
    "company":      ["company", "firma", "sirket", "şirket", "firma unvanı",
                     "unvan", "firma_unvani"],
    "street":       ["street", "adres", "address", "address1", "adres 1",
                     "sokak", "cadde", "address line 1"],
    "postal_code":  ["postal_code", "postcode", "posta kodu", "posta_kodu",
                     "zip", "zipcode", "posta"],
    "city":         ["city", "sehir", "şehir", "il"],
    "region":       ["region", "state", "bölge", "eyalet", "bolge"],
    "country":      ["country", "ulke", "ülke", "country name"],
    "contact":      ["contact", "yetkili", "ilgili kişi", "contact_person",
                     "kontak"],
    "phone":        ["phone", "telefon", "gsm", "tel", "phone number"],
    "email":        ["email", "e-posta", "eposta", "e_mail", "mail"],
    "tax_no":       ["tax_no", "vergi no", "vergi_no", "vkn", "vat", "tax id",
                     "tax_id"],
    "notes":        ["notes", "not", "notlar", "aciklama", "açıklama",
                     "description"],
    "tags":         ["tags", "etiket", "etiketler", "kategori", "category"],
}


def _norm(s: str) -> str:
    return s.strip().lower().replace("_", " ")


def _build_reverse_map() -> dict:
    """dış_ad_normalize -> iç_ad."""
    m = {}
    for internal, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            m[_norm(a)] = internal
    return m


REV = _build_reverse_map()


def map_headers(headers: Iterable[str]) -> dict:
    """
    Verilen başlık listesini iç şemamıza eşler.
    Dönüş: {dış_başlık: iç_başlık}
    """
    mapping = {}
    for h in headers:
        if h is None:
            continue
        key = _norm(str(h))
        if key in REV:
            mapping[h] = REV[key]
    return mapping


# ---------------------------------------------------------------- CSV
def read_csv(path: str, delimiter: str = None) -> list:
    """CSV -> [{header: value, ...}, ...]. Otomatik encoding + delimiter denemesi."""
    p = Path(path)
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1254", "iso-8859-9", "latin-1"):
        try:
            text = p.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(i18n.t("imp.csv_encoding", path=path))

    if delimiter is None:
        # Basit sezgi: satırdaki ; virgülden çoksa noktalı virgül
        first_line = text.splitlines()[0] if text.splitlines() else ""
        delimiter = ";" if first_line.count(";") > first_line.count(",") else ","

    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    return [row for row in reader]


def read_xlsx(path: str) -> list:
    if not _HAS_PANDAS:
        raise RuntimeError(i18n.t("imp.xlsx_needs_pandas"))
    df = pd.read_excel(path, dtype=str).fillna("")
    return df.to_dict(orient="records")


# ---------------------------------------------------------------- Genel
def import_generic(path: str, extra_map: dict = None) -> list:
    """
    CSV veya XLSX'i oku, iç şemaya map'le, temiz kayıt listesi döner.
    `extra_map` — kullanıcı GUI'den elle eşleme yaptıysa {dış: iç}.
    """
    ext = Path(path).suffix.lower()
    raw = read_xlsx(path) if ext in (".xlsx", ".xls") else read_csv(path)
    if not raw:
        return []
    auto = map_headers(raw[0].keys())
    if extra_map:
        auto.update(extra_map)
    out = []
    for row in raw:
        rec = {}
        for outer, inner in auto.items():
            value = row.get(outer, "")
            if value is not None:
                rec[inner] = str(value).strip()
        if rec.get("name") or rec.get("company"):
            rec.setdefault("name", rec.get("company", ""))
            out.append(rec)
    return out


# ---------------------------------------------------------------- JSON
# GLS portalinin adres defteri disa aktarimi (addresses-export.json) camelCase
# kullanir ve adres tipini sayi olarak verir.
JSON_FIELD_MAP = {
    "name": "name", "name2": "name2", "name3": "name3",
    "company": "company",
    "street": "street",
    "houseNumber": "house_number", "houseNo": "house_number",
    "addition": "addition", "houseNoExt": "addition",
    "postalCode": "postal_code", "zipCode": "postal_code", "zipcode": "postal_code",
    "city": "city",
    "region": "region", "state": "region",
    "country": "country", "countryCode": "country",
    "consigneeId": "consignee_id",
    "contact": "contact",
    "phone": "phone",
    "mobile": "mobile",
    "email": "email",
    "taxNo": "tax_no",
    "notes": "notes",
    "tags": "tags",
}

# GLS portali adres tipini farkli bicimlerde verir: JSON disa aktariminda sayi
# (1/2), XLSX disa aktariminda Hollandaca metin (Zakelijk/Particulier). Hepsi tek
# sozlukte toplanir; string anahtarlar kucuk harfle aranir.
JSON_ADDRESS_TYPES = {1: "business", 2: "private", "1": "business", "2": "private",
                      "b": "business", "p": "private",
                      "business": "business", "private": "private",
                      "zakelijk": "business", "particulier": "private"}

# GLS portalinin disa aktariminda `company` alani FIRMA ADINI DEGIL MAGAZA KODUNU
# tasiyor (olculdu: 360 kaydin 297'sinde boyle, or. {"company": "PNHFR1N"}).
# Gercek etiketlerde de bu kod `name2` alaninda basiliyor (bkz. RPXSE1N.pdf),
# ayrica toplu etiket kolileri bu kodla esliyor.
STORE_CODE_RE = re.compile(r"^[A-Z]{3,8}\d[NP]$")


def _finalize_gls_record(rec: dict, address_type=None, tags=None) -> dict | None:
    """GLS export kaydina ortak son islemleri uygular (JSON + XLSX ortak).

    - `company` magaza koduysa `name2`ye tasinir (etiket bu alani basar, toplu
      etiket koliyi bu koddan bulur).
    - `house_number` posta koduyla ayniysa temizlenir (portalde yanlis girilmis).
    - Adres tipi (sayi 1/2 veya Hollandaca Zakelijk/Particulier) normalize edilir.
    - `tags` liste ise virgulle birlestirilir.
    Ad/firma yoksa None doner (cagiran atlar).
    """
    company = rec.get("company", "")
    if STORE_CODE_RE.match(company.upper()):
        rec["name2"] = company.upper()
        rec["company"] = ""

    if rec.get("house_number") and rec["house_number"] == rec.get("postal_code"):
        rec["house_number"] = ""

    if address_type is not None and address_type != "":
        key = address_type.lower() if isinstance(address_type, str) else address_type
        rec["address_type"] = JSON_ADDRESS_TYPES.get(key, "business")

    if isinstance(tags, list):
        rec["tags"] = ", ".join(str(t) for t in tags)

    if rec.get("name") or rec.get("company"):
        rec.setdefault("name", rec.get("company", ""))
        return rec
    return None


def import_json(path: str) -> list:
    """GLS portali adres defteri disa aktarimini (JSON) ic semaya cevirir.

    Beklenen govde: kayit listesi, ya da {"addresses": [...]} / {"data": [...]}.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict):
        for key in ("addresses", "data", "items", "records"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = [raw]
    if not isinstance(raw, list):
        raise ValueError(i18n.t("imp.bad_json"))

    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rec = {}
        for outer, inner in JSON_FIELD_MAP.items():
            value = item.get(outer)
            if value not in (None, ""):
                rec[inner] = str(value).strip()

        rec = _finalize_gls_record(
            rec,
            address_type=item.get("type", item.get("addresseeType")),
            tags=item.get("tags"),
        )
        if rec is not None:
            out.append(rec)
    return out


# ---------------------------------------------------------------- GLS XLSX
# GLS portalinin "addresses-export.xlsx" disa aktarimi. JSON'un aksine sutun
# adlari insan-okur (bosluklu, Ingilizce) ve tip Hollandaca metin. Magaza kodu
# `Company` sutununda; posta/telefon/e-posta JSON'da bos kalan alanlari doldurur.
GLS_XLSX_MAP = {
    "name": "name",
    "company": "company",
    "street": "street",
    "house number": "house_number",
    "postal code": "postal_code",
    "city": "city",
    "country": "country",
    "phone": "phone",
    "email": "email",
    "notes": "notes",
}


def import_gls_xlsx(path: str) -> list:
    """GLS adres defteri XLSX disa aktarimini ic semaya cevirir.

    JSON importer ile ayni son islemleri (`_finalize_gls_record`) paylasir; tek
    fark sutun eslemesi ve tip sutununun (`Type`) Hollandaca olmasidir.
    """
    rows = read_xlsx(path)
    out = []
    for row in rows:
        norm = {_norm(str(k)): v for k, v in row.items() if k is not None}
        rec = {}
        for outer, inner in GLS_XLSX_MAP.items():
            value = norm.get(outer)
            if value not in (None, ""):
                rec[inner] = str(value).strip()

        rec = _finalize_gls_record(rec, address_type=norm.get("type"))
        if rec is not None:
            out.append(rec)
    return out


# ---------------------------------------------------------------- Sentez
def import_sentez_csv(path: str) -> list:
    """
    Sentez Live "Cari Kart Listesi" tipik dışa aktarımını okur.
    Alias tablosu genel; Sentez sütun adları büyük ölçüde COLUMN_ALIASES kapsıyor
    ("Cari Adı", "Vergi No", "Adres", "Şehir", "Ülke", "Telefon", "E-Posta" gibi).
    """
    return import_generic(path)


def import_bluecherry_csv(path: str) -> list:
    """BlueCherry Customer Master ihracatı — jenerik importer ile aynı şeyi yapar."""
    return import_generic(path)


# ---------------------------------------------------------------- Sentez ODBC (Faz 2)
def import_sentez_odbc(dsn: str, query: str = None) -> list:
    """
    FAZ 2 — GERÇEK KULLANIM İÇİN İT'DEN GEREKENLER:
        1) Sentez MSSQL sunucu adı + veritabanı
        2) Salt okuma (read-only) kullanıcı adı/şifresi
        3) VPN / IP whitelist erişimi
        4) `pyodbc` + unixODBC (Ubuntu)  veya  MSSQL ODBC Driver
    Şu an bilinçli olarak NotImplementedError döner ki yanlış çalıştırılmasın.
    """
    raise NotImplementedError(
        "Sentez ODBC entegrasyonu Faz 2. IT'den DSN + kimlik + VPN erişimi bekleniyor. "
        "Bkz: docs/SENTEZ_BLUECHERRY_ENTEGRASYON.md"
    )
