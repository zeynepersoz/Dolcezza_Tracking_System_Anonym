"""Toplu etiket icin ETIKETSIZ kolileri MSSQL'den ceker (salt okuma).

`erp/sync.py` tam tersini yapar: takip numarasi OLAN kolileri panele aktarir.
Burada takip numarasi OLMAYAN koliler aranir — kullanicinin ifadesiyle "takip
numaralari labellardan sonra olusuyor", yani bos `UD_TrackingNumber` "henuz
etiketlenmemis" demektir.

Tablo/kolon adlari `erp/sync.py`'den import edilir, kopyalanmaz: sezon degisince
ya da kolon adi kayinca tek yerden duzelir.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from erp.client import ERPClient, safe_identifier
from erp.sync import BOX_TABLE, CUSTOMER_TABLE, INVOICE_COLUMN, _as_date
from gls_api.pipeline import clean_cell

# Yerel gelistirmede MSSQL'e hic baglanmadan toplu etiket ekranini test
# edebilmek icin sahte koliler. BILEREK `config.MODE`e degil, AYRI ve
# BELGESIZ bir env bayragina (`ERP_BOXES_MOCK_FIXTURE=1`) bagli — test
# paketi `config.MODE` "mock" iken CALISIYOR ve gercek SQL/parametre
# davranisini dogruluyor (bkz. tests/test_erp_boxes.py), `MODE`e baglansaydi
# o testler kirilirdi. Sadece kullanicinin kendi `.env`inde acik.
# Magaza kodu deseni `dispatch/plan.py:STORE_CODE_RE`e (harfler+rakam+N)
# uymak ZORUNDA, yoksa "Out of scope" sayilir. `JUIJNL1N` adres defterine
# name2 olarak eklendi (mutlu yol); `MXLMNL2N` bilerek KAYITSIZ — "eksik
# adres gir" adimini da test edebilmek icin.
_MOCK_FIXTURE_ENABLED = os.getenv("ERP_BOXES_MOCK_FIXTURE") == "1"


def _mock_boxes() -> list[dict]:
    today = date.today()
    yesterday = today - timedelta(days=1)
    return [
        {"rec_id": "9001", "box_code": "9990000000001", "store_code": "JUIJNL1N",
         "invoice": "9000001", "weight_kg": 2.4, "dimensions": "30x20x15",
         "customer_name": "Mock Fashion BV", "country": "NL", "season": "FA26",
         "packing_date": today.isoformat()},
        {"rec_id": "9002", "box_code": "9990000000002", "store_code": "JUIJNL1N",
         "invoice": "9000001", "weight_kg": 1.8, "dimensions": "25x20x10",
         "customer_name": "Mock Fashion BV", "country": "NL", "season": "FA26",
         "packing_date": today.isoformat()},
        {"rec_id": "9003", "box_code": "9990000000003", "store_code": "MXLMNL2N",
         "invoice": "9000002", "weight_kg": 0.0, "dimensions": "",
         "customer_name": "Mock Store Without Address", "country": "NL", "season": "FA26",
         "packing_date": today.isoformat()},
        {"rec_id": "9004", "box_code": "9990000000004", "store_code": "JUIJNL1N",
         "invoice": "9000003", "weight_kg": 3.1, "dimensions": "40x30x20",
         "customer_name": "Mock Fashion BV", "country": "NL", "season": "FA26",
         "packing_date": yesterday.isoformat()},
    ]

# Toplu etiket yalnizca GLS NL (Avrupa) agina uretilir.
# Ingiltere (GB/UK), Irlanda (IE), Rusya (RU), Avustralya (AU), Yeni Zelanda (NZ)
# ve Turkiye (TR) ayri sevkiyat kanallaridir — toplu etikete gelmemelidir.
# TR kullanici karariyla eklendi (2026-09-01): magaza kodu suzgeci duzeltilince
# BUTR344 (13 koli, GLS NL ile hic gonderi gecmisi YOK) listeye dusmustu —
# Turkiye'den cikan sevkiyat bu kanaldan etiketlenmez.
#
# Kod suzgeci `...P` ILE BITENLERI eler (Irlanda/ShipIT magazalari), ALT DIZE
# ARAMAZ. Onceki hali `UD_Buyer NOT LIKE '%AU%'` gibi alt dizelere bakiyordu ve
# kodunda tesadufen o harfler gecen GERCEK Avrupa magazalarini sessizce
# eliyordu (kullanici bildirdi 2026-09-01: PNHFR1N/FR ve OLIESCN/ES
# yazdirilacaklar listesinde hic cikmiyordu; ayni sebeple FVRFR1N, QVRNL1N,
# IVRHU1N, AVRPL1N, CNHNL1N, LAUPT01, DAUSK01 de risk altindaydi).
# Ulke suzgeci (yukaridaki `CustomerCountry NOT IN`) zaten GB/IE/RU/AU/NZ'yi
# ulkesi BILINEN satirlarda eliyor; bu kural ulkesi BOS olanlari kapatir
# (olculdu: ulkesi bos 10 kodun hepsi `...P` ile biten GB magazasi).
BOXES_QUERY = """
SELECT b.RecId, b.BoxCode, b.UD_Buyer, b.UD_KA, b.UD_Koli,
       b.UD_PaketlemeTarihi, b.{invoice} AS invoice, b.UD_Season,
       c.CustomerName, c.CustomerCountry
FROM {box} b
LEFT JOIN {customers} c
       ON c.CustomerCode = b.UD_Buyer
WHERE CAST(b.UD_PaketlemeTarihi AS DATE) = %s
  AND (b.UD_TrackingNumber IS NULL OR LTRIM(RTRIM(b.UD_TrackingNumber)) = '')
  AND b.{invoice} IS NOT NULL
  AND LTRIM(RTRIM(b.{invoice})) <> ''
  AND (b.IsDeleted IS NULL OR b.IsDeleted = 0)
  AND (c.CustomerCountry IS NULL OR UPPER(LTRIM(RTRIM(c.CustomerCountry))) NOT IN ('GB', 'UK', 'IE', 'IRL', 'RU', 'RUS', 'AU', 'AUS', 'NZ', 'NZL', 'TR', 'TUR'))
  AND b.UD_Buyer NOT LIKE '%P' AND b.UD_Buyer NOT LIKE '%P[-]%'
ORDER BY b.UD_Buyer, b.{invoice}, b.BoxCode
"""

# Tarih seciciye rozet basmak icin: hangi gunde kac koli/magaza etiket bekliyor (GB, IE, RU, AU, NZ haric).
DATES_QUERY = """
SELECT CAST(b.UD_PaketlemeTarihi AS DATE) AS packing_date,
       COUNT(*) AS boxes,
       COUNT(DISTINCT b.UD_Buyer) AS stores
FROM {box} b
LEFT JOIN {customers} c
       ON c.CustomerCode = b.UD_Buyer
WHERE b.UD_PaketlemeTarihi >= DATEADD(day, -%s, CAST(GETDATE() AS DATE))
  AND (b.UD_TrackingNumber IS NULL OR LTRIM(RTRIM(b.UD_TrackingNumber)) = '')
  AND b.{invoice} IS NOT NULL
  AND LTRIM(RTRIM(b.{invoice})) <> ''
  AND (b.IsDeleted IS NULL OR b.IsDeleted = 0)
  AND (c.CustomerCountry IS NULL OR UPPER(LTRIM(RTRIM(c.CustomerCountry))) NOT IN ('GB', 'UK', 'IE', 'IRL', 'RU', 'RUS', 'AU', 'AUS', 'NZ', 'NZL', 'TR', 'TUR'))
  AND b.UD_Buyer NOT LIKE '%P' AND b.UD_Buyer NOT LIKE '%P[-]%'
GROUP BY CAST(b.UD_PaketlemeTarihi AS DATE)
ORDER BY packing_date DESC
"""


def _as_float(value) -> float:
    """`UD_KA` (koli agirligi, kg) -> float. Bos/bozuk deger 0.0 doner.

    0.0 bilincli: `dispatch/plan.py` agirligi olmayan koliyi ayrica isaretler,
    burada uydurma bir varsayilan (1 kg gibi) yanlis etiket bastirir.
    """
    if isinstance(value, (int, float)):
        return float(value)
    text = clean_cell(value).replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _sql(query: str) -> str:
    return query.format(
        box=safe_identifier(BOX_TABLE),
        customers=safe_identifier(CUSTOMER_TABLE),
        invoice=safe_identifier(INVOICE_COLUMN),
    )


def fetch_unlabeled_boxes(client: ERPClient, packing_date: str | date) -> list[dict]:
    """Verilen paketleme gununde faturasi girili, takip numarasi bos koliler."""
    day = packing_date.isoformat() if isinstance(packing_date, date) else str(packing_date)
    if _MOCK_FIXTURE_ENABLED:
        return [b for b in _mock_boxes() if b["packing_date"] == day]
    rows = client.query(_sql(BOXES_QUERY), (day,))
    return [
        {
            "rec_id": clean_cell(row.get("RecId")),
            "box_code": clean_cell(row.get("BoxCode")),
            "store_code": clean_cell(row.get("UD_Buyer")).upper(),
            "invoice": clean_cell(row.get("invoice")),
            "weight_kg": _as_float(row.get("UD_KA")),
            "dimensions": clean_cell(row.get("UD_Koli")),
            "customer_name": clean_cell(row.get("CustomerName")),
            "country": clean_cell(row.get("CustomerCountry")),
            # Etiket olusurken sezonu HEMEN alir — `erp/sync.py:season_of`
            # sonraki senkronu bekleseydi (kullanici bildirdi 2026-09-02:
            # "bunların sezonu niye yok"): o senkron yalnizca `UD_PaketlemeTarihi
            # < GETDATE()` olan (fiilen paketlenmis) kolileri kapsar, ileri
            # tarihli (ornek: bugun basilip 04.09'da alinacak) etiketler
            # gunler boyunca sezonsuz kalirdi.
            "season": clean_cell(row.get("UD_Season")),
            "packing_date": _as_date(row.get("UD_PaketlemeTarihi")),
        }
        for row in rows
    ]


def available_dates(client: ERPClient, days: int = 30) -> list[dict]:
    """Son `days` gunde etiket bekleyen paketleme tarihleri (yeni -> eski)."""
    if _MOCK_FIXTURE_ENABLED:
        by_day: dict[str, list[dict]] = {}
        for box in _mock_boxes():
            by_day.setdefault(box["packing_date"], []).append(box)
        return [
            {"date": day, "boxes": len(boxes),
             "stores": len({b["store_code"] for b in boxes})}
            for day, boxes in sorted(by_day.items(), reverse=True)
        ]
    rows = client.query(_sql(DATES_QUERY), (days,))
    return [
        {
            "date": _as_date(row.get("packing_date")),
            "boxes": int(row.get("boxes") or 0),
            "stores": int(row.get("stores") or 0),
        }
        for row in rows
    ]
