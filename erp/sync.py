"""Sentez / BlueCherry MSSQL'inden takip numaralarini panele aktarir.

Panelin BIRINCIL kaynagi burasidir; elle yuklenen Excel (web/importers/
tracking_list.py) yalnizca MSSQL'e ulasilamadiginda kullanilan yedek yoldur.

Neden gerekti: elle yuklenmis 4420 parcanin 879'u hicbir MSSQL gorunumunde yoktu
(yanlis girilmis ya da eski sezon artigi), buna karsilik gercek sevkiyatlarin
4732'si panelde hic gorunmuyordu.

Gorunumler SATIR BAZLIDIR (her SKU bir satir: FA26'da 27954 satir -> 522 takip
numarasi), bu yuzden `SELECT DISTINCT` sart.

Calistirma:
    python -m erp.sync                 # ekle/guncelle
    python -m erp.sync --dry-run       # sayim, panele yazmadan
    python -m erp.sync --reset --yes   # once paneli sifirla, sonra doldur
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date, datetime

from erp.client import ERPClient, ERPError, safe_identifier
from gls_api import config
from gls_api.pipeline import clean_cell
from tracking.db import ShipmentsDB

log = logging.getLogger("erp.sync")

# TASIMA FIRMASI kanalin BIRINCIL olcutudur (kullanici karari 2026-09-03:
# "tum sorgular tasima firmasina gore yapilacak"). ERP bu alani artik hem
# `Erp_Box`ta hem FA26 gorunumunde dolduruyor — olculdu 2026-09-03:
#   Vision_FA26_Boxed_EUROPE : GLS-NL 1515, GLS-IE 69, FEDEX 3, bos 0
#   Erp_Box                  : GLS-IE 7133, GLS-NL 1775, bos 913, ARAMEX 29, UPS 5, FEDEX 3
# Alani BOS olan satirlar bizim kolilerimiz DEGIL: SP26/FA26'daki 527 bos
# satirin onekleri MP00/1597/8869/2256… — hicbiri GLS serisi degil (38120177
# ya da 2156/2158 yok). Bu yuzden tasiyiciya gore suzmek veri KAYBETTIRMEZ,
# tam tersine 2026-09-02'de panele akan copu eler.
CARRIER_COLUMN = "UD_TasimaFirmasi"

# ERP'nin yazdigi tasiyici adi -> bizim kanal kodumuz. Bu sozlukte OLMAYAN
# tasiyici (ARAMEX, UPS, bos…) panele HIC alinmaz: takip edebildigimiz
# yalnizca bu uc kanaldir (bkz. gls_api/providers.py).
CARRIER_CHANNELS = {"GLS-NL": "NL", "GLS-IE": "IE", "FEDEX": "FEDEX"}

# Tasiyici alani bos gelen ESKI satirlar icin son care. Olculdu: `2156...` ve
# `2158...` seri numaralari FR/DE/ES/PL/AT... hepsine gidiyor, `38120177...` da
# oyle — yani ulkeye bakan kural (web/importers/tracking_list.py:detect_channel)
# burada yanlis sonuc verir. `38120177` = GLS NL musteri numarasi.
NL_PREFIX = "38120177"

# Panele HIC alinmayan ulkeler:
#   RU  Rusya sevkiyatlari — kapsam disi.
#   AU / NZ Avustralya ve Yeni Zelanda sevkiyatlari — ayri hat / kapsam disi.
#   Un  ERP'de ulkesi girilmemis satirlarin doldurma degeri ("Undefined").
# NULL ulke olculdu: FA26'da 0 satir. Cikarsa `NOT IN` onu da duserdi; oyle bir
# satir zaten `Un` kadar kullanissizdir, ozel durum yazilmadi.
#
# IE (Irlanda) 2026-09'a kadar buradaydi: IE kanali takip EDILEMIYORDU. Artik
# GLS Track & Trace v1 ile takip ediliyor (bkz. gls_api/tt_v1_client.py), o
# yuzden IE cikarildi; hangi IE-kanali kolisinin alinacagini asagidaki
# `IE_CHANNEL_COUNTRIES` belirler.
EXCLUDED_COUNTRIES = ("Un", "RU", "AU", "NZ", "AUS", "NZL")

# IE kanali ("2156..." serisi = eski ShipIT / Irlanda hub) YALNIZCA Irlanda
# teslimatlari icin panele alinir. Ayni seri bazen FR/DE/ES'e de gidiyor
# (olculdu 2026-08: 39 koli) — onlarin is sahipligi İş biriminin PHP'sinde,
# cift is olmasin diye disarida birakilir.
#
# GB (Britanya) kisa sure listedeydi ve 2026-09-02'de CIKARILDI: getirdigi tek
# sey MARK TAYLOR FASHIONS'a ait 3 olu kayitti (sevk 2025-12-08, 268 gun once).
# GLS onlari sisteminden silmis, T&T v1 `E_404_01` donuyor — asla cozulmeyecek
# ama her turda sorulup kotadan yiyorlardi. Kuzey Irlanda teslimatlari KAYBOLMAZ:
# ERP onlari `CustomerCountry='IE'` yaziyor (olculdu: Alana Interiors, GB-BT66
# CRAIGAVON -> magaza kodu NYNIE1N, ulke IE).
IE_CHANNEL_COUNTRIES = ("IE", "IRL")

# `UD_Buyer` -> musteri adi. Olculdu: 263/263 eslesme. POD dosya adi buna bagli.
CUSTOMER_TABLE = "BCH_Full_Customer_Data"
# Sevkiyat sekli (or. '5th AIR SHIPMENT'). Tum sezonlari kapsar (8260 takip no).
SHIPMENT_TABLE = "WKargo_Takip_Listesi"

# Sezon gorunumleri AYNI SEMAYA SAHIP DEGIL: FA26'da `UD_BCHInvoiceNumber` var,
# SP26'da yok. Fatura numarasi POD dosya adina giriyor, vazgecilemez — bu yuzden
# asil kaynak WKargo'dur (SP26'nin 3499 takip numarasinin 3498'ini kapsiyor) ve
# gorunumun kendi kolonu varsa o tercih edilir.
INVOICE_COLUMN = "UD_BCHInvoiceNumber"

# WKargo alt sorgusu GRUPLANIR: ayni takip no orada birden cok satirda olabilir,
# dogrudan LEFT JOIN yapilirsa ana sorgunun satirlari cogalir.
# Tarih siniri GUN bazlidir (`CAST(GETDATE() AS DATE)`), saat bazli degil: MSSQL'de
# ileri tarihli, henuz GLS'e girmemis kayitlar var (olculdu: 07-08 Agustos'a 75
# parca, hatta 29 Aralik'a 1 tane) ve onlari almak tracker'a var olmayan parcayi
# sordurur. Saat bazli `< GETDATE()` bugun paketlenenleri de iceri alirdi — etiket
# ayni gun basiliyor ama parca GLS'e ertesi gun teslim ediliyor.
SEASON_QUERY = """
SELECT DISTINCT
    v.UD_TrackingNumber, v.UD_Buyer, v.CustomerCountry, v.UD_PaketlemeTarihi,
    {invoice_expr} AS UD_BCHInvoiceNumber,
    {carrier_expr} AS UD_TasimaFirmasi,
    c.CustomerName, k.UD_GonderimSekli
FROM {view} v
LEFT JOIN {customers} c
       ON c.CustomerCode = v.UD_Buyer
LEFT JOIN (SELECT UD_TrackingNumber,
                  MIN(UD_GonderimSekli) AS UD_GonderimSekli,
                  MIN(UD_BCHInvoiceNumber) AS UD_BCHInvoiceNumber
             FROM {shipments}
            GROUP BY UD_TrackingNumber) k
       ON k.UD_TrackingNumber = v.UD_TrackingNumber
WHERE v.UD_TrackingNumber IS NOT NULL
  AND LTRIM(RTRIM(v.UD_TrackingNumber)) <> ''
  AND v.UD_PaketlemeTarihi <= CAST(GETDATE() AS DATE)
  AND v.CustomerCountry NOT IN ({countries})
  {carrier_filter}
"""

COLUMNS_QUERY = ("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                 "WHERE TABLE_NAME = %s")

# Sezon gorunumleri kaynagin TAMAMI DEGILDIR. Olculdu (2026-08): elle tutulan
# FA26 listesinde 532 GLS NL kolisi var, `Vision_FA26_Boxed_EUROPE` bunlarin
# yalnizca 480'ini veriyor. Eksik 52 koli GLS'te GERCEKTEN var (sorgulandi,
# hepsi "announced") ama gorunumde hic gecmiyor — 48'i TRICOTTO markasi, FA26
# icin Tricotto gorunumu yok. Bu yuzden gorunumlerden sonra taban tablo da
# taranir. `add_parcel`in ON CONFLICT birlestirmesi sayesinde gorunumden gelen
# degerler EZILMEZ; taban tablo yalnizca eksikleri ekler ve bos alanlari doldurur.
BOX_TABLE = "Erp_Box"

# Taban tabloda ulke kolonu YOKTUR, musteri tablosundan gelir. Eslesmeyen satirin
# ulkesi NULL kalir; `ISNULL(...,'Un')` onu da EXCLUDED_COUNTRIES'e dusurur —
# ulkesi bilinmeyen satir gorunumdeki 'Un' kadar kullanissizdir.
# `IsDeleted` alanindaki NULL "silinmemis" demektir (olculdu: NL tarafinda hic 1 yok).
BOX_QUERY = """
SELECT DISTINCT
    b.UD_TrackingNumber, b.UD_Buyer, b.UD_Season, c.CustomerCountry, b.UD_PaketlemeTarihi,
    COALESCE(NULLIF(b.UD_BCHInvoiceNumber, ''), k.UD_BCHInvoiceNumber) AS UD_BCHInvoiceNumber,
    c.CustomerName, b.UD_TasimaFirmasi,
    -- Sevkiyat adi POD klasor yolunu belirliyor (FA26_POD/1st Truck Shipment/...).
    -- Olculdu (2026-08): FA26'nin 664 NL kolisinde `WKargo_Takip_Listesi`.
    -- UD_GonderimSekli TAMAMEN BOS; alan yalnizca eski sezonlarda dolu. Kullanici
    -- bundan sonra Sentez'de `Erp_Box.UD_GonderimSekli`i dolduracak, bu yuzden
    -- taban tablo ONCE okunur, WKargo yalnizca yedek kalir.
    COALESCE(NULLIF(b.UD_GonderimSekli, ''), k.UD_GonderimSekli) AS UD_GonderimSekli
FROM {box} b
LEFT JOIN {customers} c
       ON c.CustomerCode = b.UD_Buyer
LEFT JOIN (SELECT UD_TrackingNumber,
                  MIN(UD_GonderimSekli) AS UD_GonderimSekli,
                  MIN(UD_BCHInvoiceNumber) AS UD_BCHInvoiceNumber
             FROM {shipments}
            GROUP BY UD_TrackingNumber) k
       ON k.UD_TrackingNumber = b.UD_TrackingNumber
WHERE b.UD_TrackingNumber IS NOT NULL
  AND LTRIM(RTRIM(b.UD_TrackingNumber)) <> ''
  AND (b.IsDeleted IS NULL OR b.IsDeleted = 0)
  -- FEDEX kolileri sezon/ulke/tarih suzgeclerinin DISINDA tutulur.
  -- Gerekce (canli olcum, 2026-09-01 — kullanici "fedexler de gelsin, tasiyici
  -- firma fedex" dedi): FedEx kolilerinde `UD_Buyer`, `UD_Season`,
  -- `UD_BCHInvoiceNumber` ve musteri ulkesi BOS veya 'Sample' gelebiliyor.
  -- Olcut TEK: ERP'nin `UD_TasimaFirmasi` alani.
  --
  -- TASIYICI kapisi: takip edebildigimiz uc kanal disindaki hicbir satir
  -- panele girmez (ARAMEX/UPS/bos…). 2026-09-02'de burada "takip no 12 hane"
  -- diye bir tahmin dali vardi ve felaketle sonuclandi — sezon/ulke/tarih
  -- suzgeclerinin hicbirine tabi olmadigi icin `Erp_Box`in tum gecmisini
  -- supurdu (287 koli bir gecede, 285'i "delivered", tek saatte 207 WhatsApp
  -- mesaji). Tahmin yerine ERP'nin kendi alani kullaniliyor.
  AND UPPER(LTRIM(RTRIM(ISNULL(b.{carrier}, '')))) IN ({carriers})
  -- FEDEX sezon/ulke suzgeclerinin DISINDA tutulur (kullanici karari
  -- 2026-08-31): o kolilerde `UD_Buyer`, `UD_Season` ve musteri ulkesi bos
  -- gelebiliyor. Tarih tabani yine de sart — gecmis arsivi degil, takip
  -- edilecek guncel kolileri isteriz (`config.FEDEX_SYNC_FROM_DATE`).
  AND (
        (    UPPER(LTRIM(RTRIM(ISNULL(b.{carrier}, '')))) = 'FEDEX'
         AND b.UD_PaketlemeTarihi >= %s )
     OR (
            b.UD_PaketlemeTarihi <= CAST(GETDATE() AS DATE)
        AND b.UD_Season IN ({seasons})
        AND ISNULL(c.CustomerCountry, 'Un') NOT IN ({countries})
        )
      )
"""

# `Vision_FA26_Boxed_EUROPE` -> `FA26`. Taban tablo TUM sezonlari tutar (olculdu:
# SP27'nin 132 kolisi de orada) — sezon suzgeci olmadan panel, kullanicinin takip
# etmedigi sezonlarla dolardi. Ayarlardaki gorunum listesi tek dogru kaynak kalsin
# diye sezon kodu oradan cikarilir, ayri bir ayar acilmaz.
_SEASON_CODE = re.compile(r"(?:SP|FA)\d{2}")


def _as_date(value) -> str:
    """UD_PaketlemeTarihi -> ISO tarih (saat kismi panelde gosterilmiyor)."""
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return clean_cell(value)[:10]


def season_of(row: dict, source: str) -> str:
    """Parcanin sezon kodu ("FA26"): once ERP'nin alani, sonra kaynak adi.

    Taban tabloda (`Erp_Box`) kolon vardir ve kaynak adinda sezon YOKTUR; sezon
    gorunumlerinde tam tersi. Ikisi de bossa alan bos birakilir — `config.
    season_from_source` gibi guncel sezona DUSULMEZ: tahmin edilen etiket, yeni
    sezon acildiginda eski kolileri yenisiyle karistirir (kullanicinin bu sutunu
    istemesindeki asil sebep buydu).
    """
    found = _SEASON_CODE.search(source or "")
    return clean_cell(row.get("UD_Season")) or (found.group() if found else "")


def map_row(row: dict, view: str) -> dict | None:
    """MSSQL satirini `ShipmentsDB.add_parcel` argumanlarina cevirir.

    None doner: takip numarasi kullanilabilir degil (bos ya da rakam disi) ya da
    IE kanali kolisi Irlanda/Britanya disina gidiyor (bkz. `IE_CHANNEL_COUNTRIES`).

    zip_code / sales_rep / weight_kg / freight_cost MSSQL'de YOKTUR ve
    uydurulmaz — bos gecilir. `add_parcel`'in COALESCE(NULLIF(...)) birlestirmesi
    sayesinde daha once Excel'den gelmis degerler ezilmez.
    """
    tracking_no = clean_cell(row.get("UD_TrackingNumber"))
    tracking_no = re.sub(r"\s+", "", tracking_no)
    # Veride 'GLS' ve bos deger gibi artiklar var; rakam olmayan satir alinmaz.
    if len(tracking_no) < 10 or not tracking_no.isdigit():
        return None
    # FEDEX tespiti: tasiyici adinda FEDEX varsa veya standart 12 haneli FedEx barkodu ise
    # Kanal: BIRINCIL olcut ERP'nin tasiyici alani (`CARRIER_CHANNELS`).
    # Alan bos gelen ESKI satirlar icin onek tahminine dusulur — bu yalnizca
    # tasiyicisi olmayan sezon gorunumleri icindir (SP26), yeni sorgular zaten
    # tasiyiciya gore suzuyor.
    carrier = clean_cell(row.get("UD_TasimaFirmasi")).strip().upper()
    channel = CARRIER_CHANNELS.get(carrier)
    if channel is None:
        channel = "NL" if tracking_no.startswith(NL_PREFIX) else "IE"
    country = clean_cell(row.get("CustomerCountry"))
    # IE kanali (2156... serisi) yalnizca Irlanda/Britanya teslimatlari icin
    # alinir; ayni seri FR/DE/ES'e de gidiyor ve onlar kapsam disi
    # (is sahipligi İş biriminin PHP'sinde).
    if channel == "IE" and country.strip().upper() not in IE_CHANNEL_COUNTRIES:
        return None

    store_code = clean_cell(row.get("UD_Buyer"))
    invoice = clean_cell(row.get("UD_BCHInvoiceNumber"))
    return {
        "tracking_no": tracking_no,
        "channel": channel,
        "store_code": store_code,
        # Musteri adi yoksa magaza koduna duseriz: POD dosya adi bu alandan uretiliyor.
        "consignee_name": clean_cell(row.get("CustomerName")) or store_code,
        "country": country,
        "invoice_number": invoice,
        "reference": invoice,
        "shipment_date": _as_date(row.get("UD_PaketlemeTarihi")),
        "shipment_method": clean_cell(row.get("UD_GonderimSekli")),
        "source_sheet": view,
        "season": season_of(row, view),
    }


def fetch_season(client: ERPClient, view: str) -> list[dict]:
    """Tek sezon gorunumunden ham satirlari ceker (salt okuma)."""
    view = safe_identifier(view)
    columns = {r["COLUMN_NAME"] for r in client.query(COLUMNS_QUERY, (view,))}
    invoice_expr = (f"COALESCE(NULLIF(v.{INVOICE_COLUMN}, ''), k.{INVOICE_COLUMN})"
                    if INVOICE_COLUMN in columns else f"k.{INVOICE_COLUMN}")

    # Tasiyici alani her gorunumde YOK: olculdu 2026-09-03 —
    # Vision_FA26_Boxed_EUROPE'ta VAR ve dolu (GLS-NL 1515 / GLS-IE 69 /
    # FEDEX 3 / bos 0), Vision_SP26_Boxed_EUROPE'ta YOK. Varsa hem secilir hem
    # SUZGEC olur; yoksa NULL secilir ve `map_row` onek tahminine duser —
    # eski sezonun davranisi aynen korunur.
    has_carrier = CARRIER_COLUMN in columns
    carrier_expr = f"v.{CARRIER_COLUMN}" if has_carrier else "NULL"
    carrier_filter = (
        f"AND UPPER(LTRIM(RTRIM(ISNULL(v.{CARRIER_COLUMN}, '')))) IN "
        f"({', '.join(['%s'] * len(CARRIER_CHANNELS))})" if has_carrier else "")

    sql = SEASON_QUERY.format(
        view=view,
        customers=safe_identifier(CUSTOMER_TABLE),
        shipments=safe_identifier(SHIPMENT_TABLE),
        invoice_expr=invoice_expr,
        carrier_expr=carrier_expr,
        carrier_filter=carrier_filter,
        # Tablo/kolon adlari kimliktir, metne gomulur; ulke DEGERDIR, parametre
        # gecer. Sabit bir demet olsa da kural bozulmaz.
        countries=", ".join(["%s"] * len(EXCLUDED_COUNTRIES)),
    )
    # SIRA sorgudaki `%s` sirasi: once ulkeler, sonra (varsa) tasiyicilar.
    args = tuple(EXCLUDED_COUNTRIES)
    if has_carrier:
        args += tuple(CARRIER_CHANNELS)
    return client.query(sql, args)


def season_codes(views: list[str]) -> list[str]:
    """Gorunum adlarindan sezon kodlarini cikarir; kod bulunmayan ad atlanir."""
    codes: list[str] = []
    for view in views:
        found = _SEASON_CODE.search(view or "")
        if found and found.group() not in codes:
            codes.append(found.group())
    return codes


def fetch_boxes(client: ERPClient, seasons: list[str]) -> list[dict]:
    """Taban tablodan (gorunumlerin kacirdiklari dahil) ham satirlari ceker."""
    if not seasons:
        return []
    sql = BOX_QUERY.format(
        box=safe_identifier(BOX_TABLE),
        customers=safe_identifier(CUSTOMER_TABLE),
        shipments=safe_identifier(SHIPMENT_TABLE),
        carrier=CARRIER_COLUMN,
        # Sezon kodu, ulke ve tasiyici adi DEGERDIR: metne gomulmez, parametre gecer.
        carriers=", ".join(["%s"] * len(CARRIER_CHANNELS)),
        seasons=", ".join(["%s"] * len(seasons)),
        countries=", ".join(["%s"] * len(EXCLUDED_COUNTRIES)),
    )
    # Parametre SIRASI sorgudaki `%s` sirasidir: tasiyicilar, FedEx tarih
    # tabani, sezonlar, ulkeler.
    return client.query(sql, tuple(CARRIER_CHANNELS)
                        + (config.FEDEX_SYNC_FROM_DATE,)
                        + tuple(seasons) + EXCLUDED_COUNTRIES)


def sync(db: ShipmentsDB, client: ERPClient | None = None, views: list[str] | None = None,
         dry_run: bool = False) -> dict:
    """Sezon gorunumlerini, sonra taban tabloyu sirayla panele aktarir.

    Ayni takip numarasi birden cok kaynakta gorunurse `add_parcel`'in ON CONFLICT
    birlestirmesi calisir: ikinci kaynak yalnizca bos kalan alanlari doldurur.
    """
    client = client or ERPClient()
    views = views if views is not None else config.season_views()

    eklenen = guncellenen = atlanan = 0
    hatalar: list[str] = []
    # takip no -> (ilk gorulen parca, o koliye ait faturalar gorulme sirasiyla).
    # Parcanin kendisi de saklanir: sonraki kaynaklarin BOS BIRAKTIGI alanlari
    # doldurabilmesi ama dolu alanlari ezmemesi gerekiyor. Birlestirme burada,
    # Python'da yapilir — `add_parcel`in SQL birlestirmesi tersini yapar
    # (yeni deger bos degilse eskisini ezer), o kural yazma sirasi icindir.
    seen: dict[str, tuple[dict, list[str]]] = {}

    # Gorunumler ONCE gelir: kolonlari sezona ozeldir ve daha guvenilirdir. Taban
    # tablo EN SONA birakilir, boylece yalnizca gorunumlerin kacirdiklarini ekler.
    sources: list[tuple[str, object]] = [
        (view, lambda v=view: fetch_season(client, v)) for view in views
    ]
    sources.append((BOX_TABLE, lambda: fetch_boxes(client, season_codes(views))))

    for source, fetch in sources:
        try:
            rows = fetch()
        except ERPError as exc:
            hatalar.append(f"{source}: {exc}")
            continue

        for row in rows:
            parcel = map_row(row, source)
            if parcel is None:
                atlanan += 1
                continue
            tracking_no = parcel["tracking_no"]
            invoice = parcel["invoice_number"]
            if tracking_no in seen:
                # Ayni koli birden cok fatura tasiyabilir (musteri: "adresleri
                # ayniysa"). Olculdu: FA26'da 484 (takip no + fatura) cifti ama
                # 480 koli — 4 kolide iki fatura. Eskiden ikinci fatura sessizce
                # dusuyordu; artik `emc_invoices`e ekleniyor, yeni parca acilmiyor.
                onceki, invoices = seen[tracking_no]
                if invoice and invoice not in invoices:
                    invoices.append(invoice)
                # Ilk kaynak kazanir, tekrar eden satir yalnizca BOS alanlari
                # doldurur: sevkiyat sekli (`UD_GonderimSekli`) sezon gorunumunde
                # yoktur, panele ancak taban tablodan boyle ulasir.
                birlesik = {k: v or parcel.get(k, "") for k, v in onceki.items()}
                if len(invoices) > 1:
                    birlesik["emc_invoices"] = ", ".join(invoices)
                if birlesik != onceki:
                    seen[tracking_no] = (birlesik, invoices)
                    if not dry_run:
                        db.add_parcel(**birlesik)
                continue
            seen[tracking_no] = (parcel, [invoice] if invoice else [])

            var = db.get(tracking_no) is not None
            if not dry_run:
                db.add_parcel(**parcel)
            if var:
                guncellenen += 1
            else:
                eklenen += 1

    # Anahtarlar web/templates/_partials/upload_result.html ile ayni: Excel
    # yuklemesi ve MSSQL senkronu tek sonuc sablonunu paylasir.
    return {
        "source": ", ".join([*views, BOX_TABLE]),
        "imported": eklenen + guncellenen,
        "added": eklenen,
        "updated": guncellenen,
        "skipped": atlanan,
        "total": eklenen + guncellenen + atlanan,
        "errors": hatalar,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MSSQL'den takip numaralarini panele aktar")
    parser.add_argument("--reset", action="store_true",
                        help="once paneldeki TUM parcalari sil (GERI ALINAMAZ)")
    parser.add_argument("--dry-run", action="store_true", help="panele yazma, sadece say")
    parser.add_argument("--yes", action="store_true", help="--reset icin onay sorma")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not config.mssql_configured():
        print("MSSQL yapilandirilmamis — .env'de MSSQL_* degerlerini doldurun.", file=sys.stderr)
        return 2

    db = ShipmentsDB()

    if args.reset and not args.dry_run:
        mevcut = db.total()
        podlu = len([p for p in db.list_parcels(limit=100000) if p.get("pod_path")])
        print(f"DIKKAT: paneldeki {mevcut} parca ve olay gecmisi silinecek.")
        print(f"Bunlarin {podlu} tanesinin POD'u var; PDF dosyalari diskte kalir "
              f"ama panelden erisilemez. Bu islem GERI ALINAMAZ.")
        if not args.yes and input("Devam edilsin mi? [y/N] ").strip().lower() != "y":
            print("Iptal edildi.")
            return 1
        print(f"{db.purge_parcels()} parca silindi.")

    sonuc = sync(db, dry_run=args.dry_run)
    print(f"kaynak      : {sonuc['source']}")
    print(f"eklenen     : {sonuc['added']}")
    print(f"guncellenen : {sonuc['updated']}")
    print(f"atlanan     : {sonuc['skipped']}  (gecersiz takip no ya da IE kanali)")
    for hata in sonuc["errors"]:
        print(f"HATA        : {hata}", file=sys.stderr)
    return 1 if sonuc["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
