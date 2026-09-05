"""Kargo/parcel SQLite deposu.

parcels: temel bilgiler + son bilinen statu
status_events: her statu degisikligi (denetim izi)
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import i18n
import timez
from db.migrate import ensure_columns, record_migration


# Kaynak adinin icindeki sezon kodu: "Vision_FA26_Boxed_EUROPE" -> "FA26".
# `erp.sync`ten alinmaz: bu katman ERP'yi import etmez (ters yon dairesel olur).
_SEASON_IN_NAME = re.compile(r"(?:FA|SP|SS|AW|FW)\d{2}", re.IGNORECASE)

DB_DIR = Path.home() / ".gls_pod"
DB_DIR.mkdir(exist_ok=True)
DEFAULT_DB = DB_DIR / "shipments.db"

# Basitlestirilmis statu evreni (rozetlerle 1:1)
# `returned` (iade) ayri bir statudur, `exception`in alt turu DEGIL: koli fiilen
# gondericiye geri donuyor — musteri gorunurlugu, panel karti ve mail icin
# gumruk/hasar gibi genel bir sorundan ayri sayilir (bkz. scheduler.RETURN_PHRASES).
STATUS_CHOICES = ["created", "in_transit", "out_for_delivery", "delivered",
                  "exception", "returned", "cancelled"]

# Tek bir olay metninden statu turetmenin TEK yeri (`classify_event_text`).
# Once burada VE tracking/scheduler.py'de ayri ayri, birbirinden farkli
# kelime listeleri vardi: "stored in the parcel center" ve "not out for
# delivery" gibi ifadeler statu gecmisi satirlarinda (burasi) "Exception"
# gosterirken, sag paneldeki genel rozette (scheduler.py, "out for delivery"
# alt dizesini yakalayan daha zayif kontrol yuzunden) "Out for Delivery"
# gosteriyordu. scheduler.status_from_event artik bu fonksiyonu cagirir.
EXCEPTION_PHRASES = [
    "could not be delivered", "not delivered", "niet afgeleverd", "niet bezorgd",
    "not out for delivery", "refused", "absent",
    # Cıplak "address" DEGIL: bu, "Change completed for Delivery address" gibi
    # rutin/zararsiz olaylari da yakalayip yanlislikla "exception" yapiyordu
    # (canli olcum, 2026-08-26: iade edilmis 3 koli bu yuzden "returned"
    # yerine "exception" gorunuyordu — iade sonrasi GLS bu kayit-kapatma
    # olayini yaziyor). Yalnizca GERCEKTEN sorunlu adres ifadeleri kalsin.
    "address information is needed", "incorrect address", "invalid address",
    "incomplete address", "closed", "holiday",
    "niet in levering", "in bewaring", "opgeslagen", "stored", "damage", "exception",
    "unable to deliver", "failed", "incorrect", "problem",
]
# GLS'in "bugun olmadi, ERTESI IS GUNU tekrar denenecek" bildirimi. Metin
# "Not delivered" ile BASLADIGI icin `EXCEPTION_PHRASES`teki "not delivered"a
# takiliyor ve rutin bir yeniden deneme sorun gibi gorunuyordu (canli olcum,
# 2026-08-31: 38120177020458/020465 — GLS 28 Agu'da "delivery planned next
# workingday" demis, panelde "Sorunlu" duruyorlardi; kullanici bildirdi:
# "sorun gozuken ama aslinda sorun olmayanlar var"). Koli normal akista.
# Koli HENUZ GLS'e GECMEDI: etiket basildi/duyuruldu ama tasiyici fiilen
# almadi. Bu metinler `in_transit` kontrolunden ONCE bakilir, cunku
# IN_TRANSIT_PHRASES'teki "handed over" alt dizesi "not yet handed over"
# ifadesini de yakalayip OLUMSUZLAMAYI yok sayiyordu.
#
# Canli olcum (2026-09-01, kullanici bildirdi: "bugun olusturulan koliler neden
# in transit gorunuyor, bir kismi 4'unde cikacak"): o gun basilan 72 etiketin
# hepsi `in_transit` gorunuyordu; GLS'in metni "provided by the sender for
# collection by GLS" idi, yani koli hala BIZDE, GLS 4 Eylul'de alacak.
NOT_YET_HANDED_OVER_PHRASES = [
    "not yet handed over",
    "was entered into the gls",
    "provided by the sender for collection",
    # FedEx karsiligi: bilgi gonderildi, koli HENUZ ALINMADI (canli olcum,
    # 2026-09-01: 876572877080 panelde "Yolda" gorunuyordu).
    "shipment information sent to",
]

RETRY_PLANNED_PHRASES = ["delivery planned next workingday",
                          "delivery planned next working day"]

RETURN_TEXT_PHRASES = [
    "returned to sender", "return to sender", "parcel returned",
    # ParcelShop'ta saklama suresi DOLDU = koli GONDERICIYE geri gidiyor.
    # GLS bunun icin ayrica "returned to sender" YAZMIYOR (canli olcum,
    # 2026-08-31: 38120177007589/BGRES1N — 19 Agu ParcelShop'a teslim, musteri
    # almadi, 26 Agu "maximum storage time"; panel hala "exception" diyordu,
    # oysa koli fiilen iadeye donmustu — kullanici bildirdi).
    "maximum storage time",
]
OUT_FOR_DELIVERY_PHRASES = [
    "expected to be delivered", "out for delivery", "geladen voor aflevering",
    "in delivery", "unterwegs", "in bezorging", "in distributie",
    # FedEx (canli olcum, 2026-08-31: 876365567966) tarama metni GLS'inkinden
    # farkli — "out for delivery" ifadesini HIC kullanmiyor, kendi
    # latestStatusDetail'i oyle dese bile. `map_fedex_result` tarama
    # metnini (latestStatusDetail'den once) tercih ettigi icin bu ifade
    # olmadan kargo yanlislikla "in_transit"e dusuyordu.
    "on fedex vehicle for delivery", "on vehicle for delivery",
]
DELIVERED_PHRASES = ["has been delivered", "teslim edildi", "afgeleverd", "is delivered",
                     "delivered to"]
IN_TRANSIT_PHRASES = [
    "handed over", "received", "left", "reached", "transit", "depot", "aangekomen",
    "doorgestuurd", "ontvangen", "onderweg", "hub", "change completed", "scanned",
    "weight", "sorted",
]
CREATED_PHRASES = ["entered", "aangekondigd", "created", "registered", "data received"]


def classify_event_text(desc: str, desc_nl: str = "") -> str:
    """GLS olay metnini bizim 7 statulu evrenimize esler (`STATUS_CHOICES`).

    Sira onemli: "Not out for delivery" gibi olumsuz metinler "out for
    delivery" alt dizesini icerir, bu yuzden RETURN/EXCEPTION kontrolleri
    OUT_FOR_DELIVERY kontrolunden ONCE yapilir.
    """
    low = (desc + " " + (desc_nl or "")).strip().lower()
    if not low:
        return "created"
    if any(p in low for p in RETURN_TEXT_PHRASES):
        return "returned"
    # "Not delivered - delivery planned next workingday" EXCEPTION'dan ONCE:
    # metin "not delivered" iceriyor ama GLS zaten yeniden denemeyi planlamis.
    if any(p in low for p in RETRY_PLANNED_PHRASES):
        return "in_transit"
    # GLS koliyi henuz ALMADI -> "olusturuldu". IN_TRANSIT'ten ONCE gelmeli.
    if any(p in low for p in NOT_YET_HANDED_OVER_PHRASES):
        return "created"
    if any(p in low for p in EXCEPTION_PHRASES):
        return "exception"
    if any(p in low for p in OUT_FOR_DELIVERY_PHRASES):
        return "out_for_delivery"
    if (any(p in low for p in DELIVERED_PHRASES) or low == "delivered") and not any(
        w in low for w in ("not", "could not", "except", "cancel", "return", "holiday")
    ):
        return "delivered"
    if any(p in low for p in IN_TRANSIT_PHRASES):
        return "in_transit"
    if any(p in low for p in CREATED_PHRASES):
        return "created"
    return "in_transit"


def status_label(code: str, lang: str = "") -> str:
    """Rozetlerde ve Excel'de gorunen statu adi — metin `i18n` sozlugunde.

    Eskiden burada bir sozluk vardi; iki dil gelince metin iki yerde tutulmus
    olacakti. `lang` yalnizca arka plan isleri icin (mail/Excel), panelde bos
    birakilir ve istegin dili kullanilir.
    """
    return i18n.t(f"status.{code}", lang) if code in STATUS_CHOICES else code


def delivered_note(receiver: str, lang: str = "tr") -> str:
    """"Teslim edildi, teslim alan: X" — GLS'in DEGIL, bizim yazdigimiz tek olay metni.

    Veritabanina daima TR yazilir (canli DB'de yuzlerce satir boyle); dil
    anahtari `event_text()` ile okuma aninda uygulanir.
    """
    return i18n.t("event.delivered_to", lang, name=receiver)


def event_text(text: str, lang: str = "") -> str:
    """Kayitli olay metnini istenen dile getirir.

    GLS'in kendi olay metinleri zaten Ingilizce ve oldugu gibi gecer; yalnizca
    yukaridaki teslim notu cevrilir. Onek sozlukten TURETILIR, elle
    tekrarlanmaz — TR metni degisirse bu islev kendiliginden uyar.
    """
    prefix = delivered_note("")
    if text.startswith(prefix):
        return delivered_note(text[len(prefix):], lang)
    return text


def event_stamp(value: str | None) -> str:
    """`2026-08-14T19:48:42.724Z` -> `14.08.2026 22:48`. Bos/bozuksa bos metin.

    2026-08-19 KARARI: saat Europe/Istanbul'a CEVRILIR. Onceki davranis UTC'yi
    oldugu gibi basiyordu — panelde her saat 3 saat geride gorunuyordu.
    `erp/writeback.py:_delivery_date` de ayni turda cevrilmeye baslandi, ikisi
    tutarli kaldi.

    Bu tek islev UC yeri birden besliyor: panelin sorunlu listesi, ERP'nin
    `UD_TasimaAciklama` sutunu, gunluk ozetin Excel eki.
    """
    return timez.stamp(value)


def with_stamp(text: str, when: str | None = None) -> str:
    """Metnin sonuna son islem tarihini ekler: `... parcel center. · 14.08.2026 22:48`.

    Aciklamanin gorundugu UC yer (ERP'nin `UD_TasimaAciklama` sutunu, gunluk
    ozetin Excel eki, panelin sorunlu listesi) buradan gecer; aksi halde ayni
    ayrac uc yerde ayri ayri kurulur ve zamanla ayrisir. Tarih yoksa metin
    oldugu gibi doner — bos bir ayrac birakilmaz.
    """
    stamp = event_stamp(when)
    return f"{text} · {stamp}" if text and stamp else text


def explain_text(text: str, when: str | None = None, lang: str = "") -> str:
    """Cevrilmis aciklama + son islem tarihi. Panel ve Excel icin.

    ERP bunu KULLANMAZ: oraya GLS'in ham metni gitmeli (bkz. `_TRACKING_FIELDS`
    yorumu), ceviri yalnizca bizim ekranlarimiza aittir — `with_stamp` dogrudan
    cagrilir.
    """
    return with_stamp(event_text(text, lang), when)


STATUS_COLOR = {
    "created": "slate",
    "in_transit": "blue",
    "out_for_delivery": "amber",
    "delivered": "emerald",
    "exception": "rose",
    "returned": "violet",
    "cancelled": "slate",
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS parcels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_no TEXT NOT NULL UNIQUE,
    reference TEXT,
    channel TEXT NOT NULL,          -- 'NL' | 'IE'
    country TEXT,
    consignee_name TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    last_checked_at TEXT,
    pod_path TEXT,
    invoice_number TEXT,
    emc_invoices TEXT,
    store_code TEXT,
    zip_code TEXT,
    sales_rep TEXT,
    shipment_method TEXT,
    shipment_date TEXT,
    delivered_date TEXT,
    weight_kg REAL,
    freight_cost REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parcels_status ON parcels(status);
CREATE INDEX IF NOT EXISTS idx_parcels_channel ON parcels(channel);
CREATE INDEX IF NOT EXISTS idx_parcels_country ON parcels(country);
CREATE INDEX IF NOT EXISTS idx_parcels_sales_rep ON parcels(sales_rep);

CREATE TABLE IF NOT EXISTS status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parcel_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL,
    FOREIGN KEY (parcel_id) REFERENCES parcels(id) ON DELETE CASCADE
);

-- parcel_id UZERINDE INDEX SART: `status_events` canli DB'de ~46 000 satir ve
-- her `parcels JOIN status_events` sorgusu indekssiz NESTED LOOP'a dusuyordu.
-- `parcel_shop_parcels` (ana panelde HER acilista cagriliyor) NL kanalinda
-- ~1900 parca x 46 000 olay = ~90 milyon satir taramasi -> tek sorgu 47 SANIYE
-- (canli olcum 2026-09-03). `async def` handler'da dogrudan calistigi icin bu
-- sure boyunca TUM event loop donuyordu — "site donuyor"un baslica sebebi.
CREATE INDEX IF NOT EXISTS idx_status_events_parcel_id ON status_events(parcel_id);

-- Tarayicinin sagligi. TEK satir (CHECK id = 1) — gecmis tutulmuyor, sorulan
-- soru "en son ne zaman calisti" ve o tek deger.
--
-- Bellekte DEGIL diskte: `Tracker` bu alanlari zaten oznitelik olarak tutuyordu
-- ama konteyner yeniden basladiginda siliniyorlardi. Surekli acilip taramada
-- patlayan bir sistem her restartta "yeni dogmus" gorunur ve 6 saatlik esige
-- hicbir zaman ulasmazdi — uyari da hic gitmezdi.
CREATE TABLE IF NOT EXISTS tracker_health (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_run_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    alerted_at TEXT
);

-- `tracker_health.last_error` TEK satirdir: bir sonraki BASARILI tur onu
-- temizler. Bu, GLS NL'nin 20'serli gruplarindan biri hata verdiginde (agin
-- kesilmesi, GLS'in hiz siniri) o turdaki hatanin izini kaybettiriyordu —
-- kalan gruplar basarili oldugu icin tur "basarili" sayiliyor (`ok=answered>0`),
-- 15 dakika sonraki bir sonraki tur da temiz gelirse operator hicbir zaman
-- "bir grup koli sorgulanamadi" bilgisini goremiyordu (kullanici bildirdi,
-- 2026-08-27: "son tarama 1 dakika once ama dunun guncellemeleri dusmemis").
-- Bu tablo GECMISI tutar; 7 gunden eski kayitlar her yazimda silinir.
CREATE TABLE IF NOT EXISTS tracker_tick_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    error TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tick_errors_at ON tracker_tick_errors(at);
"""

TICK_ERROR_RETENTION_DAYS = 7

EXTRA_COLUMNS = [
    ("invoice_number", "TEXT"),
    ("emc_invoices", "TEXT"),
    ("store_code", "TEXT"),
    ("zip_code", "TEXT"),
    ("sales_rep", "TEXT"),
    ("shipment_method", "TEXT"),
    ("shipment_date", "TEXT"),
    ("delivered_date", "TEXT"),
    ("weight_kg", "REAL"),
    ("freight_cost", "REAL"),
    # POD 'pdf' (ShipIT/Track&Trace) ya da 'png' (GLS NL) olabilir.
    ("pod_media_type", "TEXT"),
    # POD icin GLS'e en son ne zaman soruldu. Her teslimatin POD'u yoktur
    # (GLS bazi parcalar icin 204 doner); isaretlenmezse o parcalar POD
    # kuyrugunun basini surekli isgal eder (bkz. parcels_without_pod).
    ("pod_attempted_at", "TEXT"),
    # POD'u cikmayan teslimat icin GLS'e ne zaman MAIL yazildi. Bos olanlar
    # sorulur, dolu olanlar bir daha sorulmaz: ayni takip numarasini her gun
    # yeniden yazmak GLS musteri hizmetleri gozunde spam olur (bkz. notify/
    # pod_chase.py). Elle "GLS'e sor" sayfasi bu alani DIKKATE ALMAZ.
    ("pod_chased_at", "TEXT"),
    # POD belgesinin dosya adi govdesi ("NUNAT1N-5000202"). Bir kez hesaplanip
    # saklanir: ayni faturaya sonradan koli eklendiginde eski adlar kaymasin
    # (bkz. reserve_pod_name).
    ("pod_name", "TEXT"),
    # ERP'ye (erp_Box) EN SON yazilan degerin parmak izi. `updated_at` bu is icin
    # kullanilamaz: takip turu her cevapta onu tazeliyor, statu degismese bile.
    # Parmak izi ayni kalan koli tekrar yazilmaz — 15 dakikada bir 500 satirlik
    # gereksiz UPDATE MSSQL'e yuk olurdu (bkz. erp/writeback.py).
    ("erp_pushed", "TEXT"),
    ("last_event_at", "TEXT"),
    ("last_event_text", "TEXT"),
    # GLS koliyi FIZIKSEL olarak ne zaman teslim aldi (ilk gercek tarama olayi).
    # "Takilmis kargo" bundan olculur, son olaydan DEGIL: GLS takilan bir koliye
    # de gurultu olayi yazmaya devam ediyor ("Action list item revised/normal"),
    # bu yuzden son olay tarihi tazeyken koli 14 gundur yolda olabiliyor.
    ("handed_over_at", "TEXT"),
    # Gecmiste bulunan SON sorun olayi ("...reception was closed (Madrid ES)").
    # `last_event_text` yetmez: sorun olayi gecmisin ortasinda kalabiliyor.
    ("issue_text", "TEXT"),
    ("eta_start", "TEXT"),
    ("eta_end", "TEXT"),
    ("problem_reason", "TEXT"),
    # Excel'deki EXPLAIN sutunu ve kaydin geldigi sayfa adi ("Problematic
    # Packages", "Return to Sender" ...) — sorunlu kargo ekraninda gosterilir.
    ("explain", "TEXT"),
    ("source_sheet", "TEXT"),
    # Parcanin sezonu ("FA26"). `source_sheet`ten TUREMEZ: gorunumlerin
    # kacirdiklari taban tablodan gelir ve orada kaynak adi duz "Erp_Box"tir —
    # `config.season_from_source` orada GUNCEL sezona duser, yani yeni sezon
    # acildiginda eski koliler sessizce yeni sezon etiketi alirdi. Deger ERP'nin
    # kendi `Erp_Box.UD_Season` alanindan okunur (bkz. erp/sync.py).
    ("season", "TEXT"),
    # Tek etiket uretiminde olusan PDF'in yolu (bkz. web/routers/labels.py).
    ("label_pdf_path", "TEXT"),
    ("tracking_url", "TEXT"),
    # Toplu etikette bu parcayi doguran `Erp_Box.RecId`. CIFT ETIKET KORUMASI:
    # takip numarasi ERP'ye geri yazilmadigi surece MSSQL suzgeci tek basina
    # yetmez, ayni gun ikinci kez calistirilirsa ayni koliye ikinci etiket
    # basilirdi (bkz. labeled_box_ids).
    ("box_rec_id", "TEXT"),
    # WhatsApp (OpenWA) bildirim damgalari — ayni bildirim ikinci kez gitmesin.
    ("wa_delivered_at", "TEXT"),
    ("wa_problem_at", "TEXT"),
    ("wa_returned_at", "TEXT"),
    # FedEx'te resmi POD YOK (bkz. gls_api/providers.py:pod_provider_for —
    # FEDEX icin bilincli olarak None doner). Operator bunun yerine koli
    # fotograflarini yukluyor; bu alan o fotograflardan uretilen PDF'in yolu
    # (bkz. web/box_image_document.py, kullanici istegi 2026-08-31).
    ("box_image_path", "TEXT"),
]


# Teslimat gunu (YYYY-MM-DD), Europe/Istanbul'a gore. GLS `delivered_date`i bazi
# parcalarda bos birakiyor; o zaman son olaya, o da yoksa kaydin guncellenme
# anina duselir. Tarihler 'YYYY-MM-DDTHH:MM:SSZ' ve 'YYYY-MM-DD HH:MM:SS' olarak
# karisik geliyor.
#
# `+3 hours` SART: damgalar UTC. Duz `substr(...,1,10)` UTC gununu verirdi ve
# 00:00-03:00 arasindaki teslimat kullaniciya BIR ONCEKI gunde gorunurdu.
# Sabit +3 guvenli — Turkiye 2016'dan beri yaz saati uygulamiyor ve SQLite'ta
# saat dilimi veritabani yok (Python tarafinda `timez.TZ` ZoneInfo kullanir).
# `Z` eki atilir: SQLite onu kabul etse de bicimler karisik, sadelestiriyoruz.
# Yalniz tarih iceren deger (saat yok) kaymaz: 00:00 + 3 saat ayni gundur.
_DELIVERY_DAY = (
    "substr(datetime(replace(COALESCE(NULLIF(delivered_date, ''), "
    "NULLIF(last_event_at, ''), updated_at), 'Z', ''), '+3 hours'), 1, 10)"
)

# POD kuyrugunda yeniden deneme sikligi (bkz. parcels_without_pod).
# Taze teslimat (son POD_FRESH_DAYS gun) SIK sorulur ama HER TUR DEGIL:
# POD_FRESH_RETRY_HOURS'da bir. Onceden her tur (15 dk) soruluyordu — canli
# olcum 2026-09-03: hafta sonu ~40 taze teslimatin POD'u henuz yoktu, her
# istek `apm.gls.nl`de ~3,5 sn, tur basina ~2,5 DAKIKA event loop tikanmasi,
# panel donuyordu ("yine kilitleniyor", "giremiyorum"). 2 saatte bir sorgu
# ayni gun icinde yine yakalar, yuku ~8 kat azaltir. Eskiyen parca gunde ~3.
POD_FRESH_DAYS = 2
POD_FRESH_RETRY_HOURS = 2
POD_RETRY_HOURS = 8


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# Yazma kilidi beklerken pes etme suresi. SQLite'in varsayilani 5 saniyedir ve
# bu sistemde YETMIYOR: arka plan tarayicisi bir turda yuzlerce satir yazarken
# ERP senkronu (ya da elle calistirilan bir bakim islemi) ayni dosyaya yazmak
# istiyor ve saniyeler icinde "database is locked" ile dusuyordu.
#
# Canli olcum (2026-09-01): tarayici turlari ust uste bu hatayla patladi
# (`scheduler.py:tick` -> OperationalError), hatta hatayi kaydetmeye calisan
# `record_tick` bile ayni hatayi aldi. Bekleyen yazici SIRAYA GIRMELI, hemen
# vazgecmemeli — 30 saniye, en uzun turdan bile uzun.
BUSY_TIMEOUT_SECONDS = 30


class ShipmentsDB:
    """Parca veritabani. Baglanti THREAD BASINA acilir — `conn` bir property'dir.

    NEDEN THREAD-LOCAL (canli olcum 2026-09-02): Eskiden tek bir
    `sqlite3.Connection` butun thread'lerde paylasiliyordu
    (`check_same_thread=False`). Python'un sqlite3 modulu tek bir baglantiyi
    KENDI mutex'iyle sarar; yani tarayici turu (ayri bir thread'de, 2.5-4
    dakika, yuzlerce yazma) o baglantiyi surekli mesgul ederken BASKA her
    cagiran sirada bekliyordu. `web/main.py:health()` `async def` oldugu icin
    bu bekleme dogrudan EVENT LOOP uzerinde oluyordu ve panel her turda ~3
    dakika tamamen cevapsiz kaliyordu (kullanici iki kez bildirdi; Docker
    healthcheck'i de ayni pencerede 4 kez ust uste dustu).

    WAL bunu COZMEZ: WAL dosya duzeyindeki okuyucu/yazici kilidini kaldirir,
    buradaki darbogaz ise Python baglanti nesnesinin kilidi. Iki onlem birlikte
    gerekiyor — WAL zaten aciktir (asagida), bu da ikincisi.

    Her thread kendi baglantisini alir; yazmalar `commit` edildikten sonra
    digerlerine gorunur (WAL sayesinde okuyucu beklemez). Baglantilar kapatilmaz:
    thread havuzu sinirlidir (uvicorn/anyio ~40) ve surec omru boyunca yasarlar.
    """

    def __init__(self, path: Path = DEFAULT_DB):
        self.path = Path(path)
        self._local = threading.local()
        conn = self.conn          # ilk baglanti bu thread'de kurulur
        conn.executescript(SCHEMA)
        self._migrate()
        conn.commit()

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False,
                               timeout=BUSY_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        # `timeout` yalnizca Python katmanini baglar; `busy_timeout` ayni sinir
        # SQLite'in KENDI icinde de gecerli olsun diye ayrica verilir.
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_SECONDS * 1000}")
        # WAL: okuyucular yazicilari, yazicilar okuyuculari BEKLETMEZ. Tek
        # basina `busy_timeout` yetmedi (canli olcum 2026-09-01: 60 saniyelik
        # bekleme bile "database is locked" ile dustu) cunku varsayilan rollback
        # journal kipinde tarayici tur atarken dosya boyunca yazma kapali
        # kaliyor — tur 280 saniye surebiliyor. WAL kalici bir dosya ozelligidir,
        # bir kez yazilir; sonraki acilislarda zaten WAL'dir.
        conn.execute("PRAGMA journal_mode = WAL")
        # `synchronous = NORMAL`: WAL kipinde GUVENLIDIR — uygulama cokmesine
        # dayanir, yalnizca isletim sistemi cokmesi/elektrik kesintisinde SON
        # birkac commit riske girer (takip verisi icin kabul edilebilir).
        # Varsayilan FULL her commit'te fsync yapar; tarayici turu bir turda
        # YUZLERCE commit atiyor (parca basina `mark_checked`/`update_status`)
        # ve tek-diskli VM'de bu fsync firtinasi WAL checkpoint'i tetikleyip
        # `/health` gibi OKUYUCULARI bile 20+ sn `SQLITE_BUSY` ile bekletiyordu
        # -> panel her turda donuyordu (kullanici 2026-09-03: "yine kilitleniyor").
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """Bu THREAD'e ait baglanti; yoksa acilir. Bkz. sinif aciklamasi."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_conn()
            self._local.conn = conn
        return conn

    def _migrate(self) -> None:
        """Eski DB'lere yeni kolonlari ekle (SQLite: ALTER TABLE ADD COLUMN)."""
        ensure_columns(self.conn, "parcels", EXTRA_COLUMNS)
        record_migration(self.conn, "tracking.parcels.extra_columns")
        self._backfill_seasons()

    def _backfill_seasons(self) -> None:
        """Sezon kolonundan ONCE eklenmis parcalarin sezonunu kaynak adindan doldurur.

        Kolon eklendiginde mevcut parcalarin hepsi bos kalir (canli: 994 parca) ve
        ekranda sezon sutunu bastan sona "—" gorunur. ERP senkronu onlari
        `UD_Season`dan doldurur ama yalnizca hala ERP sorgusunun kapsamindakileri.

        TAHMIN YOK: sezon kodu kaynak adinda GERCEKTEN yaziyorsa
        ("Vision_FA26_Boxed_EUROPE") yazilir, "Erp_Box" gibi adlar bos birakilir.
        Dolu satira dokunmaz — ERP'nin kendi degeri her zaman ustundur.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT source_sheet FROM parcels "
            "WHERE (season IS NULL OR season = '') "
            "AND source_sheet IS NOT NULL AND source_sheet <> ''"
        ).fetchall()
        for row in rows:
            found = _SEASON_IN_NAME.search(row["source_sheet"])
            if found:
                self.conn.execute(
                    "UPDATE parcels SET season = ? WHERE source_sheet = ? "
                    "AND (season IS NULL OR season = '')",
                    (found.group().upper(), row["source_sheet"]),
                )
        record_migration(self.conn, "tracking.parcels.season_from_source")

    # ---------- parcels ----------

    def add_parcel(
        self,
        tracking_no: str,
        channel: str,
        reference: str = "",
        country: str = "",
        consignee_name: str = "",
        status: str = "created",
        invoice_number: str = "",
        emc_invoices: str = "",
        store_code: str = "",
        zip_code: str = "",
        sales_rep: str = "",
        shipment_method: str = "",
        shipment_date: str = "",
        delivered_date: str = "",
        weight_kg: float | None = None,
        freight_cost: float | None = None,
        explain: str = "",
        source_sheet: str = "",
        season: str = "",
    ) -> int:
        now = _now()
        # Parca ZATEN VARSA olay yazilmaz. Aksi halde her yeniden senkron, teslim
        # edilmis bir kolinin zaman cizelgesine "created" satiri ekler ve gecmis
        # okunamaz hale gelir (olculdu: tek senkron 480 sahte olay uretiyordu).
        existing = self.conn.execute(
            "SELECT id FROM parcels WHERE tracking_no = ?", (tracking_no,)
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO parcels
                (tracking_no, reference, channel, country, consignee_name, status,
                 invoice_number, emc_invoices, store_code, zip_code, sales_rep,
                 shipment_method, shipment_date, delivered_date, weight_kg, freight_cost,
                 explain, source_sheet, season, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracking_no) DO UPDATE SET
                explain         = COALESCE(NULLIF(excluded.explain, ''), explain),
                source_sheet    = COALESCE(NULLIF(excluded.source_sheet, ''), source_sheet),
                season          = COALESCE(NULLIF(excluded.season, ''), season),
                reference       = COALESCE(NULLIF(excluded.reference, ''), reference),
                consignee_name  = COALESCE(NULLIF(excluded.consignee_name, ''), consignee_name),
                invoice_number  = COALESCE(NULLIF(excluded.invoice_number, ''), invoice_number),
                emc_invoices    = COALESCE(NULLIF(excluded.emc_invoices, ''), emc_invoices),
                store_code      = COALESCE(NULLIF(excluded.store_code, ''), store_code),
                zip_code        = COALESCE(NULLIF(excluded.zip_code, ''), zip_code),
                sales_rep       = COALESCE(NULLIF(excluded.sales_rep, ''), sales_rep),
                shipment_method = COALESCE(NULLIF(excluded.shipment_method, ''), shipment_method),
                shipment_date   = COALESCE(NULLIF(excluded.shipment_date, ''), shipment_date),
                delivered_date  = COALESCE(NULLIF(excluded.delivered_date, ''), delivered_date),
                weight_kg       = COALESCE(excluded.weight_kg, weight_kg),
                freight_cost    = COALESCE(excluded.freight_cost, freight_cost),
                updated_at      = excluded.updated_at
            """,
            (
                tracking_no, reference, channel, country, consignee_name, status,
                invoice_number, emc_invoices, store_code, zip_code, sales_rep,
                shipment_method, shipment_date, delivered_date, weight_kg, freight_cost,
                explain, source_sheet, season, now, now,
            ),
        )
        self.conn.commit()
        if existing:
            return existing["id"]
        row = self.conn.execute(
            "SELECT id FROM parcels WHERE tracking_no = ?", (tracking_no,)
        ).fetchone()
        pid = row["id"]
        self._add_event(pid, status, note="created")
        # COMMIT SART: `_add_event` yalnizca INSERT eder, commit'i cagirana
        # birakir (bkz. `update_status`). Burada unutulmustu ve yeni parca
        # eklenen her cagri baglantida ACIK bir yazma islemi birakiyordu —
        # SQLite'ta bu, dosyayi butun diger yazicilara KAPATIR.
        #
        # Canli sonucu (2026-09-01): ERP senkronu ilk yeni koliyi ekler eklemez
        # kilidi tutuyor, ardindan tarayici turlari ve elle calistirilan her
        # islem "database is locked" ile dusuyordu — hatayi kaydetmeye calisan
        # `record_tick` bile. 60 saniyelik `busy_timeout` bile ise yaramadi,
        # cunku kilit GECICI degil KALICIYDI.
        self.conn.commit()
        return pid

    def update_status(self, tracking_no: str, new_status: str, note: str = "",
                       log_event: bool = True) -> bool:
        """Sadece degistiyse update + event kaydeder.

        `log_event=False`: NL kanalinda `sync_events()` GLS'in TAM olay
        gecmisini (kendi zaman damgasi + ham metniyle) zaten yazdi; burada
        AYRICA `_now()` damgali, farkli bicimlenmis (depo eklenmemis / bazen
        Turkce'ye cevrilmis) bir kopya yazmak ayni gercek olayi iki kez
        gosterirdi (canli ornek: "delivered" olayi hem "Teslim edildi, teslim
        alan: X" hem GLS'in "has been delivered (X) — depot" metniyle, iki
        ayri saatte, statu gecmisinde YAN YANA gorunuyordu).
        """
        row = self.conn.execute(
            "SELECT id, status FROM parcels WHERE tracking_no = ?", (tracking_no,)
        ).fetchone()
        if not row:
            return False
        if row["status"] == new_status:
            self.conn.execute(
                "UPDATE parcels SET last_checked_at = ? WHERE id = ?",
                (_now(), row["id"]),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            "UPDATE parcels SET status = ?, last_checked_at = ?, updated_at = ? WHERE id = ?",
            (new_status, _now(), _now(), row["id"]),
        )
        if log_event:
            self._add_event(row["id"], new_status, note=note)
        self.conn.commit()
        return True

    def set_pod_path(self, tracking_no: str, pod_path: str, media_type: str = "pdf") -> None:
        self.conn.execute(
            "UPDATE parcels SET pod_path = ?, pod_media_type = ?, updated_at = ? "
            "WHERE tracking_no = ?",
            (pod_path, media_type, _now(), tracking_no),
        )
        self.conn.commit()

    def set_box_image_path(self, tracking_no: str, path: str) -> None:
        """FedEx icin elle yuklenen koli fotograflarindan uretilen PDF'in yolu
        (bkz. web/box_image_document.py) — `pod_path`in FedEx'teki karsiligi."""
        self.conn.execute(
            "UPDATE parcels SET box_image_path = ?, updated_at = ? WHERE tracking_no = ?",
            (path, _now(), tracking_no),
        )
        self.conn.commit()

    def set_label_path(self, tracking_no: str, label_path: str,
                       box_rec_id: str = "") -> None:
        """Toplu etiketin ikizi: uretilen PDF'in yolu + kaynak koli kaydi."""
        self.conn.execute(
            "UPDATE parcels SET label_pdf_path = ?, "
            "box_rec_id = COALESCE(NULLIF(?, ''), box_rec_id), updated_at = ? "
            "WHERE tracking_no = ?",
            (label_path, box_rec_id, _now(), tracking_no),
        )
        self.conn.commit()

    def labeled_box_ids(self, day: str) -> set[str]:
        """Verilen paketleme gununde etiketi ZATEN basilmis koli kayit no'lari.

        Toplu etiket ayni gun icin ikinci kez calistirilirsa bu koliler plandan
        dusurulur — etiket geri alinamaz, cift basmak koliyi iki kez faturalatir.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT box_rec_id FROM parcels "
            "WHERE shipment_date = ? AND box_rec_id IS NOT NULL AND box_rec_id <> ''",
            (day,),
        ).fetchall()
        return {r["box_rec_id"] for r in rows}

    def delete_parcel(self, tracking_no: str) -> bool:
        """Parcayi ve TUM izlerini veritabanindan siler; yoksa False.

        Kullanici karari (2026-09-01): *"biz labeli silersek bizden komple
        silinsin, GLS'te kalabilir ama bizim db'de hic olmamasi lazim, o
        yuzden sorgular koliden olmali"*. Yani silinen etiket `cancelled`
        statusuyle DURMAZ, kayit tamamen kalkar.

        Bunun ONEMLI bir yan etkisi vardir ve BILEREK istenmistir: `box_rec_id`
        de gittigi icin koli `labeled_box_ids` suzgecinden duser ve toplu
        etikette YENIDEN gorunur — etiketi silinmis bir koli zaten yeniden
        etiketlenebilmelidir. Uygunlugun asil kaynagi ERP'deki kolinin kendisi
        (`Erp_Box.UD_TrackingNumber` bos mu), bizim tablomuz degil.

        `status_events` elle silinir: semada `ON DELETE CASCADE` yazili olsa da
        SQLite bunu ancak `PRAGMA foreign_keys = ON` ile uygular ve baglanti
        onu acmiyor — yoksa olaylar oksuz satirlar olarak kalirdi.
        """
        row = self.conn.execute(
            "SELECT id FROM parcels WHERE tracking_no = ?", (tracking_no,)
        ).fetchone()
        if not row:
            return False
        self.conn.execute("DELETE FROM status_events WHERE parcel_id = ?", (row["id"],))
        self.conn.execute("DELETE FROM parcels WHERE id = ?", (row["id"],))
        self.conn.commit()
        return True

    def reserve_pod_name(self, tracking_no: str, base: str) -> str:
        """POD dosya adinin govdesini bir kez hesaplar ve saklar.

        Kullanici adin `{MAGAZA}-{FATURA}` olmasini istiyor ama bir fatura
        birden cok koli tasiyabiliyor: canlida 480 koliye karsilik yalnizca 248
        farkli (magaza, fatura) cifti var, en buyugu 34 koli. Cok koliliyse ada
        iki haneli sira numarasi eklenir.

        Ad KALICIDIR: sonradan ayni faturaya koli eklenirse eski adlar kaymaz,
        yeni koli bir sonraki BOS numarayi alir. Aksi halde daha once musteriye
        gonderilmis bir belgenin adi degisirdi.
        """
        row = self.conn.execute(
            "SELECT pod_name, store_code, invoice_number FROM parcels WHERE tracking_no = ?",
            (tracking_no,),
        ).fetchone()
        if row is None:
            return base
        if row["pod_name"]:
            return row["pod_name"]

        name = base
        store, invoice = row["store_code"] or "", row["invoice_number"] or ""
        siblings = 0
        if store and invoice:
            siblings = self.conn.execute(
                "SELECT COUNT(*) FROM parcels WHERE store_code = ? AND invoice_number = ?",
                (store, invoice),
            ).fetchone()[0]
        if siblings > 1:
            taken = {r["pod_name"] for r in self.conn.execute(
                "SELECT pod_name FROM parcels WHERE pod_name LIKE ?", (f"{base}-%",))}
            number = 1
            while f"{base}-{number:02d}" in taken:
                number += 1
            name = f"{base}-{number:02d}"

        self.conn.execute("UPDATE parcels SET pod_name = ? WHERE tracking_no = ?",
                          (name, tracking_no))
        self.conn.commit()
        return name

    # Takip taramasinin yazdigi, statuden bagimsiz alanlar.
    # `explain` = GLS'in KENDI son olay metni (Ingilizce, dokunulmamis). Panelde
    # gosterilen `last_event_text` teslimatta Turkce'ye cevriliyor; ERP'ye
    # (erp/writeback.py) GLS'in ham metni yazilsin diye ayri tutuluyor.
    _TRACKING_FIELDS = ("last_event_at", "last_event_text", "eta_start", "eta_end",
                        "problem_reason", "delivered_date", "explain",
                        "handed_over_at", "issue_text", "tracking_url")

    # Bos deger de YAZILIR. Sorun cozulunce (koli sonunda teslim edildi) alan
    # temizlenmezse parca sonsuza dek sorunlu listesinde kalir.
    _CLEARABLE_FIELDS = ("issue_text",)

    def update_tracking_info(self, tracking_no: str, **fields) -> None:
        """Son olay / ETA / sorun sebebi gibi alanlari gunceller (bos degerler atlanir)."""
        updates = {k: v for k, v in fields.items()
                   if k in self._TRACKING_FIELDS and (v or k in self._CLEARABLE_FIELDS)}
        unknown = set(fields) - set(self._TRACKING_FIELDS)
        if unknown:
            raise TypeError(f"Bilinmeyen takip alani: {', '.join(sorted(unknown))}")
        if not updates:
            return
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(
            f"UPDATE parcels SET {assignments}, updated_at = ? WHERE tracking_no = ?",
            (*updates.values(), _now(), tracking_no),
        )
        self.conn.commit()

    def get(self, tracking_no: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM parcels WHERE tracking_no = ?", (tracking_no,)
        ).fetchone()
        return dict(row) if row else None

    # Serbest metin (LIKE) ile filtrelenebilir tum sutunlar — tek satirlik alan ->
    # DB kolonu eslemesi. Yeni bir filtrelenebilir alan eklemek icin sadece buraya
    # eklemek yeterli (bkz. web/routers/tracking.py, web/templates/tracking.html).
    # Tek kutuluk genel arama hangi kolonlara bakar. Magaza kodu ve fatura no
    # SONRADAN eklendi: kullanici numarayi degil "AHAAT01"i biliyor, arama onu
    # bulamayinca kutu iseyaramaz gorunuyordu (sutun huni menusu vardi ama once
    # dogru sutunu bulmak gerekiyordu).
    _SEARCH_COLUMNS = ("tracking_no", "reference", "consignee_name",
                       "store_code", "invoice_number")

    _LIKE_FILTERS = {
        "tracking_no": "tracking_no",
        "reference": "reference",
        "store_code": "store_code",
        "invoice_number": "invoice_number",
        "emc_invoices": "emc_invoices",
        "consignee_name": "consignee_name",
        "sales_rep": "sales_rep",
        "shipment_method": "shipment_method",
        "country": "country",
        "zip_code": "zip_code",
        "shipment_date": "shipment_date",
        "delivered_date": "delivered_date",
    }

    @staticmethod
    def _in_clause(column: str, value) -> tuple[str, list]:
        """`status`/`channel` tek deger de liste de olabilir (ust filtre coklu secer).

        Bos string "secim yok" demektir, `season = ''` diye SORULMAZ: oyle olsaydi
        sezon secilmeden yapilan her arama yalnizca sezonu bos olan parcalari
        dondururdu.
        """
        values = [v for v in ([value] if isinstance(value, str) else (value or [])) if v]
        if not values:
            return "", []
        if len(values) == 1:
            return f" AND {column} = ?", values
        return f" AND {column} IN ({','.join('?' * len(values))})", values

    def season_counts(self) -> dict[str, int]:
        """Sezon suzgecinin secenekleri: {sezon kodu: parca sayisi}.

        Sezonu bos olan parcalar SECENEK OLMAZ: liste ERP'den gelen gercek
        sezonlari gostermeli, "bos" bir sezon degil eksik veridir.
        """
        rows = self.conn.execute(
            "SELECT season, COUNT(*) AS n FROM parcels "
            "WHERE season IS NOT NULL AND season <> '' GROUP BY season"
        ).fetchall()
        return {r["season"]: r["n"] for r in rows}

    def shipment_method_counts(self) -> dict[str, int]:
        """Gonderim sekli suzgecinin secenekleri: {gonderim sekli: parca sayisi}."""
        rows = self.conn.execute(
            "SELECT shipment_method, COUNT(*) AS n FROM parcels "
            "WHERE shipment_method IS NOT NULL AND shipment_method <> '' GROUP BY shipment_method"
        ).fetchall()
        return {r["shipment_method"]: r["n"] for r in rows}

    def _list_where(
        self,
        status=None,
        channel=None,
        season=None,
        shipment_method=None,
        search: str = "",
        delivered_day: str = "",
        **filters: str,
    ) -> tuple[str, list]:
        """Listenin TEK suzgeci — sayfa da sayac da buradan gecer.

        Iki ayri yerde kurulsaydi "986 kayit" derken sayfalarda 900 satir
        cikardi (bkz. `_archive_where`, ayni gerekce).

        NOT: 2026-09-02'de burada "IE kanalinda YALNIZCA teslim edilenler
        gorunsun" diye bir suzgec vardi; 2026-09-03'te KULLANICI KALDIRTTI
        ("bunlar gizli kalmasin"). Artik butun IE kolileri listede.
        """
        sql = " WHERE 1=1"
        args: list = []
        for column, value in (("status", status), ("channel", channel),
                              ("season", season), ("shipment_method", shipment_method)):
            clause, values = self._in_clause(column, value)
            sql += clause
            args.extend(values)
        if delivered_day:
            sql += f" AND status = 'delivered' AND {_DELIVERY_DAY} = ?"
            args.append(delivered_day)
        if search:
            sql += (" AND (" + " OR ".join(f"{c} LIKE ?" for c in self._SEARCH_COLUMNS)
                    + ")")
            args.extend([f"%{search}%"] * len(self._SEARCH_COLUMNS))
        for key, value in filters.items():
            if not value:
                continue
            column = self._LIKE_FILTERS.get(key)
            if not column:
                raise TypeError(f"Bilinmeyen filtre alani: {key}")
            sql += f" AND {column} LIKE ?"
            args.append(f"%{value}%")
        return sql, args

    # Huni menusu sayaclarinin sayilabilecegi sutunlar. BEYAZ LISTE: sutun adi
    # sorguya metin olarak giriyor, disaridan gelen bir ad enjeksiyon olurdu.
    _FACET_COLUMNS = {"status", "channel", "season", "shipment_method"}

    def facet_counts(self, column: str, **criteria) -> dict[str, int]:
        """`column` degerlerine gore adet — verilen suzgecler UYGULANMIS halde.

        Huni menuleri icin: bir suzgecin secenekleri sayilirken DIGER suzgecler
        uygulanir, kendi suzgeci uygulanmaz (cagiran onu `criteria`den cikarir).
        Boylece "kanal=FedEx" secikken statu menusu FedEx'in statulerini gosterir.
        Onceden sayaclar TUM tablodan geliyordu ve kullanici 289 FedEx kolisi
        suzerken statu menusunde "delivered 1000+" goruyordu (bildirildi
        2026-09-03).
        """
        if column not in self._FACET_COLUMNS:
            raise ValueError(f"Sayilamayan sutun: {column}")
        where, args = self._list_where(**criteria)
        rows = self.conn.execute(
            f"SELECT {column} AS k, COUNT(*) AS n FROM parcels{where} GROUP BY {column}",
            args).fetchall()
        return {r["k"]: r["n"] for r in rows if r["k"]}

    def count_parcels(self, **criteria) -> int:
        """Suzgece uyan TOPLAM satir — sayfa oklari icin.

        Sayfadaki satir sayisi yetmez: son sayfada mi olduğumuzu ancak toplam
        soyler, yoksa "ileri" oku hep tiklanabilir kalir.
        """
        where, args = self._list_where(**criteria)
        return self.conn.execute(
            "SELECT COUNT(*) FROM parcels" + where, args).fetchone()[0]

    def list_parcels(
        self,
        status=None,
        channel=None,
        season=None,
        shipment_method=None,
        search: str = "",
        limit: int = 500,
        delivered_day: str = "",
        offset: int = 0,
        **filters: str,
    ) -> list[dict]:
        """Her basligi ayri filtrelenebilir tablo icin (bkz. web/templates/tracking.html)."""
        where, args = self._list_where(
            status=status, channel=channel, season=season, shipment_method=shipment_method,
            search=search, delivered_day=delivered_day, **filters)
        sql = ("SELECT * FROM parcels" + where
               + " ORDER BY updated_at DESC, tracking_no DESC LIMIT ? OFFSET ?")
        return [dict(r) for r in
                self.conn.execute(sql, args + [limit, offset]).fetchall()]

    # Arsiv sayfasinin "belgesi var" tanimi. Yol kolonu NULL da olabilir bos
    # metin de (eski kayitlar) — ikisi de "yok" demektir.
    _HAS_POD = "(pod_path IS NOT NULL AND pod_path <> '')"
    _HAS_LABEL = "(label_pdf_path IS NOT NULL AND label_pdf_path <> '')"

    # Arsiv klasorlerinin karsiligi olan kolonlar. Klasor adi ISTEKTEN gelir,
    # kolon adi BURADAN — sorguya disaridan metin girmesin diye beyaz liste.
    ARCHIVE_LEVELS = {"season": "source_sheet", "country": "country",
                      "store": "store_code",
                      # Etiket dalinin klasoru = `dispatch/engine.py:label_dir`
                      # gunudur (ikisi de ayni `day` degerinden yaziliyor), yani
                      # paneldeki klasor diskteki klasorle birebir ortusur.
                      # Eski kayitlarda saat de olabildigi icin ilk 10 karakter.
                      "day": "substr(IFNULL(shipment_date, ''), 1, 10)"}

    def _archive_where(self, kind: str = "", search: str = "", sheets=None,
                       country=None, store=None, day=None) -> tuple[str, list]:
        """Arsivin TEK suzgeci. Klasor sayimi ile dosya listesi ayni yerden
        gecmezse kart "12 belge" derken icerde 9 satir cikar."""
        sql = " WHERE " + {"pod": self._HAS_POD, "label": self._HAS_LABEL}.get(
            kind, f"({self._HAS_POD} OR {self._HAS_LABEL})")
        args: list = []
        if search:
            # `list_parcels`taki desenin ayni; buradaki alanlar kullanicinin
            # elinde olan tanimlayicilar: takip no, magaza, fatura, referans.
            sql += (" AND (tracking_no LIKE ? OR store_code LIKE ? "
                    "OR invoice_number LIKE ? OR reference LIKE ?)")
            args += [f"%{search}%"] * 4
        # Sezon `parcels`ta kolon degil; kaynak adindan turer (config.
        # season_from_source). Cagiran o sezona karsilik gelen kaynaklari verir.
        if sheets is not None:
            sql += f" AND source_sheet IN ({','.join('?' * len(sheets))})" if sheets \
                else " AND 0"
            args += list(sheets)
        # `None` = suzme yok; `""` = ADI BOS klasor (magaza kodu girilmemis
        # parcalar gercekten var, onlar da bir klasorde toplanmali). Kolon adi
        # `ARCHIVE_LEVELS`ten gelir — sayim ile suzme ayni ifadeyi kullansin.
        for level, value in (("country", country), ("store", store), ("day", day)):
            if value is not None:
                sql += f" AND IFNULL({self.ARCHIVE_LEVELS[level]}, '') = ?"
                args.append(value)
        return sql, args

    def list_archive(self, search: str = "", kind: str = "", limit: int = 300,
                     sheets=None, country=None, store=None, day=None) -> list[dict]:
        """Diskte belgesi olan parcalar. `kind`: '' (ikisi) | 'pod' | 'label'.

        Yeni tablo YOK: POD ve etiket yollari zaten `parcels`ta duruyor
        (`pod_path`, `label_pdf_path`). Bu sorgu yalnizca "dosyasi olanlari"
        suzer — arsivleme mekanizmasina hic dokunmaz.
        """
        where, args = self._archive_where(kind, search, sheets, country, store, day)
        sql = "SELECT * FROM parcels" + where + " ORDER BY updated_at DESC LIMIT ?"
        return [dict(r) for r in self.conn.execute(sql, args + [limit]).fetchall()]

    def archive_folders(self, level: str, **filters) -> list[dict]:
        """Bir klasor seviyesinin alt klasorleri: [{"name": "FR", "n": 150}, ...].

        Sayfa once klasorleri gosterip icine girildikce daraliyor; her seviyede
        300 satir cekip Python'da saymak hem bosuna hem yaniltici olurdu
        (LIMIT'e takilan sayim eksik cikar).
        """
        column = self.ARCHIVE_LEVELS[level]
        where, args = self._archive_where(**filters)
        sql = (f"SELECT IFNULL({column}, '') AS name, COUNT(*) AS n FROM parcels"
               f"{where} GROUP BY 1 ORDER BY 1")
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def mark_checked(self, tracking_no: str) -> None:
        """Parcaya 'bakildi' der — statu degismese, hatta sorgu basarisiz olsa bile.

        Takip taramasi artik turda sabit sayida parca sorguluyor ve siraya
        `last_checked_at`'e gore giriyor. Cevap vermeyen ya da hata veren parca
        isaretlenmezse sonsuza dek sirani basinda kalir ve digerlerini ac birakir.
        """
        self.conn.execute(
            "UPDATE parcels SET last_checked_at = ? WHERE tracking_no = ?",
            (_now(), tracking_no),
        )
        self.conn.commit()

    def active_parcels(self, limit: int = 0) -> list[dict]:
        """Sonuclanmamis parcalar — takip taramasi icin.

        'delivered' disinda 'cancelled' de dislanir. `cancelled` YALNIZCA bizim
        panelden `Label/Delete` gonderdigimiz etiketlere yazilir — o etiket
        gercekten olmustur. GLS'in "parcel data have been deleted" olayi buraya
        DAHIL DEGILDIR: o bir on-duyuru temizligidir, numara sonradan yeniden
        canlanabiliyor (bkz. tracking/scheduler.py'deki canli olcum).

        En uzun suredir bakilmayanlar once gelir; `limit` verilirse tur basina
        sorgulanacak parca sayisi sinirlanir (GLS hiz siniri, bkz. scheduler).
        """
        sql = ("SELECT * FROM parcels WHERE status NOT IN ('delivered', 'cancelled', 'returned') "
               "ORDER BY last_checked_at IS NULL DESC, last_checked_at ASC")
        args: list = []
        if limit > 0:
            sql += " LIMIT ?"
            args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ParcelShop'a birakilan koli TESLIM EDILMISTIR ama HENUZ SONUCLANMAMISTIR:
    # musteri gelip almazsa GLS saklama suresi dolunca onu gondericiye geri
    # yollar. `active_parcels` 'delivered' olani bir daha SORMADIGI icin bu
    # donusum panele hic yansimiyordu (kullanici bildirdi, 2026-08-31:
    # "otees iadeye donmus ... apiden gelen cevaplari duzgun yansitmiyorsun").
    #
    # "Alindi" isareti GLS'in AYRI bir olayidir ("Handing over the parcel to
    # the recipient at the GLS ParcelShop.") — o gelmisse koli musterinin
    # elindedir ve artik sorulmasi gerekmez.
    PARCEL_SHOP_COLLECTED_MARKER = "handing over the parcel to the recipient"

    # Izleme penceresi. GLS'in ParcelShop saklama suresi ~10 IS GUNUDUR; bundan
    # cok sonra koli mutlaka sonuclanmistir (ya alindi ya iadeye dondu). Sinir
    # olmazsa "alindi" olayi hic yazilmayan koliler kuyrukta SONSUZA KADAR
    # kalir ve her tur bosuna sorgulanirdi.
    PARCEL_SHOP_WATCH_DAYS = 30

    def parcel_shop_pending(self, limit: int = 0) -> list[dict]:
        """ParcelShop'a teslim edilmis ama HENUZ ALINMAMIS koliler.

        Takip taramasi bunlari `active_parcels`e EK olarak sorgular: koli
        alinana (ya da saklama suresi dolup iadeye donene) kadar akibeti
        degisebilir. Pencere `PARCEL_SHOP_WATCH_DAYS` ile sinirlidir.
        """
        sql = """
            SELECT p.* FROM parcels p
            WHERE p.status = 'delivered'
              AND EXISTS (SELECT 1 FROM status_events e
                           WHERE e.parcel_id = p.id
                             AND lower(e.note) LIKE '%parcelshop%')
              AND NOT EXISTS (SELECT 1 FROM status_events e2
                               WHERE e2.parcel_id = p.id
                                 AND lower(e2.note) LIKE ?)
              AND julianday('now') - julianday(
                    COALESCE(NULLIF(replace(p.delivered_date, 'Z', ''), ''), p.updated_at)
                  ) <= ?
            ORDER BY p.last_checked_at IS NULL DESC, p.last_checked_at ASC
        """
        args: list = [f"%{self.PARCEL_SHOP_COLLECTED_MARKER}%", self.PARCEL_SHOP_WATCH_DAYS]
        if limit > 0:
            sql += " LIMIT ?"
            args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def parcels_without_pod(self, limit: int = 0) -> list[dict]:
        """Teslim edilmis ama POD'u arsivlenmemis parcalar — takip taramasi icin.

        POD'u kullanicinin indirme tiklamasina birakmak calismadi: 173 teslimatin
        yalnizca 2'sinde arsiv olustu (2026-08 olcumu). Tarayici bu kuyrugu her
        turda biraz eritir.

        Hic sorulmamislar once gelir; sorulup POD'u cikmayanlar (GLS 204) en sona
        duser ve zamanla yeniden denenir — POD teslimattan saatler sonra
        olusabiliyor.

        ESKIYEN PARCA HER TURDA SORULMAZ (bkz. POD_FRESH_DAYS / POD_RETRY_HOURS).
        Canli olcum 2026-08-17: 378 teslimatin 16'sinin POD'u gunler sonra hala
        yoktu ve GLS her seferinde 204 donuyordu. Kuyruk onlari sonsuza dek her
        15 dakikada bir yeniden soruyordu — tek turda ~40 saniye, gunde ~2200
        bosa istek, ustelik hiz sinirli (429) bir uc. Vazgecilmiyor, yalnizca
        seyreltiliyor: POD gunler sonra da gelebiliyor.
        """
        sql = ("SELECT * FROM parcels WHERE status = 'delivered' "
               "AND (pod_path IS NULL OR pod_path = '') "
               # Uc kosuldan biri yeterli: hic sorulmamis / teslimat taze VE
               # son denemenin uzerinden >= POD_FRESH_RETRY_HOURS gecmis /
               # eskiyen ama son denemenin uzerinden >= POD_RETRY_HOURS gecmis.
               # Taze parca da bir gap'e tabi: yoksa ~40 taze teslimat her tur
               # (15 dk) sorulup turu ~2,5 dk tikatiyordu (2026-09-03).
               "AND (pod_attempted_at IS NULL "
               f"     OR ({_DELIVERY_DAY} >= date('now', ?) "
               "         AND datetime(pod_attempted_at) <= datetime('now', ?)) "
               # `datetime(...)` SART: damgayi biz `T` ayracli yaziyoruz
               # (`_now`), SQLite ise bosluklu uretiyor. Duz metin
               # karsilastirmasinda 'T' > ' ' oldugu icin ayni gun sorulmus
               # her parca "cok eski" gorunurdu.
               "     OR datetime(pod_attempted_at) <= datetime('now', ?)) "
               "ORDER BY pod_attempted_at IS NULL DESC, pod_attempted_at ASC")
        args: list = [f"-{POD_FRESH_DAYS} days", f"-{POD_FRESH_RETRY_HOURS} hours",
                      f"-{POD_RETRY_HOURS} hours"]
        if limit > 0:
            sql += " LIMIT ?"
            args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def mark_pod_attempted(self, tracking_no: str) -> None:
        """POD icin GLS'e soruldu — cevap gelmese de. Kuyruk sirasi bunun uzerinden doner."""
        self.conn.execute(
            "UPDATE parcels SET pod_attempted_at = ? WHERE tracking_no = ?",
            (_now(), tracking_no),
        )
        self.conn.commit()

    def pod_missing_since(self, wait_days: int = 7, unchased_only: bool = True,
                          limit: int = 200) -> list[dict]:
        """Teslim edileli `wait_days` gun gecmis ama POD'u hala gelmemis parcalar.

        Bekleme SART: POD teslimattan saatler, bazen bir gun sonra olusuyor
        (`parcels_without_pod` kuyrugu bunu surekli yeniden deniyor). Hemen
        sorulursa GLS'e henuz var olmayan bir belge sorulmus olur.

        Olcum tarihi icin bkz. `_DELIVERY_DAY`.
        """
        # FedEx DISARIDA: bu liste GLS musteri hizmetlerine "POD'u gonderin"
        # maili yaziyor (bkz. notify/pod_chase.py). FedEx'te boyle bir belge de
        # boyle bir muhatap da yok — GLS'e ait olmayan bir koli icin GLS'e mail
        # gitmis olurdu.
        sql = ("SELECT * FROM parcels WHERE status = 'delivered' "
               "AND (pod_path IS NULL OR pod_path = '') "
               "AND COALESCE(issue_text, '') = '' "
               "AND COALESCE(channel, '') <> 'FEDEX' "
               f"AND {_DELIVERY_DAY} <= date('now', ?)")
        args: list = [f"-{int(wait_days)} days"]
        if unchased_only:
            sql += " AND COALESCE(pod_chased_at, '') = ''"
        sql += " ORDER BY delivered_date ASC"
        if limit > 0:
            sql += " LIMIT ?"
            args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def mark_pod_chased(self, tracking_nos: list[str]) -> None:
        """GLS'e mail yazildi olarak isaretler — cevap gelmese de tekrar sorulmaz."""
        now = _now()
        self.conn.executemany(
            "UPDATE parcels SET pod_chased_at = ? WHERE tracking_no = ?",
            [(now, no) for no in tracking_nos],
        )
        self.conn.commit()

    def mark_erp_pushed(self, marks: list[tuple[str, str]]) -> None:
        """ERP'ye yazilan degerin parmak izini saklar. marks: (takip_no, parmak_izi)."""
        self.conn.executemany(
            "UPDATE parcels SET erp_pushed = ? WHERE tracking_no = ?",
            [(fingerprint, no) for no, fingerprint in marks],
        )
        self.conn.commit()

    def purge_parcels(self) -> int:
        """Tum parcalari ve olay gecmisini siler; silinen parca sayisini doner.

        Panel MSSQL'den sifirdan doldurulurken kullanilir (erp/sync.py --reset).
        Arsivdeki POD dosyalarina dokunulmaz.
        """
        with self.conn:
            count = self.conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
            self.conn.execute("DELETE FROM status_events")
            self.conn.execute("DELETE FROM parcels")
        return count

    def counts_by_status(self, exclude_channels: tuple[str, ...] = ()) -> dict[str, int]:
        """`exclude_channels`: dashboard'un ust statu seridi icin — kullanici
        istegi (2026-08-31): FedEx (Kanada/ara sira Irlanda, olcek COK farkli)
        GLS'in "ilk 8 kutucuk" ozetine KARISMASIN, panel FedEx hacmiyle
        yanlislikla buyumus/kuculmus gorunmesin. Varsayilan bos: diger
        cagiranlar (rapor, API vb.) TUM kanallari gormeye devam eder.
        """
        placeholders = ", ".join("?" for _ in exclude_channels)
        where = f"WHERE channel NOT IN ({placeholders})" if exclude_channels else ""
        rows = self.conn.execute(
            f"SELECT status, COUNT(*) as n FROM parcels {where} GROUP BY status",
            tuple(exclude_channels),
        ).fetchall()
        out = {s: 0 for s in STATUS_CHOICES}
        for r in rows:
            out[r["status"]] = r["n"]
        return out

    def delivered_on(self, day: str) -> int:
        """`day` (YYYY-MM-DD) gunu teslim edilen parca sayisi.

        Gun kovasi `_DELIVERY_DAY` ile ISTANBUL gunudur, `day` de cagirandan
        yerel saatle gelmelidir (`timez.now().date()`). SQL'e `date('now')`
        gomulmez — o konteynerin UTC gununu verir ve 00:00-03:00 arasindaki
        teslimatlar bir onceki gune sayilirdi.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM parcels WHERE status = 'delivered' "
            f"AND {_DELIVERY_DAY} = ?",
            (day,),
        ).fetchone()
        return row["n"]

    def exception_ever_tracking_nos(self, channel: str = "") -> set[str]:
        """GECMISTE (su an degil) hic 'exception' statusune girmis takip
        numaralari — kalici bir "problemli oldu mu" izi. `status_events`teki
        her satir bir anlik durumdur; koli sonradan duzelse (delivered olsa)
        bile bu kayit SILINMEZ, bu yuzden EXISTS sorgusu guvenilir.

        `erp/writeback.py` (UD_Problemli) ve `tracking/delivery_report.py`
        (lokal gecikme isareti) AYNI tanimi kullanir — iki yerde ayri ayri
        yazilmasin diye burada. `channel` verilirse (NL/IE/FEDEX) yalnizca o
        tasima firmasi; bos ise tum kanallar.
        """
        sql = ("SELECT DISTINCT p.tracking_no FROM status_events se "
               "JOIN parcels p ON p.id = se.parcel_id WHERE se.status = 'exception'")
        args: list = []
        if channel:
            sql += " AND p.channel = ?"
            args.append(channel)
        return {r["tracking_no"] for r in self.conn.execute(sql, args)}

    # GLS bir koliyi dogrudan alicinin adresine degil kendi ParcelShop'una
    # (teslim noktasi) biraktiginda KENDI modelinde bunu "teslim edildi" sayar
    # — panelde de status='delivered' olur. Ama musteri koliyi almazsa GLS bir
    # sure sonra onu GONDERENE geri döndürür: "delivered" bir koli sessizce
    # "returned"a doner ve normal iade sayisini sisirir (kullanici bulgusu,
    # 2026-08-31: "parcel shopa teslim edilen ancak musterinin almadigi
    # gonderi de iadeye donebiliyor, returned durumu da yanlis oluyor").
    PARCEL_SHOP_MARKERS = ("parcelshop", "parcel shop")
    PARCEL_SHOP_TIMEOUT_MARKER = "maximum storage time"

    def parcel_shop_parcels(self, channel: str = "") -> list[dict]:
        """ParcelShop'a UGRAMIS her koli — statusu ne olursa olsun (delivered,
        exception, returned...), gecmisinde bir ParcelShop olayi varsa doner.

        Ayri raporlanabilsin diye (kullanici istegi): mevcut `returned` sayisi
        "musteriye hic ulasilamadi" ile "ParcelShop'ta zaman asimina ugradi"yi
        ayirt etmiyordu, ikisi ayni kovaya dusuyordu.
        """
        like_clauses = " OR ".join("lower(e.note) LIKE ?" for _ in self.PARCEL_SHOP_MARKERS)
        params = [f"%{m}%" for m in self.PARCEL_SHOP_MARKERS]
        channel_clause = "p.channel = ?" if channel else "1=1"
        rows = self.conn.execute(
            f"""
            SELECT p.tracking_no, p.status, p.store_code, p.invoice_number,
                   p.consignee_name, p.country, p.channel, p.delivered_date,
                   MIN(e.at) AS first_at,
                   GROUP_CONCAT(e.note, ' || ') AS notes
            FROM parcels p
            JOIN status_events e ON e.parcel_id = p.id
            WHERE {channel_clause} AND ({like_clauses})
            GROUP BY p.id
            ORDER BY first_at DESC
            """,
            ((channel, *params) if channel else tuple(params)),
        ).fetchall()
        result = []
        for r in rows:
            notes_low = (r["notes"] or "").lower()
            status = r["status"]
            result.append({
                "tracking_no": r["tracking_no"],
                "status": status,
                "store_code": r["store_code"],
                "invoice_number": r["invoice_number"],
                "consignee_name": r["consignee_name"],
                "country": r["country"],
                "channel": r["channel"],
                "delivered_date": r["delivered_date"],
                "first_at": r["first_at"],
                "timed_out": self.PARCEL_SHOP_TIMEOUT_MARKER in notes_low,
                "returned": status == "returned",
                # "at_risk": su an alici elinde DEGIL ama iade de olmadi — hala
                # ParcelShop'ta bekliyor olabilir, GLS zaman asimina yaklasiyor
                # olabilir. delivered/returned/cancelled disindaki HER statu.
                "at_risk": status not in ("delivered", "returned", "cancelled"),
            })
        return result

    def recent_exceptions(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM parcels WHERE status IN ('exception', 'returned') "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]

    # ---------- tarayici sagligi ----------

    def health(self) -> dict:
        """Tarayicinin son durumu. Hic tur donmediyse alanlar None."""
        row = self.conn.execute("SELECT * FROM tracker_health WHERE id = 1").fetchone()
        return dict(row) if row else {"last_run_at": None, "last_success_at": None,
                                      "last_error": None, "alerted_at": None}

    def record_tick(self, ok: bool, error: str = "") -> None:
        """Bir tarama turunun sonucunu yazar.

        `last_success_at` YALNIZCA basarili turda ilerler; basarisiz tur
        `last_run_at`i tazeler ama saatin islemesini durdurmaz — "en son ne
        zaman gercekten calisti" sorusunun cevabi budur.

        Basarili tur `alerted_at`i temizler: sistem duzeldiginde bir sonraki
        arizada uyari yeniden gidebilmeli.
        """
        now = _now()
        self.conn.execute(
            "INSERT INTO tracker_health (id, last_run_at, last_success_at, last_error, alerted_at) "
            "VALUES (1, ?, ?, ?, NULL) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  last_run_at = excluded.last_run_at, "
            "  last_success_at = COALESCE(excluded.last_success_at, tracker_health.last_success_at), "
            "  last_error = excluded.last_error, "
            "  alerted_at = CASE WHEN excluded.last_success_at IS NULL "
            "                    THEN tracker_health.alerted_at ELSE NULL END",
            (now, now if ok else None, (error or "").strip() or None),
        )
        self.conn.commit()

    def mark_alerted(self) -> None:
        """Uyari gonderildi — ayni ariza icin her turda mail atilmasin."""
        self.conn.execute(
            "UPDATE tracker_health SET alerted_at = ? WHERE id = 1", (_now(),))
        self.conn.commit()

    def record_tick_error(self, error: str) -> None:
        """Bir turdaki hatayi (grup basarisizligi vb.) KALICI gecmise ekler.

        `record_tick`'in tek satirlik `last_error`sinden farkli: o bir sonraki
        basarili turda silinir, bu INSERT edilir — turlar arasi hata izi
        kaybolmaz. Her yazimda 7 gunden eski kayitlar temizlenir (kendi
        kendini sinirlar, `auth/db.py:login_events` ile ayni desen).
        """
        error = (error or "").strip()
        if not error:
            return
        self.conn.execute(
            "INSERT INTO tracker_tick_errors (at, error) VALUES (?, ?)",
            (_now(), error),
        )
        cutoff = (datetime.utcnow() - timedelta(days=TICK_ERROR_RETENTION_DAYS)) \
            .isoformat(timespec="seconds")
        self.conn.execute("DELETE FROM tracker_tick_errors WHERE at < ?", (cutoff,))
        self.conn.commit()

    def recent_tick_errors(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT at, error FROM tracker_tick_errors ORDER BY at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- analitik ----------

    def channel_breakdown(self) -> dict[str, dict[str, int]]:
        """{'NL': {'delivered': 12, 'in_transit': 3, ...}, 'IE': {...}, 'FEDEX': {...}}

        FEDEX ONCEDEN burada YOKTU — bilinmeyen kanal "NL"ye sessizce
        karisiyordu (kullanicinin ilk raporladigi hata, 2026-08-31): FedEx
        kolileri NL sayimini sisiriyor, kendi kutusunda hic gorunmuyordu.
        """
        rows = self.conn.execute(
            "SELECT channel, status, COUNT(*) as n FROM parcels GROUP BY channel, status"
        ).fetchall()
        out: dict[str, dict[str, int]] = {"NL": {s: 0 for s in STATUS_CHOICES},
                                          "IE": {s: 0 for s in STATUS_CHOICES},
                                          "FEDEX": {s: 0 for s in STATUS_CHOICES}}
        for r in rows:
            ch = r["channel"] if r["channel"] in out else "NL"
            out[ch][r["status"]] = r["n"]
        return out

    def top_countries(self, limit: int = 6) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT COALESCE(NULLIF(country, ''), 'Bilinmiyor') as country,
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) as delivered,
                   SUM(CASE WHEN status = 'exception' THEN 1 ELSE 0 END) as exception
            FROM parcels
            GROUP BY country
            ORDER BY total DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


    def weekly_series(self, weeks: int = 8) -> list[dict]:
        """Son N haftaya ait teslimat/olusturulma grafigi verisi."""
        rows = self.conn.execute(
            """
            SELECT strftime('%Y-%W', COALESCE(delivered_date, updated_at)) as week,
                   COUNT(*) as delivered
            FROM parcels
            WHERE status = 'delivered'
            GROUP BY week
            ORDER BY week DESC
            LIMIT ?
            """,
            (weeks,),
        ).fetchall()
        return list(reversed([dict(r) for r in rows]))


    def week_summary(self) -> dict:
        """Son 7 gunun SEVKIYAT ozeti.

        Olcut `shipment_date` (MSSQL'de `UD_PaketlemeTarihi`, toplu etikette
        sevk gunu) — `created_at` DEGIL. `created_at` parcanin bizim
        veritabanimiza girdigi andir: MSSQL'den toplu senkron bir gunde binlerce
        satiri ayni damgayla yaziyor, o gunden sonra kart kalici olarak 0
        gosteriyordu (olcum: 4347 parca hepsi 2026-07-28 damgali, "bu hafta" 0).
        `shipment_date` bos olan eski kayitlar icin `created_at`e dusulur —
        `parcels_to_chase` ile ayni desen.
        """
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) as delivered,
                SUM(CASE WHEN status='exception' THEN 1 ELSE 0 END) as exception,
                COALESCE(SUM(freight_cost), 0) as freight_cost,
                COALESCE(SUM(weight_kg), 0) as weight_kg
            FROM parcels
            WHERE COALESCE(NULLIF(shipment_date, ''), created_at) >= date('now', '-7 days')
            """
        ).fetchone()
        return dict(row) if row else {"total": 0, "delivered": 0, "exception": 0, "freight_cost": 0, "weight_kg": 0}

    def delete(self, tracking_no: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM parcels WHERE tracking_no = ?", (tracking_no,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- events ----------

    def _add_event(self, parcel_id: int, status: str, note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO status_events (parcel_id, status, note, at) VALUES (?, ?, ?, ?)",
            (parcel_id, status, note, _now()),
        )

    def sync_events(self, tracking_no: str, events: list[dict]) -> int:
        """GLS API'sinden gelen tum olay gecmisini status_events tablosuna yazar.
        
        Mukerrer kayitlar (ayni zaman damgasi ve aciklama) filtrelenir.
        """
        if not events:
            return 0
        parcel = self.conn.execute("SELECT id FROM parcels WHERE tracking_no = ?", (tracking_no,)).fetchone()
        if not parcel:
            return 0
        parcel_id = parcel["id"]
        inserted = 0
        for evt in events:
            at = evt.get("date") or evt.get("at") or evt.get("created")
            if not at:
                continue

            desc = (evt.get("descriptionEN") or evt.get("descriptionNL") or evt.get("description") or evt.get("note") or "").strip()
            depot = (evt.get("depotName") or evt.get("depot") or "").strip()
            details = (evt.get("details") or "").strip()

            note_parts = [desc]
            if details:
                note_parts.append(f"({details})")
            if depot and depot != "-":
                note_parts.append(f"— {depot}")
            note = " ".join(filter(None, note_parts)).strip()

            st = classify_event_text(desc, evt.get("descriptionNL") or "")

            at_clean = str(at).replace("Z", "").split(".")[0]
            existing = self.conn.execute(
                "SELECT id, status FROM status_events WHERE parcel_id = ? AND (at = ? OR at LIKE ? OR substr(at, 1, 19) = ?) AND (note = ? OR note LIKE ?)",
                (parcel_id, at, f"{at_clean}%", at_clean, note, f"{desc}%")
            ).fetchone()

            if not existing:
                self.conn.execute(
                    "INSERT INTO status_events (parcel_id, status, note, at) VALUES (?, ?, ?, ?)",
                    (parcel_id, st, note, at),
                )
                inserted += 1
            elif existing["status"] != st:
                self.conn.execute(
                    "UPDATE status_events SET status = ?, note = ? WHERE id = ?",
                    (st, note, existing["id"]),
                )
                inserted += 1

        if inserted > 0:
            self.conn.commit()
        return inserted

    def events_for(self, tracking_no: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT e.status, e.note, e.at FROM status_events e "
            "JOIN parcels p ON p.id = e.parcel_id "
            "WHERE p.tracking_no = ? ORDER BY e.at DESC",
            (tracking_no,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delivered_since(self, since: str, limit: int = 200) -> list[dict]:
        """`since` anindan sonra 'delivered' olayi yazilmis parcalar (yeni once).

        Gunluk ozet icin. `parcels.delivered_date` yerine `status_events`
        kullaniliyor cunku ozet "bugun ne oldu"yu anlatmali; teslim tarihi
        GLS'in bildirdigi tarihtir, bizim ogrendigimiz an degil.

        GROUP BY sart: ayni parca icin birden fazla 'delivered' olayi yazilmis
        olabilir.
        """
        rows = self.conn.execute(
            "SELECT p.*, MAX(e.at) AS event_at FROM status_events e "
            "JOIN parcels p ON p.id = e.parcel_id "
            "WHERE e.status = 'delivered' AND e.at >= ? "
            "GROUP BY p.id ORDER BY event_at DESC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- sorunlu kargolar ----------

    # `problem_parcels` icindeki CASE ile ayni sira — sayaclarin ve sekmelerin
    # duzeni buradan turer.
    PROBLEM_KINDS = (
        "exception", "damaged", "partial", "pod_missing", "stale", "not_handed_over",
        "returned",
    )

    # exception/damaged bir kargo son olaydan bu kadar gun sonra "Sorunlu
    # Kargolar"dan otomatik duser (kullanici 2026-09-04). Canli takip
    # listesinde status'uyle gorunmeye devam eder.
    EXCEPTION_MAX_DAYS = 21

    def problem_parcels(self, stale_days: int = 7, limit: int = 500,
                        exception_max_days: int | None = None) -> list[dict]:
        """Elle mudahale gerektiren kargolar; her satirda `problem_kind` alani var.

        - exception       : GLS teslimati durduran bir sorun bildirdi
                            (bkz. scheduler.BLOCKING_PHRASES)
        - damaged         : GLS hasar bildirdi ama koli yola devam ediyor
                            (bkz. scheduler.DAMAGE_PHRASES) — icindeki giysi
                            zarar gormus olabilir, teslimat beklenmeden bakilmali
        - partial         : ayni faturanin bir kismi teslim, bu koli degil
        - pod_missing     : teslim edildi ama teslimat kaniti indirilmemis
        - stale           : GLS koliyi N gun once teslim aldi, hala teslim yok
        - not_handed_over : etiket basilmis, GLS koliyi HALA teslim almamis

        Ayrim `handed_over_at` uzerinden yapilir, statuden degil: GLS koli yola
        ciktiktan sonra bile `state` alanini "Announced" birakabiliyor, ayrica
        teslim almadan once de olay yaziyor (preadvice, e-posta bildirimi) —
        bunlar hareket sayilmaz ve kullanicinin dogruladigi gibi SORUN DEGIL.

        `stale` son olaya degil TESLIM ALMA anina bakar: takilan bir koliye GLS
        gurultu olayi yazmaya devam ediyor ("Action list item revised/normal"),
        son olay tarihi taze gorunurken koli 14 gundur yolda kalabiliyor.

        `partial` "5 koliden 3'u teslim, 1'i degil" durumudur: gruplama
        (magaza kodu, fatura no) ciftidir, cunku ayni fatura birden cok koliye
        bolunur (olculdu: bir fatura 34 koli).
        """
        exc_days = self.EXCEPTION_MAX_DAYS if exception_max_days is None else exception_max_days
        rows = self.conn.execute(
            """
            WITH grp AS (
                SELECT store_code, invoice_number,
                       SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered_n
                FROM parcels
                WHERE COALESCE(store_code, '') <> ''
                  AND COALESCE(invoice_number, '') <> ''
                GROUP BY store_code, invoice_number
            )
            SELECT p.*, CASE
                WHEN p.status = 'returned' THEN 'returned'
                WHEN p.status = 'exception' THEN 'exception'
                WHEN p.status NOT IN ('delivered', 'cancelled', 'returned') AND COALESCE(p.issue_text, '') <> '' THEN 'damaged'
                WHEN p.status = 'delivered' AND COALESCE(p.issue_text, '') = ''
                     AND COALESCE(p.channel, '') NOT IN ('FEDEX', 'IE')
                     AND """ + _DELIVERY_DAY + """ <= date('now', ?) THEN 'pod_missing'
                WHEN COALESCE(g.delivered_n, 0) > 0 THEN 'partial'
                WHEN COALESCE(p.handed_over_at, '') = '' THEN 'not_handed_over'
                ELSE 'stale'
            END AS problem_kind
            FROM parcels p
            LEFT JOIN grp g
                   ON g.store_code = p.store_code
                  AND g.invoice_number = p.invoice_number
            WHERE p.status = 'returned'
               -- exception/damaged: SON OLAYIN uzerinden `exc_days` gunden
               -- fazla gectiyse listeden duser. GLS koliyi o sureden once
               -- ya iade eder (-> `returned`) ya musteri alir (-> `delivered`);
               -- ust uste kalan eskiler cogunlukla SINIR OTESI teslimatlar
               -- (FR/ES/BE): `api.gls.nl` son adimi hedef ulkeden geri
               -- yazmiyor, GLS'in kendi portali "delivered" gosteriyor ama
               -- bizim beslememiz "depoda" da kaliyor (kullanici 2026-09-04:
               -- "gls ekraninda delivered zaten bunlar"). Canli takip
               -- listesinde status=exception olarak yine gorunurler.
               OR (p.status = 'exception'
                   AND substr(COALESCE(p.last_event_at, p.updated_at), 1, 10) >= date('now', ?))
               OR (p.status NOT IN ('delivered', 'cancelled', 'returned')
                   AND COALESCE(p.issue_text, '') <> ''
                   AND substr(COALESCE(p.last_event_at, p.updated_at), 1, 10) >= date('now', ?))
               -- "POD eksik" YALNIZCA: teslim edileli >= stale_days GUN gecmis
               -- ama POD hala yok. Taze teslimatta POD saatler/bir gun sonra
               -- olusur (`parcels_without_pod` kuyrugu ceker) — o sirada
               -- "sorunlu" degildir (kullanici 2026-09-04: "teslim edilmisler").
               -- FedEx ve IE DISARIDA: ikisinde de su an cekilebilir bir POD
               -- yok (FedEx'te hic; IE'de ShipIT-Farm yetkisi bekliyor,
               -- bkz. gls_api/providers.py:pod_provider_for -> None). Hicbir
               -- zaman gelmeyecek bir kanit icin surekli uyari verilirdi.
               OR (p.status = 'delivered' AND (p.pod_path IS NULL OR p.pod_path = '')
                   AND COALESCE(p.issue_text, '') = ''
                   AND COALESCE(p.channel, '') NOT IN ('FEDEX', 'IE')
                   AND """ + _DELIVERY_DAY + """ <= date('now', ?))
               OR (p.status NOT IN ('delivered', 'exception', 'cancelled', 'returned')
                   AND COALESCE(g.delivered_n, 0) > 0)
               OR (p.status NOT IN ('delivered', 'exception', 'cancelled', 'returned')
                   AND COALESCE(p.handed_over_at, '') <> ''
                   -- GLS tarihleri 'YYYY-MM-DDTHH:MM:SSZ'; SQLite'in kendi
                   -- bicimiyle ('... ') metin karsilastirmasi ayni gun icinde
                   -- yanilir, bu yuzden yalnizca gun kismi karsilastirilir.
                   AND substr(p.handed_over_at, 1, 10) < date('now', ?))
               OR (p.status = 'created' AND COALESCE(p.handed_over_at, '') = ''
                   AND COALESCE(NULLIF(p.shipment_date, ''), p.created_at) < date('now', ?))
            ORDER BY
                CASE problem_kind
                    WHEN 'returned' THEN 0 WHEN 'exception' THEN 1 WHEN 'damaged' THEN 2
                    WHEN 'partial' THEN 3 WHEN 'pod_missing' THEN 4
                    WHEN 'stale' THEN 5 ELSE 6
                END,
                COALESCE(p.last_event_at, p.updated_at) ASC
            LIMIT ?
            """,
            # SQL metnindeki `?` sirasiyla:
            # (1) CASE pod_missing gun kapisi        -stale_days
            # (2) WHERE exception son-olay kapisi    -exc_days
            # (3) WHERE damaged son-olay kapisi      -exc_days
            # (4) WHERE pod_missing gun kapisi       -stale_days
            # (5) WHERE handed_over_at 'stale' kapisi -stale_days
            # (6) WHERE 'created' bekleme kapisi      -stale_days
            # (7) LIMIT
            (f"-{int(stale_days)} days",
             f"-{int(exc_days)} days", f"-{int(exc_days)} days",
             f"-{int(stale_days)} days", f"-{int(stale_days)} days",
             f"-{int(stale_days)} days", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def problem_counts(self, stale_days: int = 7) -> dict[str, int]:
        counts = {kind: 0 for kind in self.PROBLEM_KINDS}
        for row in self.problem_parcels(stale_days=stale_days, limit=10000):
            counts[row["problem_kind"]] += 1
        counts["total"] = sum(counts.values())
        return counts

    def mark_wa_delivered(self, tracking_no: str, at: str | None = None) -> None:
        """Koli icin WhatsApp teslimat bildiriminin gonderildigini kaydeder."""
        stamp = at or _now()
        self.conn.execute(
            "UPDATE parcels SET wa_delivered_at = ? WHERE tracking_no = ?",
            (stamp, tracking_no),
        )
        self.conn.commit()

    def mark_wa_problem(self, tracking_no: str, at: str | None = None) -> None:
        """Koli icin WhatsApp sorun bildiriminin gonderildigini kaydeder."""
        stamp = at or _now()
        self.conn.execute(
            "UPDATE parcels SET wa_problem_at = ? WHERE tracking_no = ?",
            (stamp, tracking_no),
        )
        self.conn.commit()

    def mark_wa_returned(self, tracking_no: str, at: str | None = None) -> None:
        """Koli icin WhatsApp iade bildiriminin gonderildigini kaydeder."""
        stamp = at or _now()
        self.conn.execute(
            "UPDATE parcels SET wa_returned_at = ? WHERE tracking_no = ?",
            (stamp, tracking_no),
        )
        self.conn.commit()

