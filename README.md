# Dolcezza Gönderi Takip Sistemi

Dolcezza Netherlands BV'nin Avrupa mağaza sevkiyatlarını uçtan uca yöneten iç panel:
**etiket üretimi**, **canlı takip**, **teslimat kanıtı (POD) arşivi**, **sorunlu kargo
takibi** ve **WhatsApp / e-posta bildirimleri**. Kargolar üç taşıma firması üzerinden
gider ve sistem her birini kendi resmî API'siyle izler.

FastAPI + Jinja2 (sunucu tarafı render) + HTMX/Alpine.js + SQLite. Docker ile tek
konteyner olarak çalışır; veri kaynağı Sentez/BlueCherry MSSQL'idir.

> **Not:** Bu depo, üzerinde tek başıma geliştirdiğim canlı bir üretim sisteminin
> **anonimleştirilmiş** bir kopyasıdır — portföy amaçlı paylaşılıyor. Gerçek mağaza
> kodları, müşteri adları, adresler, iç ağ adresleri ve sunucu kimlikleri kurgusal
> değerlerle değiştirildi; iş mantığı ve mimari birebir korundu.

---

## İçindekiler

- [Taşıma firmaları / kanallar](#taşıma-firmaları--kanallar)
- [Proje düzeni](#proje-düzeni) — hangi klasör ne işe yarıyor
- [Yerel geliştirme](#yerel-geliştirme-mock-mod)
- [Panel sayfaları](#panel-sayfaları)
- [Arka plan işleri](#arka-plan-işleri-scheduler)
- [Ayarlar](#ayarlar-settings)
- [Dağıtım (canlı)](#dağıtım-canlı)
- [Testler](#testler)

---

## Taşıma firmaları / kanallar

Kanal **ERP'nin `UD_TasimaFirmasi` alanından** belirlenir (`erp/sync.py`), takip
numarası biçiminden değil. `gls_api/providers.py` hangi kanala hangi istemcinin
gideceğini seçer.

| Kanal (`parcels.channel`) | ERP değeri | Etiket | Takip | POD |
|---|---|---|---|---|
| **NL** — GLS Netherlands | `GLS-NL` | `api.gls.nl/v1/api` (`nl_client.py`) | `api.gls.nl/tt/V1` + `apm.gls.nl` teslim taraması (`nl_track_client.py`) | `apm.gls.nl` |
| **IE** — GLS Ireland | `GLS-IE` | (bu sistemden basılmaz) | GLS Group **Track & Trace v1** OAuth2, `api.gls-group.net` (`tt_v1_client.py`) | ShipIT-Farm yetkisi bekliyor → şu an yok |
| **FEDEX** | `FEDEX` | (FedEx kendi Ship Manager'ında) | FedEx Track API OAuth2 (`fedex_client.py`) | belge yok → panelden elle yüklenen "Shipment Details" PDF |

> **Not (sınır ötesi NL):** FR/ES/BE'ye giden NL kolilerinde toplu takip ucu son
> teslim adımını geri yazmıyor; tarayıcı `exception` kolileri ayrıca `apm.gls.nl`
> teslim taramasından (`deliveryScanInfo`) geçirir.

Eski kanallar (`shipit_client.py`, `tt_soap_client.py`) kodda duruyor ama canlıda
yapılandırılmamış — GLS Group geçidine geçilmeden önce kullanılıyordu.

---

## Proje düzeni

### Uygulama çekirdeği (backend)

| Klasör | Sorumluluk |
|---|---|
| **`gls_api/`** | Tüm GLS / FedEx API istemcileri + `config.py` (ortam + panel ayarları), `providers.py` (kanal→istemci seçimi), `mock_server.py` (geliştirme için sahte API'ler), `label_payload.py` / `schemas.py` (etiket gövdesi + doğrulama). |
| **`tracking/`** | `db.py` — merkezî SQLite katmanı (parçalar, olaylar, sorunlu kargo sorguları). `scheduler.py` — 15 dakikada bir çalışan takip turu. `delivery_report.py` / `completion_report.py` — rapor üretimi. |
| **`erp/`** | Sentez/BlueCherry MSSQL erişimi (salt okuma). `sync.py` — koli/sevkiyat senkronu (yarım saatte bir), `addresses.py` — mağaza adresleri, `boxes.py` — etiketlenmemiş koliler, `writeback.py` — ERP'ye takip no yazma. |
| **`dispatch/`** | Toplu etiket "planı": `plan.py` (saf mantık — mağaza/fatura gruplama, birleştir/ayır kuralları), `engine.py` (GLS'e istek atıp PDF üreten yan etkili kısım). |
| **`notify/`** | Bildirim kanalları: `whatsapp.py` (OpenWA/WAHA), `mail.py` + `digest.py` (Microsoft Graph günlük özet), `excel.py` (rapor eki), `pod_chase.py` (POD gecikince GLS'e mail), `scheduler.py`. |
| **`auth/`** | Oturum, JWT/refresh, rol (admin/operator/viewer), rate-limit, IP engelleme, ilk admin seed'i. |
| **`settings/`** | Panelden düzenlenebilen ayarların şeması (`schema.py`), kalıcılığı (`store.py` → `~/.gls_pod/settings.db`) ve çalışırken uygulanması (`apply.py`). |
| **`address_book/`** | Yerel adres defteri (mock/test'te ERP yerine kullanılır; canlıda ERP'yi tamamlar). |
| **`db/`** | Ortak SQLite yardımcıları: `migrate.py` (`ALTER TABLE ADD COLUMN` göçleri) ve `backup.py` (gecelik yedek). Hiçbir üst modülü import etmez. |
| **`i18n/`** | Arayüz metinleri — `tr.py` (şemadan otomatik türetilir) + `en.py`. |
| **`timez.py`** | Saat dilimi yardımcıları (Europe/Istanbul / Amsterdam). |
| **`cli.py`** | Panelsiz komut satırı: `doctor` (hangi sağlayıcı canlı), `run` (Excel'den uçtan uca), `seed_admin`. |

### Web katmanı (frontend)

| Yol | Sorumluluk |
|---|---|
| **`web/main.py`** | FastAPI uygulaması + `lifespan` (scheduler'ları başlatır), `/health`, güvenlik başlıkları (CSP). |
| **`web/routers/`** | Sayfa başına bir router: `dashboard`, `tracking`, `labels`, `dispatch`, `pod`, `problems`, `reports`, `archive`, `settings`, `auth`, `lang`. |
| **`web/templates/`** | Jinja2 şablonları (45 dosya); `_partials/` HTMX ile parça parça yenilenen bölümler. |
| **`web/static/`** | Logolar + `vendor/` (Tailwind, htmx, Alpine, Chart.js **yerel kopyaları** — dış CDN yok, iç ağda donmaya yol açıyordu). |
| **`web/deps.py`** | Tekil nesneler (DB, adres kaynağı, ayar deposu) ve şablon bağlamı. |
| **`web/*_document.py` / `exports.py`** | PDF (POD, Shipment Details) ve Excel çıktı üretimi. |

### Veri ve altyapı

| Yol | Ne |
|---|---|
| **`~/.gls_pod/`** (Docker'da `data/` bind-mount) | Dört SQLite dosyası: `shipments.db`, `addressbook.db`, `auth.db`, `settings.db` + `backups/`. **Repoda tutulmaz.** |
| **`OUTPUT_DIR`** (Docker'da `output/`) | Üretilen etiket PDF'leri ve POD arşivi. Repoda tutulmaz. |
| **`Dockerfile` / `docker-compose.yml`** | Tek `web` servisi (port 8765→8000); `mock` servisi yalnızca `--profile dev`. |
| **`deploy.sh`** | Sunucuya rsync (`--delete`, `data/`+`output/`+`.env` hariç) → `docker compose build web && up -d web`. |
| **`.env.example`** | Tüm ortam değişkenlerinin şablonu. Gerçek `.env` git'e girmez. |
| **`docs/`** | Kurulum, günlük kullanım, geçiş kontrol listesi, Sentez/BlueCherry entegrasyon notları. |
| **`tests/`** | ~180 dosyalık pytest paketi (birim + HTTP uçtan uca; MSSQL ve GLS mock'lanır). |

---

## Yerel geliştirme (mock mod)

Kimlik gerektirmez — `mock_server.py` gerçek API şemalarının kopyasını sunar.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # GLS_MODE=mock (varsayılan)
```

İki terminal:

```bash
# 1) sahte GLS/FedEx API'leri
uvicorn gls_api.mock_server:app --port 8788

# 2) panel
uvicorn web.main:app --port 8000 --reload
```

İlk açılışta bir admin kullanıcısı üretilir; parola `AUTH_ADMIN_PASSWORD` tanımlı
değilse loglara rastgele yazılır (`cli.py seed_admin` ile de kurulabilir).

Toplu etiket ekranını gerçek MSSQL olmadan denemek için:
`ERP_BOXES_MOCK_FIXTURE=1 uvicorn web.main:app ...` (bkz. `.claude/launch.json`).

---

## Panel sayfaları

| Sayfa | Ne yapar |
|---|---|
| `/` | Statü özeti (4+4 kutu, taşıma firması kırılımlı), haftalık grafik, sorunlu kargo sayacı, bugün teslim edilenler. |
| `/labels` | Adres defterinden **tek** GLS-NL etiketi oluştur (anında basar, önizleme yok). |
| `/dispatch` | **Toplu etiket**: paketleme tarihi seç → önizle → (gerekirse fatura ayır / adres düzenle) → onayla → PDF'ler. Önizlemedeki adres düzenlemesi doğrudan etikete işler. |
| `/tracking` | Canlı takip listesi; sol filtre + sütun huni menüleri (dinamik sayaçlı), Excel içe aktarma, "MSSQL'den Yenile". |
| `/pod` | POD arşivi — tekil indirme + toplu ZIP. FedEx satırlarında "Shipment Details" PDF. |
| `/problems` | Sorunlu kargolar: exception / hasar / kısmi teslim / POD eksik / hareketsiz. Teslim edilmişler ve eskiyenler otomatik düşer. |
| `/reports` | Teslimat süreleri, ParcelShop, tamamlanma raporları — GLS-NL / GLS-IE / FEDEX sekmeli, Excel/PDF dışa aktarım. |
| `/archive` | Üretilen etiketlerin arşivi. |
| `/settings` | MSSQL, tarayıcı aralıkları, GLS/FedEx kimlikleri, bildirimler, mail, WhatsApp, kullanıcı yönetimi. |

---

## Arka plan işleri (scheduler)

`web/main.py` `lifespan`'inde başlar (`tracking/scheduler.py`, `notify/scheduler.py`):

| İş | Sıklık | Ne yapar |
|---|---|---|
| Takip turu | 15 dk | Aktif kolileri kanal kanal (NL → IE → FedEx) **sırayla** sorgular, statü + olay geçmişini yazar, teslim olanların POD'unu çeker, ERP'ye takip no yazar. |
| MSSQL senkronu | 30 dk | Sentez/BlueCherry'den etiketlenmemiş/güncel kolileri çeker (`ERP_SYNC_INTERVAL_MINUTES`). |
| Günlük özet | `NOTIFY_HOURS` (örn. 9,18) | İç ekibe yönetici özeti maili + tam liste Excel'i + son 24 saatin POD ZIP'i. |
| DB yedeği | Her gün 03:30 | Dört SQLite dosyasını `~/.gls_pod/backups/` altına kopyalar (14 gün saklar). |

Tarayıcı tek çekirdekli sunucuda event loop'u aç bırakmasın diye tüm ağ
çağrıları sıralı yapılır; ağır sorgular ayrı iş parçacığında (`to_thread`).

---

## Ayarlar (`/settings`)

Panelden kaydedilen değerler `~/.gls_pod/settings.db`'de durur, `.env`'i **ezer**
ve konteyner yeniden başlamadan devreye girer (`settings/apply.py`).

- **Yedeğe dahildir.** Silinirse her değer `.env`'e döner.
- Sır alanları tarayıcıya hiç gönderilmez; boş bırakmak mevcut değeri korur.
- `GLS_MODE=mock` iken GLS kimlikleri kaydedilir ama uygulanmaz.
- Panelden düzenlenemeyenler (bilinçli): `GLS_MODE`, `OUTPUT_DIR`, `AUTH_*` —
  bunlar `.env` + yeniden başlatma işidir.

Tüm değişkenlerin listesi ve açıklaması: `.env.example`.

---

## Dağıtım (canlı)

Sunucu: tek Docker konteyneri, `~/gls-pod` altında. `.env`'de `GLS_MODE=prod`.

```bash
./deploy.sh
```

Yaptığı iş: kaynağı sunucuya `rsync --delete` ile aktarır (`data/`, `output/`,
`.env`, `.git/` hariç), sonra `docker compose build web && docker compose up -d web`.
Ardından `/health` kontrol eder.

`./deploy.sh --dry-run` yalnızca ne gönderileceğini gösterir.

Veritabanları ve çıktılar sunucuda **bind-mount** olduğu için dağıtımdan
etkilenmez.

---

## Testler

```bash
pytest -q
```

MSSQL ve GLS/FedEx API'leri mock'lanır; testler ağa çıkmaz. HTTP uçları
`fastapi.testclient` ile uçtan uca doğrulanır.

---

## Güvenlik ilkeleri

- Kimlikler asla koda yazılmaz; `.env` ve `~/.gls_pod/settings.db` git'e girmez.
- ERP erişimi **salt okumadır** (`WITH (NOLOCK)`); yalnızca takip numarası
  geri yazılır.
- Panel rol tabanlıdır (admin / operator / viewer); yazan uçlar `operator`+ ister.
- Tüm ön yüz varlıkları yerelden sunulur — sayfa hiçbir dış servise bağlı değildir.
- İstekler arasında nazik hız sınırı (`RATE_LIMIT_SECONDS`).
