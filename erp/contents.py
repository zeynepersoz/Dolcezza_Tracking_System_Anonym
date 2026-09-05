"""Bir kolinin ICINDE ne var: barkodlar, adetler ve beden matrisi (salt okuma).

Kullanicinin istegi: "kolinin icindeki barkod numaralari ve adetlerini gorucez".
İş biriminin mobil uygulamasindaki ekran bir BEDEN MATRISI — satir basi
model+renk, sutunlar bedenler. Ayni gorunum burada uretilir.

Veri sezon gorunumlerinden gelir; `dbo.BCH_Barcode` ile ayrica JOIN YAPILMAZ:
canlida olculdu, `Vision_*_Boxed_EUROPE` gorunumleri `StyleNumber/StyleColor/
StyleSize` alanlarini zaten tasiyor. Fazladan bir JOIN yalnizca kilit riski
eklerdi.

Sonuc SQLite'a KOPYALANMAZ (kullanici karari): icerik canli sorgulanir, boylece
ERP'de bir duzeltme yapildiginda panel ayni anda dogruyu gosterir.
"""
from __future__ import annotations

from decimal import Decimal

from erp.client import ERPClient, safe_identifier
from gls_api import config

# `WITH (NOLOCK)` ZORUNLU: ilk denemede bu gorunumler MSSQL deadlock'u (hata
# 1205) verdi. Salt okunur bir panel sorgusu yuzunden ERP'nin yazma islemleri
# beklememeli.
CONTENTS_QUERY = """
SELECT UD_SPS, Barcode, qty, BCH_UPC, StyleNumber, StyleColor, StyleSize
FROM {view} WITH (NOLOCK)
WHERE UD_TrackingNumber = %s
"""

# Coklu secim (musteri bazli Excel) icin ayni sorgunun toplu hali. Koli basina
# ayri gitmek 50 kolide 150 sorgu demekti; kullanici indirmenin bitmesini
# dakikalarca bekliyordu. Takip no SELECT'e de alinir — donen satirlar hangi
# koliye ait olduklarini kendileri tasimali.
CONTENTS_MANY_QUERY = """
SELECT UD_TrackingNumber, UD_SPS, Barcode, qty, BCH_UPC,
       StyleNumber, StyleColor, StyleSize
FROM {view} WITH (NOLOCK)
WHERE UD_TrackingNumber IN ({slots})
"""

BOX_MANY_QUERY = """
SELECT UD_TrackingNumber, BoxCode
FROM Erp_Box WITH (NOLOCK)
WHERE UD_TrackingNumber IN ({slots})
"""

# `fetch_many` gibi satir satir degil, TOPLAM adet ister (raporlar icin —
# beden/barkod detayi degil, koli basina kac urun var). GROUP BY ile MSSQL
# tarafinda toplanir; yuzlerce satir yerine yuzlerce SAYI doner.
CONTENTS_QTY_QUERY = """
SELECT UD_TrackingNumber, SUM(qty) AS total_qty
FROM {view} WITH (NOLOCK)
WHERE UD_TrackingNumber IN ({slots})
GROUP BY UD_TrackingNumber
"""

# Ayni ozet, AMA takip numarasi suzgeci YOK: gorunumun TAMAMI tek seferde
# gruplanir. Cok sayida koli sorulurken 500'luk `IN (...)` listeleri felaket
# yavas kaliyor (canli olcum, 2026-09-01: 1488 koli icin 6 parcali sorgu
# 81 SANIYE surdu; ayni veriyi iki gorunumden suzgecsiz cekmek 2 SANIYE).
# MSSQL uzun `IN` listesinde indeksi birakip her parca icin yeniden tarama
# yapiyor; tek GROUP BY ise gorunumu bir kez okuyor.
CONTENTS_QTY_ALL_QUERY = """
SELECT UD_TrackingNumber, SUM(qty) AS total_qty
FROM {view} WITH (NOLOCK)
WHERE UD_TrackingNumber IS NOT NULL
GROUP BY UD_TrackingNumber
"""

# Bu esigin USTUNDE gorunumun tamami cekilip Python'da suzulur, altinda
# `IN (...)` listesi kullanilir. Az sayida koli icin suzgecli sorgu hala
# ucuzdur (gorunumun tamamini okumaya gerek yok).
QTY_FULL_SCAN_THRESHOLD = 200

# MSSQL bir sorguda ~2100 parametre kabul eder. 500 hem o sinirin cok altinda
# hem de 200 kolilik en buyuk secimi TEK gidiste bitirir.
CHUNK = 500

# Koli barkodu (`BoxCode`, 13 haneli) sezon gorunumlerinde YOK, yalnizca taban
# tabloda var — bu yuzden ayri ve kucuk bir sorgu. Tablo adi sabit, kullanici
# girdisi degil; takip no yine parametrelenir.
BOX_QUERY = """
SELECT TOP 1 BoxCode
FROM Erp_Box WITH (NOLOCK)
WHERE UD_TrackingNumber = %s
"""

# Bedenler ALFABETIK siralanamaz — "L < M < S < XL" cikardi. Sira burada elle
# verilir; listede olmayan bir kod (yeni beden semasi) sona alfabetik eklenir,
# tablo bozulmaz.
SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "O/S"]

# Bu sutunlar koli bos bile olsa cizilir. Kullanici istedi: beden cetveli her
# koside AYNI okunsun, "XS'ten XXL'e". Yalnizca gecen bedenleri basmak her
# koliyi farkli genislikte gosteriyordu ve iki koli goz ile karsilastirilamiyordu.
BASE_SIZES = ["XS", "S", "M", "L", "XL", "XXL"]


def _text(value) -> str:
    """`clean_cell` DEGIL: o `/` karakterini siliyor ve "A/S" -> "AS" oluyordu.

    Renk ve beden kodlarinda egik cizgi anlamli ("O/S" = one size), bozulamaz.
    """
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "null") else text


def _as_int(value) -> int:
    """`qty` MSSQL'den `Decimal('2.00000000')` olarak geliyor -> 2."""
    if isinstance(value, Decimal):
        return int(value.to_integral_value())
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _size_key(size: str) -> tuple[int, str]:
    """Bilinen bedenler `SIZE_ORDER` sirasinda, bilinmeyenler sonda alfabetik."""
    upper = size.upper()
    if upper in SIZE_ORDER:
        return (SIZE_ORDER.index(upper), "")
    return (len(SIZE_ORDER), upper)


def views_for(season: str) -> list[str]:
    """Denenecek gorunumler; PARCANIN kendi sezonu once.

    Yapilandirmada birden fazla sezon acik olabiliyor (gecis donemi). Sirasiz
    denemek FA26 parcasi icin once SP26'ya gitmek demekti — bos donen bir sorgu
    ve gereksiz bir gidis-gelis.
    """
    views = config.season_views()
    code = (season or "").upper()
    if not code:
        return views
    mine = [v for v in views if code in v.upper()]
    return mine + [v for v in views if v not in mine]


def _row(row: dict) -> dict:
    """MSSQL satiri -> panelin/Excel'in kullandigi sozluk. TEK yerde: tekil ve
    toplu okuma ayni alan adlarini uretmeli, yoksa biri duzeltilip digeri unutulur."""
    return {
        "barcode": _text(row.get("Barcode")) or _text(row.get("BCH_UPC")),
        "box_no": _text(row.get("UD_SPS")),
        "qty": _as_int(row.get("qty")),
        "style": _text(row.get("StyleNumber")),
        "color": _text(row.get("StyleColor")),
        "size": _text(row.get("StyleSize")),
    }


def _chunks(values: list[str]) -> list[list[str]]:
    return [values[i:i + CHUNK] for i in range(0, len(values), CHUNK)]


def _slots(count: int) -> str:
    """`%s, %s, %s` — takip numaralari SQL'e GOMULMEZ, hepsi parametredir."""
    return ", ".join(["%s"] * count)


def fetch_contents(client: ERPClient, tracking_no: str, season: str = "") -> list[dict]:
    """Kolinin satirlari. Ilk DOLU donen gorunum kazanir, digerleri denenmez."""
    for view in views_for(season):
        rows = client.query(
            CONTENTS_QUERY.format(view=safe_identifier(view)), (tracking_no,))
        if rows:
            return [_row(row) for row in rows]
    return []


def fetch_many(client: ERPClient, tracking_nos) -> dict[str, list[dict]]:
    """Bircok kolinin icerigi: {takip no: satirlar}. Icerigi cikmayan koli
    sonucta YER ALMAZ — cagiran onu "kayit yok" diye isaretler.

    Gorunum secimi tekil sorgudan FARKLI calisir: orada "ilk dolu gorunum
    kazanir" dogru, burada degil — bir secimde FA26 ve SP26 kolileri birlikte
    olabilir. Her gorunumden sonra BULUNANLAR bekleyen kumeden dusurulur,
    kalanlar bir sonraki gorunume sorulur.
    """
    pending = [no for no in dict.fromkeys(tracking_nos) if no]
    found: dict[str, list[dict]] = {}

    for view in config.season_views():
        if not pending:
            break
        table = safe_identifier(view)
        for chunk in _chunks(pending):
            sql = CONTENTS_MANY_QUERY.format(view=table, slots=_slots(len(chunk)))
            for row in client.query(sql, tuple(chunk)):
                key = _text(row.get("UD_TrackingNumber"))
                if key:
                    found.setdefault(key, []).append(_row(row))
        pending = [no for no in pending if no not in found]

    return found


def total_quantities(client: ERPClient, tracking_nos) -> dict[str, int]:
    """Bircok kolinin TOPLAM urun adedi: {takip no: adet}. `fetch_many`in
    ozet hali — rapor kartlarinda beden/barkod degil tek bir sayi gerekir,
    yuzlerce satir cekip Python'da toplamak yerine MSSQL GROUP BY yapar."""
    pending = [no for no in dict.fromkeys(tracking_nos) if no]
    found: dict[str, int] = {}
    # Buyuk kumelerde gorunumun TAMAMI tek sorguda gruplanir; kucuk kumelerde
    # `IN (...)` listesi kalir. Gerekce ve olcum: CONTENTS_QTY_ALL_QUERY.
    full_scan = len(pending) > QTY_FULL_SCAN_THRESHOLD

    for view in config.season_views():
        if not pending:
            break
        table = safe_identifier(view)
        if full_scan:
            wanted = set(pending)
            sql = CONTENTS_QTY_ALL_QUERY.format(view=table)
            for row in client.query(sql):
                key = _text(row.get("UD_TrackingNumber"))
                if key in wanted:
                    found[key] = _as_int(row.get("total_qty"))
        else:
            for chunk in _chunks(pending):
                sql = CONTENTS_QTY_QUERY.format(view=table, slots=_slots(len(chunk)))
                for row in client.query(sql, tuple(chunk)):
                    key = _text(row.get("UD_TrackingNumber"))
                    if key:
                        found[key] = _as_int(row.get("total_qty"))
        pending = [no for no in pending if no not in found]

    return found


def fetch_box_codes(client: ERPClient, tracking_nos) -> dict[str, str]:
    """Kolilerin kendi barkodlari: {takip no: BoxCode}. Bulunamayan yer almaz."""
    codes: dict[str, str] = {}
    values = [no for no in dict.fromkeys(tracking_nos) if no]
    for chunk in _chunks(values):
        sql = BOX_MANY_QUERY.format(slots=_slots(len(chunk)))
        for row in client.query(sql, tuple(chunk)):
            key, code = _text(row.get("UD_TrackingNumber")), _text(row.get("BoxCode"))
            if key and code:
                codes.setdefault(key, code)
    return codes


def fetch_box_code(client: ERPClient, tracking_no: str) -> str:
    """Kolinin kendi barkodu. Bulunamazsa bos doner — icerik tablosu onsuz da cizilir."""
    rows = client.query(BOX_QUERY, (tracking_no,))
    return _text(rows[0].get("BoxCode")) if rows else ""


def build_matrix(rows: list[dict]) -> dict:
    """Satirlari model+renk kirilimli beden matrisine cevirir.

    Sutunlar `BASE_SIZES` (XS-XXL) + o kolide gecen diger bedenler.

    Barkodlar satirin altina LISTELENMEZ: her beden ayri bir barkod demek, bir
    model icin alt alta bes-alti numara cikiyordu ve tablo okunmaz oluyordu
    (kullanici sikayeti). Barkod artik hucrenin `bc` esleminde durur, ekranda
    yalnizca uzerine gelince gorunur.
    """
    groups: dict[tuple[str, str], dict] = {}
    extras: set[str] = set()

    for row in rows:
        # ERP gorunumunde bazi satirlarin model/renk/beden alanlari BOS geliyor
        # (canlida 38120177012637'nin 4 barkodu boyle). Bos birakilirsa hepsi
        # tek bir ADSIZ satirda toplaniyor ve kullanici 4 adedin nereden geldigini
        # goremiyordu. Barkod hic olmazsa depoda ve ERP'de aranabilir.
        style = row["style"] or row["barcode"]
        key = (style, row["color"])
        group = groups.setdefault(key, {
            "style": style, "color": row["color"],
            "qty": {}, "bc": {}, "total": 0,
        })
        size = row["size"]
        # Bos beden de bir sutundur ("—"): aksi halde o satirin adedi hicbir
        # sutuna dusmez ve toplam ile govde tutmaz.
        if size.upper() not in BASE_SIZES:
            extras.add(size)
        group["qty"][size] = group["qty"].get(size, 0) + row["qty"]
        group["total"] += row["qty"]
        if row["barcode"]:
            group["bc"].setdefault(size, row["barcode"])

    sizes = BASE_SIZES + sorted(extras, key=_size_key)
    # Uc sayi UC AYRI seydir ve kullanici bunlari esit sandi ("28 barcode lines"
    # ile toplam 34 neden tutmuyor?): `models` kac model+renk, `lines` kac ayri
    # barkod, `total` kac PARCA. Ekranda ve Excel'de birimleriyle yazilirlar.
    return {
        "sizes": sizes,
        "groups": sorted(groups.values(), key=lambda g: (g["style"], g["color"])),
        "box_no": next((r.get("box_no", "") for r in rows if r.get("box_no")), ""),
        "models": len(groups),
        "total": sum(r["qty"] for r in rows),
        "lines": len(rows),
    }
