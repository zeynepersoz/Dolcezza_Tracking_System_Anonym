# -*- coding: utf-8 -*-
"""
Ortam yapılandırması — tüm kimlik bilgileri .env dosyasından okunur, koda asla yazılmaz.

GLS_MODE=mock   -> yerel sahte sunucu (geliştirme/test, kimlik bilgisi gerekmez)
GLS_MODE=test   -> GLS test ortamı
GLS_MODE=prod   -> canlı ortam

GLS tek bir "API" vermez; birbirinden bağımsız ÜÇ kanal vardır ve hangisinin
verildiği müşteriden müşteriye değişir. Bu yüzden üçü de ayrı yapılandırılır ve
`gls_api/providers.py` .env'de hangisi doluysa onu kullanır:

  A) ShipIT REST      — etiket + takip + POD (PDF).  URL müşteriye özeldir.
  B) Track&Trace SOAP — sadece takip + POD.          gls-group.eu Uni-Portal girişi.
  C) GLS Netherlands  — etiket (api.gls.nl/v1/api) + takip (api.gls.nl/tt/V1)
                        + POD (apm.gls.nl). UCU DE AYRI HOST'tur; etiket ve takip
                        ayni kimligi kullanir, POD ucu kimlik istemez.
"""
import os
import re
from pathlib import Path


def _load_dotenv(path: str = ".env"):
    """Harici bağımlılık olmadan basit .env okuyucu."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

MODE = os.getenv("GLS_MODE", "mock").lower()
IS_MOCK = MODE == "mock"

# Yerel mock sunucu (gls_api/mock_server.py). mock modda tüm kanallar buraya bakar.
MOCK_BASE_URL = "http://127.0.0.1:8788"


def _env(name: str, mock_default: str = "", real_default: str = "") -> str:
    """mock modda mock_default, aksi halde real_default ile ortam degiskeni okur."""
    return os.getenv(name, mock_default if IS_MOCK else real_default).strip()


# ---------------------------------------------------------------------------
# Canli yapilandirma katmani
# ---------------------------------------------------------------------------
# Asagidaki degerler ARTIK modul sabiti DEGIL: `_ENV_DEFAULTS` sozlugunde durur ve
# `__getattr__` (PEP 562) uzerinden okunur. Sebep: panelden (`/settings`) kaydedilen
# ayarlarin konteyner yeniden baslamadan devreye girmesi gerekiyor ve `.env`
# konteynere mount edilmedigi icin dosyaya yazmak mumkun degil.
#
# Disaridaki 45 kullanim yerinin HICBIRI degismedi — hepsi zaten `config.X` bicimindeydi.
# Ama MODUL ICINDE ciplak ad kullanilamaz: `SHIPIT_BASE_URL` yazmak dogrudan
# `globals()`e gider ve `__getattr__`a hic ulasmaz. Bu dosyanin icinde daima
# `get("SHIPIT_BASE_URL")` kullanin.
_ENV_DEFAULTS: dict[str, object] = {}
_OVERRIDES: dict[str, object] = {}

# Mock modda bu oneklerle baslayan override'lar UYGULANMAZ (saklanir ama devreye
# girmez): aksi halde bir gelistirme makinesi veya CI canli api.gls.nl'e istek atardi.
_MOCK_PROTECTED_PREFIXES = ("SHIPIT_", "TT_", "NL_", "FEDEX_", "GLSG_")


def _compute_env_defaults() -> dict[str, object]:
    return {
        # --- E) FedEx Track API (yalnizca takip) -----------------------------
        # Avustralya/Yeni Zelanda Aramex uzerinden, Kanada/Irlanda numune
        # gonderileri FedEx uzerinden gidiyor — bu sistemde HENUZ hicbir kanal
        # yok (kullanici karari, 2026-08). OAuth2 client_credentials: token
        # `FEDEX_BASE_URL/oauth/token`den alinir, takip `/track/v1/trackingnumbers`e
        # gider. Sandbox (`apis-sandbox.fedex.com`) varsayilan — canliya gecmek
        # icin .env'de `FEDEX_BASE_URL=https://apis.fedex.com` yazilir.
        "FEDEX_BASE_URL": _env("FEDEX_BASE_URL", f"{MOCK_BASE_URL}/fedex",
                               "https://apis-sandbox.fedex.com"),
        "FEDEX_API_KEY": _env("FEDEX_API_KEY", "mock-fedex-key"),
        "FEDEX_API_SECRET": _env("FEDEX_API_SECRET", "mock-fedex-secret"),
        "FEDEX_ACCOUNT_NUMBER": _env("FEDEX_ACCOUNT_NUMBER", ""),
        # ERP'den FedEx kolisi cekmenin TARIH TABANI (paketleme tarihi).
        # ERP'de tasiyici alani cogunlukla bos oldugu icin numara bicimine de
        # bakiliyor (bkz. erp/sync.py:BOX_QUERY); tarih tabani olmadan bu kural
        # `Erp_Box`in tum gecmisini supuruyor. Olculdu 2026-09-02: sinirsiz
        # halde 605 koli, 287'si bir gecede panele dustu ve FedEx grubuna tek
        # saatte 207 mesaj gitti. Diger FEDEX_* gibi `.env`den ayarlanir
        # (panel semasinda FedEx bolumu henuz yok).
        "FEDEX_SYNC_FROM_DATE": _env("FEDEX_SYNC_FROM_DATE", "2026-08-01", "2026-08-01"),

        # --- A) GLS ShipIT REST (etiket + takip + POD/PDF) -------------------
        # Taban URL musteriye ozel olarak GLS'ten gelir, ornegin:
        #   https://<host>.gls-group.eu:8443/backend
        "SHIPIT_BASE_URL": _env("SHIPIT_BASE_URL", f"{MOCK_BASE_URL}/shipit"),
        "SHIPIT_USERNAME": _env("SHIPIT_USERNAME", "mock-user"),
        "SHIPIT_PASSWORD": _env("SHIPIT_PASSWORD", "mock-pass"),
        # ContactID: "<musteri no> <depo kodu>" bicimindedir (or. "276a0b1c IES").
        # Etiket olustururken zorunlu; takip/POD icin gerekmez.
        "SHIPIT_CONTACT_ID": _env("SHIPIT_CONTACT_ID", "MOCK CONTACT"),

        # --- B) GLS Track & Trace SOAP (sadece takip + POD) ------------------
        # Uc nokta grup capinda sabittir; kimlik = gls-group.eu Uni-Portal kullanicisi.
        "TT_ENDPOINT": _env(
            "TT_ENDPOINT",
            f"{MOCK_BASE_URL}/tt/services/Tracking",
            "https://www.gls-group.eu/276-I-PORTAL-WEBSERVICE/services/Tracking",
        ),
        "TT_USERNAME": _env("TT_USERNAME", "mock-user"),
        "TT_PASSWORD": _env("TT_PASSWORD", "mock-pass"),

        # --- C) GLS Netherlands REST (etiket) -------------------------------
        # Canli: https://api.gls.nl/v1/api  |  Test: https://api.gls.nl/test/v1/api
        # API anahtari YOKTUR — kimlik JSON govdesinde username/password ile gider.
        "NL_BASE_URL": _env("NL_BASE_URL", f"{MOCK_BASE_URL}/nl", "https://api.gls.nl/v1/api"),
        "NL_USERNAME": _env("NL_USERNAME", "mock-user"),
        "NL_PASSWORD": _env("NL_PASSWORD", "mock-pass"),
        # Musteri no bos birakilirsa ValidateLogin cagrisindan otomatik cozulur.
        "NL_CUSTOMER_NO": _env("NL_CUSTOMER_NO", ""),
        # C1) GLS NL Track & Trace — SADECE takip. Etiket API'sinden AYRI servis:
        # baska taban URL, govde alani `userName`. Kimlik ayni.
        "NL_TT_BASE_URL": _env("NL_TT_BASE_URL", f"{MOCK_BASE_URL}/nltt", "https://api.gls.nl/tt/V1"),
        # C1b) GLS NL POD ucu — takip API'sinden de AYRI host. Kimlik dogrulamasi
        # YOKTUR; anahtar parcanin `uniqueNo` + `jobDate` ciftidir.
        "NL_POD_BASE_URL": _env("NL_POD_BASE_URL", f"{MOCK_BASE_URL}/nlpod", "https://apm.gls.nl"),

        # --- F) GLS Grup geçidi — OAuth 2.0 + Track & Trace v1 -------------
        # Klasik ShipIT'ten (A) FARKLI yol: dev-portal.gls-group.net'te açılan
        # "App"in API Key/Secret'iyle önce token alınır, sonra grubun ortak
        # geçidi api.gls-group.net üzerinden çağrılır. Sadece TAKİP; etiket ve
        # POD yoktur (POD ShipIT-Farm'dan gelecek — o ürün bu App ID'ye henüz
        # açılmadı, aynı token'la 401 dönüyor). Ayrıntı: docs/IT_TALEP_NOTU.md (D).
        # Track & Trace v1 canlı doğrulandı (2026-09-02, HTTP 200).
        "GLSG_TOKEN_URL": _env(
            "GLSG_TOKEN_URL",
            f"{MOCK_BASE_URL}/glsg/oauth2/v2/token",
            "https://api.gls-group.net/oauth2/v2/token",
        ),
        "GLSG_BASE_URL": _env(
            "GLSG_BASE_URL",
            f"{MOCK_BASE_URL}/glsg",
            "https://api.gls-group.net",
        ),
        "GLSG_CLIENT_ID": _env("GLSG_CLIENT_ID", "mock-user"),
        "GLSG_CLIENT_SECRET": _env("GLSG_CLIENT_SECRET", "mock-pass"),
        # Token isteğindeki `scope`. GLS "all" öneriyor; boş bırakılırsa scope
        # hiç gönderilmez (JWT'deki `scp` zaten boş dönüyor, T&T v1'i etkilemiyor).
        "GLSG_SCOPE": _env("GLSG_SCOPE", "all", "all"),
        # Günlük istek tavanı. GLS'in verdiği kota 500/gün; tracker 15 dakikada
        # bir çalıştığı için (96 tur) 63 aktif IE parçası bile 672 istek eder —
        # `tt_v1_client` bu sayacı tutup kota bitince istek ATMAZ. GLS kotayı
        # yükseltince (onboarding formu gönderildi) panelden bu değer artırılır.
        "GLSG_DAILY_LIMIT": int(os.getenv("GLSG_DAILY_LIMIT", "500")),

        # --- D) Sentez / BlueCherry MSSQL -----------------------------------
        # Panele hangi parcalarin girecegini artik elle yuklenen Excel degil bu
        # veritabani soyler. Erisim SALT OKUMADIR; erp/client.py bunu zorlar.
        "MSSQL_SERVER": _env("MSSQL_SERVER", ""),
        "MSSQL_PORT": int(os.getenv("MSSQL_PORT", "1434")),
        "MSSQL_DATABASE": _env("MSSQL_DATABASE", ""),
        "MSSQL_USERNAME": _env("MSSQL_USERNAME", ""),
        "MSSQL_PASSWORD": _env("MSSQL_PASSWORD", ""),
        # Sirali sezon gorunumleri. Yeni sezon acildiginda tek yapilacak is bu
        # listenin sonuna eklemek (or. ...,Vision_SP27_Boxed_EUROPE) — kod degismez.
        "MSSQL_SEASON_VIEWS": _env("MSSQL_SEASON_VIEWS", ""),
        # Takip turundan sonra durumu ERP'ye (erp_Box) yaz. Mock modda MSSQL
        # zaten yapilandirilmadigi icin islevsizdir; korumasi `mssql_configured()`.
        "ERP_WRITEBACK_ENABLED": _env("ERP_WRITEBACK_ENABLED", "1", "1"),
        # Alici adres kaynagi: "erp" = Sentez Erp_Address canli sorgu (CurrentAccountId 5353),
        # "book" = yerel defter (mock/test ortaminda).
        "ADDRESS_SOURCE": _env("ADDRESS_SOURCE", "erp", "erp"),

        # --- Arayuz ----------------------------------------------------------
        # Panelin ve gunluk ozet mailinin VARSAYILAN dili. Kullanici ust
        # cubuktaki TR|EN anahtariyla kendi tarayicisi icin degistirir (cerez);
        # mail ile Excel ekinin cerezi yoktur, bu ayari kullanirlar.
        "UI_LANG": _env("UI_LANG", "tr"),

        # --- Takip tarayicisi -----------------------------------------------
        "TRACKER_INTERVAL_MINUTES": int(os.getenv("TRACKER_INTERVAL_MINUTES", "15")),
        # Tek tarama turunda en fazla kac parca sorgulanir. GLS NL takip ucu
        # 20'serli gruplar alir ve saatlik istek siniri vardir; 4000+ parcayi tek
        # turda sormak siniri asar. En uzun suredir bakilmayanlar once sorgulanir.
        "TRACKER_MAX_PER_TICK": int(os.getenv("TRACKER_MAX_PER_TICK", "600")),
        # Tek turda en fazla kac teslimatin POD'u arsivlenir. POD ayri bir hosttan
        # (apm.gls.nl) ve parca basina ayri istekle gelir — toplu sorgu YOKTUR.
        # 40 -> 12: her istek ~3,5 sn ve tek-vCPU sunucuda tur boyunca event
        # loop'u tikatiyordu (2026-09-03: "yine kilitleniyor"). Kuyruk artik
        # seyreltilmis (bkz. POD_FRESH_RETRY_HOURS) — 12 tavan yeter.
        "TRACKER_MAX_POD_PER_TICK": int(os.getenv("TRACKER_MAX_POD_PER_TICK", "12")),
        # Tarama kac saattir basarisizsa uyari maili gider. 0 = uyari kapali.
        # 6 saat = 24 kacirilmis tur; GLS'in 429 hiz siniri ve kisa kesintiler
        # normaldir, dar bir esik uyarilari okunmaz yapardi.
        "TRACKER_ALERT_HOURS": int(os.getenv("TRACKER_ALERT_HOURS", "6")),
        # MSSQL'den yeni koli cekme sikligi. 0 = kapali (yalnizca panel dugmesi).
        # Bu is olmadan senkron ELLE kaliyordu: 2026-08-18'de son senkron 5 gun
        # oncesine aitti ve 454 koli panele hic girmemisti.
        # 60 -> 30 (kullanici 2026-09-03: yeni FedEx gonderileri panele daha
        # cabuk dussun; tam resync ~66 sn surer, DB indeks+synchronous=NORMAL
        # ile hizlandi, yarim saatte bir yuk sorun degil).
        "ERP_SYNC_INTERVAL_MINUTES": int(os.getenv("ERP_SYNC_INTERVAL_MINUTES", "30")),

        # --- Gunluk ozet bildirimi -------------------------------------------
        # Varsayilan KAPALI: sevk edildigi anda kimseye mail gitmesin, kullanici
        # bilerek acsin. Sema'da bool tipi yok, "0"/"1" select ile modellendi.
        "NOTIFY_ENABLED": _env("NOTIFY_ENABLED", "0"),
        "NOTIFY_RECIPIENTS": _env("NOTIFY_RECIPIENTS", ""),
        # Gonderim saatleri, virgulle. Iki sevkiyat penceresi var: sabah bir
        # onceki gunun kapanisi, aksam gun icinde teslim edilenler.
        "NOTIFY_HOURS": os.getenv("NOTIFY_HOURS", "9,18"),
        "NOTIFY_STALE_DAYS": int(os.getenv("NOTIFY_STALE_DAYS", "7")),
        # Maildeki "Kontrol Merkezini Ac" dugmesinin hedefi. Bos ise dugme cizilmez.
        "NOTIFY_PANEL_URL": _env("NOTIFY_PANEL_URL", ""),

        # --- POD gelmedi, GLS'e sor ------------------------------------------
        # Bu mail DISARIYA, GLS musteri hizmetlerine gider. Varsayilan KAPALI:
        # bir kurulum kendiliginden GLS'e yazmaya baslamasin.
        # `_env` ucuncu argumani CANLI moddaki varsayilan — verilmezse canli
        # kurulumda bos doner (mock'ta dogru gorunup canlida bosalmisti).
        "GLS_INQUIRY_ENABLED": _env("GLS_INQUIRY_ENABLED", "0", "0"),
        "GLS_INQUIRY_RECIPIENTS": _env("GLS_INQUIRY_RECIPIENTS",
                                       "klantenservice@gls-netherlands.com",
                                       "klantenservice@gls-netherlands.com"),
        # Teslimattan sonra beklenecek gun. POD saatler, bazen bir gun sonra
        # olusuyor; erken sorulursa GLS'e var olmayan belge sorulmus olur.
        "GLS_INQUIRY_WAIT_DAYS": int(os.getenv("GLS_INQUIRY_WAIT_DAYS", "7")),

        # --- Bildirim kanallari (kimlik) -------------------------------------
        "MAIL_TENANT_ID": _env("MAIL_TENANT_ID", ""),
        "MAIL_CLIENT_ID": _env("MAIL_CLIENT_ID", ""),
        "MAIL_CLIENT_SECRET": _env("MAIL_CLIENT_SECRET", ""),
        # Gonderen ADI bilerek ayar DEGIL: Graph govdesindeki `from.name`
        # Exchange tarafindan yok sayilir, gelen kutusunda her zaman posta
        # kutusunun dizindeki adi cikar (2026-08-05 canli dogrulandi). Ad
        # M365 yonetici merkezinden degistirilir.
        "MAIL_SENDER": _env("MAIL_SENDER", ""),
        "WA_PROVIDER": _env("WA_PROVIDER", "openwa", "openwa"),
        "WA_ACCOUNT_ID": _env("WA_ACCOUNT_ID", ""),
        "WA_TOKEN": _env("WA_TOKEN", ""),
        "WA_SENDER": _env("WA_SENDER", ""),

        # --- OpenWA WhatsApp Bildirimleri (Grup Mesajlari) -------------------
        "OPENWA_ENABLED": _env("OPENWA_ENABLED", "1", "1"),
        "OPENWA_URL": _env("OPENWA_URL", "http://192.0.2.10:2785", "http://192.0.2.10:2785"),
        "OPENWA_SESSION_ID": _env("OPENWA_SESSION_ID", "8acfc80f-383a-4402-91db-2a0c6cd88d59", "8acfc80f-383a-4402-91db-2a0c6cd88d59"),
        "OPENWA_API_KEY": _env("OPENWA_API_KEY", "owa_k1_b1488c8c29e5279f60038c6900d4c710b05b1218bf4c0748244565b02ea8329f", "owa_k1_b1488c8c29e5279f60038c6900d4c710b05b1218bf4c0748244565b02ea8329f"),
        "OPENWA_CHAT_ID": _env("OPENWA_CHAT_ID", "100000000000001@g.us", "100000000000001@g.us"),
        # FedEx AYRI gruba yazar (kullanici karari 2026-09-01). FedEx kolileri
        # ana GLS grubuna girmiyordu ("wp bildirimi haric", 2026-08-31); artik
        # tamamen susmak yerine kendi grubuna bildiriyor. Bos birakilirsa
        # FedEx bildirimi HIC gonderilmez — ana gruba SIZMAZ.
        "OPENWA_FEDEX_CHAT_ID": _env("OPENWA_FEDEX_CHAT_ID",
                                      "100000000000002@g.us",
                                      "100000000000002@g.us"),
        # Baslangic tarihi: bu tarihten onceki eski kayitlar icin bildirim gitmez
        "OPENWA_START_DATE": _env("OPENWA_START_DATE", "2026-08-21", "2026-08-21"),
        "OPENWA_FORCE_LIVE": False,
    }


_ENV_DEFAULTS = _compute_env_defaults()


def get(name: str, default=None):
    """Anahtarin gecerli degerini dondurur.

    Ortam degiskeni set edilmisse onu, yoksa `_DEFAULTS` sozlugundeki
    varsayilani dondurur.
    """
    if name in _OVERRIDES:
        return _OVERRIDES[name]
    if default is not None and name not in _ENV_DEFAULTS:
        return default
    return _ENV_DEFAULTS[name]


def __getattr__(name: str):
    """PEP 562 — yalnizca modul globalinde OLMAYAN adlar icin cagrilir."""
    try:
        return get(name)
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    return sorted(list(globals()) + list(_ENV_DEFAULTS))


def apply_overrides(mapping: dict) -> None:
    """Panelden gelen ayarlari devreye alir. Bilinmeyen anahtarlar yok sayilir."""
    global _OVERRIDES
    clean = {k: v for k, v in mapping.items() if k in _ENV_DEFAULTS}
    if IS_MOCK:
        clean = {k: v for k, v in clean.items()
                 if not k.startswith(_MOCK_PROTECTED_PREFIXES)}
    _OVERRIDES = clean


def ignored_override_keys(mapping: dict) -> list[str]:
    """Mock modda saklanip UYGULANMAYAN anahtarlar — arayuzde uyari icin."""
    if not IS_MOCK:
        return []
    return sorted(k for k in mapping
                  if k in _ENV_DEFAULTS and k.startswith(_MOCK_PROTECTED_PREFIXES))


def clear_overrides() -> None:
    _OVERRIDES.clear()


# ---------------------------------------------------------------------------
# D) Sentez / BlueCherry MSSQL  — takip numaralarinin BIRINCIL kaynagi
# ---------------------------------------------------------------------------
# Degerler `_compute_env_defaults()` icinde; panelden ezilebilir.
def season_views() -> list[str]:
    return [v.strip() for v in get("MSSQL_SEASON_VIEWS").split(",") if v.strip()]


_SEASON_RE = re.compile(r"[_ ]([A-Z]{2})(\d{2})[_ ]")
# Takvim sirasi: SP26 ilkbahar, FA26 sonbahar. Yil esitse FA sonradir.
_SEASON_ORDER = {"SP": 0, "SS": 0, "FA": 1, "AW": 1, "FW": 1}


def season_label() -> str:
    """Sezon gorunum adindan sezon kodu: Vision_FA26_Boxed_EUROPE -> FA26.

    Ayri bir ayar alani ISTEMEDEN dogru cikar: sezon degistiginde kullanici
    zaten `MSSQL_SEASON_VIEWS`i guncelliyor, ayni bilgiyi ikinci kez yazmasin.

    Birden fazla gorunum tanimliysa EN YENI sezon kazanir: gecis doneminde eski
    gorunum bir sure listede birakiliyor ve ilkini secmek ozeti gecmis sezonun
    adiyla etiketliyordu (SP26+FA26 tanimliyken basliga "SP26" yaziyordu).

    Ozet mailinin basligi da POD arsivinin kok klasoru de bunu kullaniyor.
    """
    seasons = []
    for view in season_views():
        found = _SEASON_RE.search(view)
        if found:
            code, year = found.group(1), found.group(2)
            seasons.append((int(year), _SEASON_ORDER.get(code, 0), code + year))
    return max(seasons)[2] if seasons else ""


def season_sort_key(code: str) -> tuple[int, int]:
    """'FA26' -> (26, 1). Sezon listesi TAKVIM sirasina dizilsin diye.

    Duz alfabetik siralama yanlis sonuc verir: "FA26" < "SP26" cikar ama sonbahar
    ilkbahardan SONRADIR.
    """
    found = re.fullmatch(r"([A-Za-z]{2})(\d{2})", (code or "").strip())
    if not found:
        return (-1, -1)
    return (int(found.group(2)), _SEASON_ORDER.get(found.group(1).upper(), 0))


def season_from_source(source_sheet: str) -> str:
    """Parcanin geldigi kaynagin adindan sezon kodu: Vision_FA26_Boxed_EUROPE -> FA26.

    `season_label()` yapilandirmadaki EN YENI sezonu verir; burada ise PARCANIN
    kendi sezonu lazim (arsiv klasoru gecmis sezonlari da gostermeli). Gorunum
    adi olmayan kaynaklarda (`Erp_Box`, bos) sezon bilgisi yok — guncel sezona
    dusulur ki parca klasorsuz kalmasin.
    """
    found = _SEASON_RE.search(source_sheet or "")
    return found.group(1) + found.group(2) if found else season_label()


def mssql_configured() -> bool:
    return all(get(k) for k in ("MSSQL_SERVER", "MSSQL_DATABASE",
                                "MSSQL_USERNAME", "MSSQL_PASSWORD")) and bool(season_views())


# `season_views()` ile ayni gerekce: csv alanlar config'te metin durur, listeye
# cevirmek cagiranin isi olmasin — tek yerde ayristirilsin.
def notify_recipients() -> list[str]:
    return [v.strip() for v in get("NOTIFY_RECIPIENTS").split(",") if v.strip()]


def notify_hours() -> list[int]:
    """Ozetin gonderilecegi saatler — kucukten buyuge, tekrarsiz.

    Bozuk deger sessizce DUSURULUR ve liste bosalirsa 09:00'a donulur: hatali
    bir ayar yuzunden gunluk ozetin tamamen susmasi, yanlis saatte gitmesinden
    daha kotudur (kimse fark etmez).
    """
    hours = sorted({int(v) for v in get("NOTIFY_HOURS").split(",")
                    if v.strip().isdigit() and 0 <= int(v) <= 23})
    return hours or [9]


def gls_inquiry_recipients() -> list[str]:
    return [v.strip() for v in get("GLS_INQUIRY_RECIPIENTS").split(",") if v.strip()]


# ---------------------------------------------------------------------------
# Çıktı ve davranış
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", os.path.expanduser("~/Desktop/SP26_API"))).expanduser()

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "0.5"))
# TRACKER_* degerleri panelden duzenlenebilir; `_compute_env_defaults()` icinde.

# ---------------------------------------------------------------------------
# Kimlik dogrulama (panel oturumlari)
# ---------------------------------------------------------------------------
def _persisted_secret_key() -> tuple[str, bool]:
    """Oturum imza anahtari — .env'de verilmemisse diske sabitlenir.

    Eskiden her acilista rastgele uretiliyordu: her yeniden baslatma (yani her
    aktarim) herkesi disari atiyordu, kullanicilar sebepsiz yere yeniden giris
    yapiyordu. Anahtar veritabanlariyla ayni klasorde, yalnizca sahibinin
    okuyabilecegi izinle (0600) durur.

    Doner: (anahtar, kalici_mi). Kalici degilse oturumlar yine restart'ta duser
    (eski davranis) — `readiness_check()` bunu uyari olarak bildirir.
    """
    path = Path.home() / ".gls_pod" / "session_key"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing, True
    except OSError:
        pass

    key = os.urandom(32).hex()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        return key, False  # salt-okunur dosya sistemi
    return key, True


_secret_from_env = os.getenv("AUTH_SECRET_KEY")
AUTH_SECRET_KEY, AUTH_SECRET_KEY_PERSISTED = (
    (_secret_from_env, True) if _secret_from_env else _persisted_secret_key()
)
AUTH_SESSION_MAX_AGE = int(os.getenv("AUTH_SESSION_MINUTES", "20")) * 60
AUTH_REFRESH_MAX_AGE_DAYS = int(os.getenv("AUTH_REFRESH_DAYS", "30"))
AUTH_ADMIN_USERNAME = os.getenv("AUTH_ADMIN_USERNAME", "admin")
AUTH_ADMIN_PASSWORD = os.getenv("AUTH_ADMIN_PASSWORD", "")
# Cookie'lerde Secure bayragi — sadece HTTPS uzerinden gonderilsin. mock/dev'de
# http://localhost uzerinden test edilebilsin diye MODE=mock'ta varsayilan kapali.
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false" if IS_MOCK else "true").lower() == "true"


# ---------------------------------------------------------------------------
# Hangi sağlayıcı yapılandırılmış?
# ---------------------------------------------------------------------------
# Mock modda her uc kanal da yerel mock sunucuya baktigi icin "yapilandirilmis"
# sayilir. test/prod'da ise mock artigi degerler yapilandirilmamis kabul edilir.
_MOCK_SENTINELS = {"mock-user", "mock-pass", "mock-key", "MOCK CONTACT", ""}


def _real(value: str) -> bool:
    """Deger gercek mi (bos degil, mock artigi degil, mock sunucuyu gostermiyor)?"""
    if not value or value in _MOCK_SENTINELS:
        return False
    return not value.startswith(MOCK_BASE_URL)


def shipit_configured() -> bool:
    if IS_MOCK:
        return True
    return all(_real(get(k)) for k in ("SHIPIT_BASE_URL", "SHIPIT_USERNAME", "SHIPIT_PASSWORD"))


def tt_configured() -> bool:
    if IS_MOCK:
        return True
    # TT_ENDPOINT'in gercek varsayilani zaten dolu — belirleyici olan kimliktir.
    return bool(get("TT_ENDPOINT")) and all(_real(get(k)) for k in ("TT_USERNAME", "TT_PASSWORD"))


def nl_configured() -> bool:
    if IS_MOCK:
        return True
    return all(_real(get(k)) for k in ("NL_BASE_URL", "NL_USERNAME", "NL_PASSWORD"))


def nl_tt_configured() -> bool:
    """NL takip ucu — kimlik etiket API'siyle ayni, ek olarak taban URL gerekir."""
    if IS_MOCK:
        return True
    return _real(get("NL_TT_BASE_URL")) and all(_real(get(k)) for k in ("NL_USERNAME", "NL_PASSWORD"))


def fedex_configured() -> bool:
    if IS_MOCK:
        return True
    return all(_real(get(k)) for k in ("FEDEX_API_KEY", "FEDEX_API_SECRET"))


def glsg_tt_configured() -> bool:
    """GLS Grup gecidi (OAuth 2.0) — Track & Trace v1. Token/taban URL + App kimligi gerekir."""
    if IS_MOCK:
        return True
    return (_real(get("GLSG_TOKEN_URL")) and _real(get("GLSG_BASE_URL"))
            and all(_real(get(k)) for k in ("GLSG_CLIENT_ID", "GLSG_CLIENT_SECRET")))


def configured_providers() -> list[str]:
    out = []
    if shipit_configured():
        out.append("shipit")
    if tt_configured():
        out.append("tt")
    if nl_configured():
        out.append("nl")
    if fedex_configured():
        out.append("fedex")
    return out


def readiness_check() -> list[str]:
    """test/prod'da eksik veya tutarsiz yapilandirmayi Turkce olarak listeler.

    Amac: uygulamayi engellemek degil, acilista net uyari vermek. `cli.py doctor`
    bunun uzerine bir de gercek baglanti testi yapar.
    """
    if IS_MOCK:
        return []

    issues: list[str] = []
    providers = configured_providers()

    if not providers:
        issues.append(
            "Hicbir GLS saglayicisi yapilandirilmamis — .env'de en az birini doldurun: "
            "SHIPIT_* (etiket+takip+POD) / TT_* (takip+POD) / NL_* (etiket)"
        )

    # Kismi yapilandirma: URL girilmis ama kimlik yok (veya tersi) — sessiz kalirsa
    # kullanici "neden calismiyor" diye saatlerce arar.
    if _real(get("SHIPIT_BASE_URL")) and not shipit_configured():
        issues.append("SHIPIT_BASE_URL girilmis ama SHIPIT_USERNAME/SHIPIT_PASSWORD eksik")
    if (_real(get("TT_USERNAME")) or _real(get("TT_PASSWORD"))) and not tt_configured():
        issues.append("TT_USERNAME/TT_PASSWORD ikisi birden dolu olmali")
    if _real(get("NL_USERNAME")) and not nl_configured():
        issues.append("NL_USERNAME girilmis ama NL_PASSWORD veya NL_BASE_URL eksik")

    # GLS NL'in dogruladigimiz siniri: sifre en fazla 20 karakter.
    if nl_configured() and len(get("NL_PASSWORD")) > 20:
        issues.append("NL_PASSWORD 20 karakterden uzun — GLS NL API'si bunu kabul etmez")

    # GLS Grup gecidi (OAuth 2.0 / Track & Trace v1): App kimliginin bir yarisi
    # dolu digeri bossa sessiz kalmasin — token hic alinamaz.
    if (_real(get("GLSG_CLIENT_ID")) or _real(get("GLSG_CLIENT_SECRET"))) \
            and not glsg_tt_configured():
        issues.append("GLSG_CLIENT_ID/GLSG_CLIENT_SECRET ikisi de dolu olmalı "
                      "(GLS Grup geçidi — Track & Trace v1)")

    # Kismi MSSQL yapilandirmasi: panel sessizce Excel'e geri duser ve kimse
    # takip listesinin neden eksik oldugunu anlamaz.
    if any(get(k) for k in ("MSSQL_SERVER", "MSSQL_USERNAME", "MSSQL_PASSWORD")) \
            and not mssql_configured():
        issues.append(
            "MSSQL yapilandirmasi eksik — MSSQL_SERVER / MSSQL_DATABASE / MSSQL_USERNAME / "
            "MSSQL_PASSWORD / MSSQL_SEASON_VIEWS hepsi dolu olmali"
        )

    if MODE == "prod":
        if not AUTH_SECRET_KEY_PERSISTED:
            issues.append("Oturum anahtari diske yazilamadi — her yeniden baslatmada tum oturumlar dusecek")
        if not AUTH_COOKIE_SECURE:
            issues.append("AUTH_COOKIE_SECURE=false — HTTPS arkasindaysa true olmali")

    return issues
