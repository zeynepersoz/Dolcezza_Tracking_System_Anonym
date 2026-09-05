# GLS Kontrol Merkezi — Ürünleşme Yol Haritası

> Bu doküman üç stratejik soruya cevap verir:
> 1. Kullanıcı girişi, rol ve yetki mimarisi nasıl olmalı?
> 2. GLS'in **tüm** ulusal hesaplarını gerçekten bağlayabiliyor muyuz?
> 3. Bu iş teknik olarak ne kadar bir ürüne dönüşür — neler yapılabilir, neler yapılamaz?

> **Durum notu (Faz 3 — uygulandı):** Aşağıdaki 1.2/1.5 bölümlerindeki auth önerisi
> artık **canlı kodda mevcut** (custom light yaklaşımı, argon2 yerine PBKDF2-SHA256
> ile — bkz. §6.3) ve GLS İrlanda/Hollanda posta kodu + takip numarası doğrulaması
> tamamlandı (bkz. §6.1-6.2). Detaylar ve tam endpoint listesi için §6'ya bakın.
> Rol sayısı ilk sürümde 4 değil **2** (admin/operator) — viewer/auditor v2'ye bırakıldı.

---

## 1. Kullanıcı Girişi + Rol/Yetki Mimarisi

### 1.1 Kullanıcı sınıfları (öneri)

| Rol | Kim | Ne yapabilir | Ne yapamaz |
|---|---|---|---|
| **admin** | IT / şirket yöneticisi | Her şey. Kullanıcı ekle/sil, .env düzenle, GLS kimliği gir, tüm modüller | — |
| **operator** | Sevkiyat sorumlusu (asıl kullanıcı) | Etiket oluştur, tracking yükle, POD indir, adres defteri, şablon | Kullanıcı yönetimi, kimlik bilgisi değiştirme |
| **viewer** | Muhasebe / satış / müdür | Dashboard + Canlı Takip + POD indir | Etiket üretemez, veri yükleyemez, silemez |
| **auditor** | Denetim / dış firma | Yalnız okuma + audit log görüntüleme | Hiçbir yazma yok |

**Neden 4 rol?** Muhasebe "sadece POD indirmek" istiyor ama operator paneline gerek yok — yanlış tıklama riski azalır. Auditor rolü bir gün ISO/SOC denetimi olursa şart.

### 1.2 Teknoloji seçimi

**Öneri: FastAPI-Users veya minimal custom (fastapi + itsdangerous + argon2).**

- **FastAPI-Users** — hazır: register/login/reset-password/verify + role-based dependency. SQLAlchemy adapter var. Bağımlılığı ağır ama olgun.
- **Custom light** (önerilen) — 200 satır:
  - `users` tablosu (id, email, password_hash argon2, role, is_active, created_at)
  - `/login` → cookie (`httponly`, `secure` prod'da, `samesite=lax`) + signed session token (itsdangerous)
  - `Depends(current_user)` + `Depends(require_role("admin"))` dependency'leri
  - Şifre kurtarma e-postası ilk versiyonda **yok** (admin sıfırlar).

### 1.3 Yetki matrisi (kod tarafı)

```python
# web/auth/permissions.py
PERMISSIONS = {
    "labels.create":   {"admin", "operator"},
    "tracking.upload": {"admin", "operator"},
    "tracking.view":   {"admin", "operator", "viewer", "auditor"},
    "pod.download":    {"admin", "operator", "viewer"},
    "addresses.edit":  {"admin", "operator"},
    "settings.write":  {"admin"},
    "users.manage":    {"admin"},
    "audit.view":      {"admin", "auditor"},
}
```

Her router endpoint'inde `Depends(require_permission("labels.create"))`. Template'lerde `{% if can("labels.create") %}...{% endif %}` helper.

### 1.4 Denetim izi (audit log)

Şu **an bile** yapmalıyız çünkü hem KVKK/GDPR gerekir hem müşteri "kim ne zaman ne yaptı" soracak:

```
audit_log(id, user_id, action, resource_type, resource_id, ip, ua, meta_json, at)
```

Her kritik aksiyon (etiket oluştur, tracking sil, kimlik değiştir, POD indir) buraya düşer. **Sadece admin/auditor okur**, kimse silemez (append-only).

### 1.5 Oturum güvenliği

- Şifre: **argon2id** (bcrypt yerine — 2024 sonrası OWASP önerisi)
- Cookie: `HttpOnly`, `Secure` (prod), `SameSite=Lax`
- Rate limit: `/login` 5/dk/IP (`slowapi`)
- 2FA: v2'ye ertelensin (TOTP → `pyotp`); admin için opsiyonel açılabilir
- Otomatik oturum kapatma: 8 saat idle → çıkış

### 1.6 KVKK / GDPR notu

- Adres defterinde **kişisel veri** var. "Silme talebi" endpoint'i şart.
- POD PDF'leri **imza görüntüsü** içerir → kişisel veri. Erişim yalnız yetkili rollere, log tutulmalı.
- IP + UA log kaydı 6 ay tutulur, sonra otomatik silinir (v2).

---

## 2. GLS Hesapları — Gerçekten Hepsini Bağlayabiliyor muyuz?

### 2.1 GLS'in gerçekliği: **tek şirket değil**

GLS Group, Deutsche Post gibi merkezi bir API şirketi **değil**. Her ülke ayrı sistem, ayrı kimlik, ayrı endpoint:

| Ülke | API türü | Erişilebilirlik | Notlar |
|---|---|---|---|
| **Netherlands (NL)** | Azure APIM REST | ✅ Var (Ocp-Apim-Subscription-Key) | Bizde şu an bu var. Sağlam, dokümante. |
| **Ireland (IE) / ShipIT** | ShipIT REST | ✅ Var (HTTP Basic + `application/glsVersion1+json`) | ShipIT çoklu ülke destekliyor. Bizde var. |
| **Germany (DE)** | ShipIT | ⚠️ Var ama ayrı kontrat | Aynı ShipIT ama farklı kullanıcı/şifre + tarife |
| **Italy (IT)** | ShipIT + eski XML | ⚠️ ShipIT'e geçiş sürüyor | Bazı müşteriler hâlâ eski sistemde |
| **France (FR)** | ShipIT | ⚠️ Var ama farklı sözleşme | |
| **Spain (ES)** | ShipIT | ⚠️ Var | |
| **Belgium (BE) / Luxembourg** | ShipIT | ⚠️ Var | |
| **Austria (AT)** | ShipIT (via DE) | ⚠️ Var | |
| **Poland (PL)** | ADE API (kendi) | ⚠️ Ayrı sistem, ayrı doküman | GLS Poland kendi API'sini yazıyor |
| **UK** | GLS UK ayrı API | ⚠️ Ayrı sistem | Post-Brexit gümrük alanları var |
| **Croatia, Slovenia, Hungary, Czech, Slovakia, Romania** | ShipIT | ⚠️ Var | Standart ShipIT |
| **Denmark, Finland, Sweden, Norway** | ShipIT (partner) | ❌/⚠️ | Bazıları partner kurye — direkt GLS değil |
| **Portugal** | ShipIT | ⚠️ | |

**Kısacası:** Teknik olarak **~20 ülkenin ShipIT'i** aynı REST spec'ini paylaşıyor. Her ülke için sadece:
- Base URL değişir (`https://api.gls-italy.com`, `https://api.gls-spain.com` vs.)
- Username/password (o ülkedeki hesap)
- Bazen `ShipperID` / `ContactID` parametresi

Bizim `ShipITClient` kodu şu anda **bu esneklik için hazır** — sadece config'e ülke listesi eklemek yeter.

### 2.2 Pratikte önündeki engel: **API değil, sözleşme**

Teknik zorluk düşük. Gerçek engel:

1. **Her ülkede ayrı sözleşme.** GLS Türkiye'nin merkezi anlaşması Avrupa hesaplarını kapsamaz. Şirketin her ülkede gönderi hacmi varsa ayrı çerçeve sözleşmesi + ayrı API kimliği çıkarılır.
2. **API kimliği talep süresi:** Ülkeye göre 2-10 iş günü. NL'de otomatik portal, IT'de e-posta.
3. **Test → Prod geçişi:** Her ülkede ayrı UAT ortamı. Onay olmadan prod açılmaz.
4. **Tarife farkı:** Aynı sözleşme kodu farklı ülkelerde farklı fiyat.

### 2.3 Bizim ürün açısından ne demek?

**Yapabildiğimiz:**
- Kod tarafında "yeni ülke ekle" 30 dakikalık iş (config + tarife tablosu).
- Kullanıcı GUI'den kendi hesap bilgisini girer, biz mock'tan gerçeğe geçeriz.
- Tüm ülkelerin tracking numarasını **tek panelde** izleyebiliriz (bizim asıl değerimiz burada).

**Yapamadığımız:**
- Sözleşmesi olmayan ülkeye etiket üretmek → GLS API 401 döner. **Bu iş satış/hukuk tarafında.**
- GLS'in yayınlamadığı endpoint'e erişmek. Örn. gerçek zamanlı sürücü koordinatı yok (bu GLS'in kendisinde de sadece bazı ülkelerde var).
- Türkiye içi GLS yok — GLS Türkiye pazarında değil (bilgi: PTT/Yurtiçi/MNG hakim). "GLS Türkiye" araması yapan müşteriye net söylenmeli.

### 2.4 Konsolide multi-account desteği (v2 tasarımı)

```
gls_accounts tablosu:
  id, tenant_id, country, base_url, auth_type (basic|apim),
  username_enc, password_enc, subscription_key_enc,
  shipper_id, contact_id, is_active, is_test_mode
```

- Kimlikler **fernet ile şifreli** DB'de (key: `.env` GLS_VAULT_KEY).
- `LabelClient.for_country("IT")` → doğru credential + doğru base URL.
- Kullanıcı GUI'den "Hollanda hesabımız", "İrlanda hesabımız", "İtalya-Milano" ekleyebilir.

---

## 3. Bu İş Bir "Ürün" Olur mu? — Fizibilite

### 3.1 Şu an elimizde ne var?

- Web panel (FastAPI + HTMX) — çalışıyor
- Tracking DB, adres defteri, şablon motoru, mock server
- Excel import, canlı takip scheduler, POD indirme, PDF birleştirme
- 15 satır demo veriyle profesyonel görünüm

Bu bir **iç araç** seviyesinde. Ürün olmak için ne eksik?

### 3.2 Ürünleşme için yapılması gerekenler

#### A. Multi-tenancy (çok kiracı)

Şu an DB tek `~/.gls_pod/shipments.db`. Bir SaaS için:

**Opsiyon 1: Şema izolasyonu (önerilen orta yol)**
- PostgreSQL'e geçiş. Her tenant → ayrı schema (`tenant_acme`, `tenant_beta`).
- Kod: her sorguda `SET search_path TO $tenant`.
- Yedek/geri yükleme tenant başına.

**Opsiyon 2: Tek DB + tenant_id kolonu**
- Her tabloya `tenant_id`. Basit ama sızıntı riski (bir bug = tüm veriler görünür).
- Row-level security (PostgreSQL RLS) ile güvenli hale gelir.

**Opsiyon 3: Tam ayrı DB per tenant**
- En güvenli. En pahalı. Enterprise müşteri ister.

**Öneri:** Opsiyon 2 + RLS ile başla. Enterprise talep gelirse Opsiyon 3'e taşınır.

#### B. Deployment mimarisi

| Deployment | Kim ister | Zorluk |
|---|---|---|
| **SaaS** (biz host ederiz) | KOBİ, hızlı başlamak isteyen | Orta — devops + uptime SLA |
| **On-prem** (müşteri sunucusuna kur) | Büyük şirket, veri dışarı çıkmasın | Yüksek — kurulum scripti, güncelleme mekanizması |
| **Hybrid** (uygulama SaaS + DB on-prem) | KVKK/GDPR hassas | Çok yüksek — VPN + custom auth |

**Başlangıç: SaaS.** Docker + docker-compose + PostgreSQL + Traefik. `fly.io` veya kendi Hetzner sunucusu (aylık €20).

#### C. Ölçekleme

Mevcut mimari (SQLite + tek uvicorn worker) ~5 eş zamanlı kullanıcıya kadar iyi. Sonrası için:

- SQLite → **PostgreSQL** (yukarıda). Migration `alembic` ile.
- Scheduler → **APScheduler** yerine **RQ / Celery + Redis**. Çünkü çok tenant = çok job.
- POD PDF depolama → dosya sistemi yerine **S3-compatible** (MinIO on-prem / AWS S3 SaaS). PDF'ler büyür.
- Sessions → cookie yerine **Redis session store** (multi-worker için).
- Log → `logging` + **Loki/Grafana** veya en azından günlük rotating file.

#### D. Bakım/operasyon

- **Yedekleme:** PostgreSQL nightly dump + S3'e upload. Adres defteri + POD arşivi ayrı yedek.
- **İzleme:** Sentry (hata) + Uptime Kuma (uptime) + Prometheus/Grafana (metrik).
- **Güncelleme:** SemVer + changelog. On-prem müşteri için `gls-panel upgrade` CLI.
- **Destek:** Basit issue tracker (GitHub Issues yeterli başlarken).

#### E. Lisanslama + ticari model

Sıfır lisans hedefimiz var ama SaaS'ta biz de bir şey satacağız:

| Model | Fiyat | Kime |
|---|---|---|
| **Free** | 0 | Ayda ≤50 gönderi, 1 kullanıcı, ≤1000 adres, community destek |
| **Pro** | ~€49/ay | ≤2000 gönderi, 5 kullanıcı, ≤50k adres, e-posta destek |
| **Business** | ~€199/ay | Sınırsız gönderi, 20 kullanıcı, çok GLS hesabı, SLA |
| **Enterprise** | Görüşme | On-prem, custom SLA, single sign-on (SSO), audit paketi |

Not: Bu fiyatlar örnek. Pazar araştırması ile netleşir.

### 3.3 Neler **teknik olarak yapılabilir**

✅ Excel'den toplu etiket üretimi (bulk label API)
✅ GLS'in ~20 Avrupa hesabına aynı panelden bağlantı
✅ Canlı takip + otomatik POD arşivi + PDF birleştirme
✅ Şablon motoru (etiket, packing list, proforma, e-posta)
✅ Multi-user + rol/yetki + audit log
✅ Multi-tenant SaaS
✅ SSO (Google/Microsoft) — v2
✅ Webhook'lar: "gönderi teslim edildi → Slack/e-posta bildirim"
✅ Rapor PDF export (haftalık özet, hata raporu)
✅ REST API (bizim panelimizin API'si — müşterinin ERP'si kullansın)
✅ Sentez / BlueCherry / SAP entegrasyonu (ayrı belgede — ODBC/REST/CSV)

### 3.4 Neler **teknik olarak yapılamaz** (ya da çok pahalı)

❌ **Sürücü canlı GPS koordinatı** — GLS bu veriyi API'sinde vermiyor (bazı ülkelerde iç kullanım var, dışa açık değil)
❌ **Teslim zamanı tahmini** — GLS'in kendi ETA'sı var (bunu okuruz), ML ile daha iyisi hacim gerektirir
❌ **Türkiye içi kargo** — GLS Türkiye'de yok
❌ **Otomatik gümrük beyannamesi** — bu ayrı iş, GLS scope dışı
❌ **Fiziksel POD'un dijitalleşmesi** — sürücü tabletle imza almıyorsa (bazı NL sürücüleri hâlâ kağıt) API'de imza gelmez
❌ **Ücretsiz TLS + Web hosting sonsuza dek** — SaaS'ta operasyon maliyeti var, "sıfır lisans" ≠ "sıfır maliyet"
❌ **GLS'in fiyatlandırmasını değiştirmek** — biz sadece tahmin ederiz, gerçek fatura GLS'ten gelir
❌ **Rakip kuryelere (DHL/DPD/UPS/FedEx) tek panelden bağlantı** — mümkün ama her biri ayrı proje, ayrı sözleşme

### 3.5 Yapılabilir ama **iş tarafında blok** olanlar

⚠️ Her GLS ülke sözleşmesi → satış/hukuk gerekir
⚠️ Sentez ODBC → müşteri IT'sinin DB kullanıcısı açması gerek
⚠️ BlueCherry REST → `applications@cgsinc.com` API anahtarı gerek
⚠️ KVKK VERBIS kaydı (Türkiye'de SaaS satacaksak)
⚠️ GDPR Data Processing Agreement şablonu (AB müşterisi için)
⚠️ ISO 27001 / SOC 2 sertifikası (enterprise müşteri ister — 6-12 ay iş)

---

## 4. 12 Aylık Yol Haritası (Öneri)

### v1.0 — "İç Araç Tamam" (mevcut + 1 ay)
- Auth + 4 rol + audit log
- Multi GLS account (config UI)
- Bulk label upload
- Weight/freight raporu
- E-fatura şablonu
- Correction/exception raporu

### v1.5 — "Pilot Müşteri Hazır" (2-3 ay)
- SQLite → PostgreSQL
- Docker compose deploy
- Sentry + basit izleme
- Kullanıcı kılavuzu + video
- GDPR checklist
- 1-2 pilot müşteri (kendi şirketimiz + 1 dış)

### v2.0 — "SaaS MVP" (4-6 ay)
- Multi-tenant (schema izolasyonu)
- Stripe abonelik + faturalandırma
- SSO (Google/Microsoft)
- REST API (webhook + read/write)
- Loki + Grafana
- 5-10 ödeyen müşteri hedefi

### v3.0 — "Enterprise Ready" (7-12 ay)
- On-prem paketi
- Row-level security
- ISO 27001 hazırlığı
- SLA + destek portalı
- SAP/Netsuite/Odoo bağlayıcıları
- Multi-carrier (DPD/DHL eklenmesi)

---

## 5. Karar Bekleyen Sorular

Yol haritasını netleştirmek için:

1. **Hedef pazar:** Sadece kendi şirketimiz mi (iç araç), yoksa Avrupa KOBİ pazarı mı (SaaS)?
2. **Ekip:** Tek geliştirici (sen) mi, yoksa 2-3 kişilik ekip mi olacak? SaaS için minimum 2 kişi lazım (dev + ops+support).
3. **Bütçe:** Aylık sunucu/domain/monitoring toplamı ~€100-200. Ödeyen var mı?
4. **GLS ilişkisi:** GLS Türkiye/NL/IE ile "resmî iş ortağı" sözleşmesi düşünülüyor mu? (GLS bazı ülkelerde partner programı sunuyor.)
5. **Zaman baskısı:** SP26/FA26/SP27 sezonu için ne kadar hızlı ihtiyaç var — tek şirketlik iç araç mı öncelik?

Bu 5 sorunun cevabı yol haritasını **çok** değiştirir. Örneğin cevap "iç araç, tek şirket, hemen" ise v1.0'da dururuz, SaaS mimarisine hiç girmeyiz.

---

## 6. Faz 3 — Gerçekleştirilen İş (İrlanda/Hollanda Adaptasyonu + Auth + Güvenlik)

Bu bölüm **planlanan değil, kodda uygulanmış ve uçtan uca test edilmiş** durumu belgeler.

> **Güncelleme (2026-07-22):** Gerçek GLS NL (printship.gls.nl) ve GLS Group
> ShipIT (gls-group.eu) portallarından alınan ekran görüntüleri/örnek veri
> incelendi ve aşağıdaki uyarlamalar buna göre **koda işlendi**: adres alan
> yapısı (Name/Name2/Name3, Street+HouseNumber+Addition, Address Type,
> Consignee ID, Phone+Mobile ayrı — bkz. §6.1), takip numarası bicimleri
> (IE 11 hane, NL 14 hane — gerçek verilerle doğrulandı), takip olay metinleri
> (mock sunucu artık gerçek portaldaki gibi depo/konum bilgili, zengin
> olay metinleri üretiyor), toplu POD indirme (ZIP, gerçek IE portalındaki
> çoklu-seçim + "Proof of delivery" özelliğiyle uyumlu) ve adlandırılmış
> servis desteği (`FlexDeliveryService` vb.). İncelenen ekran görüntüleri ve
> örnek adres verisi **gerçek müşteri verisi içerdiği için** repoya
> eklenmedi/saklanmadı — sadece yapısal referans olarak kullanıldı.

### 6.1 GLS İrlanda (Eircode) doğrulaması

`gls_api/schemas.py` — Eircode bir tek harfli routing key + 4 karakterlik benzersiz
tanımlayıcıdan oluşur (ör. `D02 XY45`). Kod tarafında:

```python
EIRCODE_RE = re.compile(r"^[A-Za-z]\d[A-Za-z0-9]\s?[A-Za-z0-9]{4}$")
```

- `AddressIn` Pydantic modeli, `country="IE"` olduğunda `postal_code` alanını bu
  regex'e karşı doğrular; uymuyorsa adres defterine **hiç yazılmaz** (422 döner).
- `LabelCreateIn` + `web/routers/labels.py`, etiket oluşturma anında **kanal IE
  seçilmişse** hem gönderen hem alıcı adresinin posta kodunu tekrar doğrular —
  adres defterine eski/toplu-içe-aktarılmış (import) kayıtlar için ikinci bir
  güvenlik katmanı.
- Takip numarası (ShipIT `TrackID`): 8-14 haneli sayısal (`IE_TRACKING_RE`).

### 6.2 GLS Hollanda (NL) doğrulaması

```python
NL_POSTAL_RE = re.compile(r"^\d{4}\s?[A-Za-z]{2}$")
```

- 4 hane + 2 harf (ör. `1234 AB`), boşluk opsiyonel — aynı `AddressIn` /
  `LabelCreateIn` katmanından geçer.
- Takip numarası (NL `ParcelNumber`): 8-20 haneli alfanumerik (`NL_TRACKING_RE`).
- IE/NL dışındaki ülkeler için sistem **kısıtlayıcı değil** (sadece boş/aşırı uzun
  değer reddedilir) — bu iki ülke dışı henüz tam adaptasyon kapsamında değil.

Testler: `tests/test_schemas.py` (25 test — geçerli/geçersiz Eircode, NL posta kodu,
kanal/ağırlık/koli sınırları, takip numarası biçimleri).

### 6.3 Kimlik doğrulama ve yetkilendirme (uygulanan hâli)

- **Roller:** `admin`, `operator` (§1.1'deki 4 rollü öneri v2'ye bırakıldı).
- **Şifre:** PBKDF2-HMAC-SHA256, 200.000 iterasyon, kullanıcı başına salt
  (argon2 yerine — stdlib'de hazır, ek bağımlılık istemiyor).
- **Oturum cookie'si:** `itsdangerous.URLSafeTimedSerializer`, **HMAC-SHA256**
  ile imzalı (kütüphane varsayılanı SHA1'dir, açıkça SHA256'ya yükseltildi),
  20 dakika ömürlü, `HttpOnly` + `SameSite=Lax` + prod'da `Secure`.
- **Refresh token:** `secrets.token_urlsafe(48)` ile üretilir; ham değer **hiçbir
  zaman** veritabanına yazılmaz, sadece SHA256 hash'i saklanır. Her kullanımda
  **tek kullanımlık rotasyon** uygulanır (eskisi iptal edilir, yenisi verilir) —
  çalıntı/tekrar oynatılan (replay) bir refresh token ikinci kullanımda reddedilir.
  30 gün ömürlü.
- **Sessiz yenileme:** `auth/dependencies.py:get_current_user` — session cookie
  süresi dolmuşsa, ayrı bir endpoint çağrısına gerek kalmadan refresh token
  otomatik devreye girer.
- **Yetki:** Rol tamamen imzalı session cookie'sindeki `role` alanından okunur;
  `is_active=0` yapılan kullanıcının **tüm** refresh token'ları anında iptal
  edilir (anlık yetki iptali için DB'ye tek dokunma noktası).
- **Brute-force kilidi:** Kullanıcı adı başına 15 dakikada 5 başarısız denemeden
  sonra 5 dakika kilit (`web/routers/auth.py`). Tek process için yeterli; çoklu
  instance'a geçilirse Redis tabanlı bir rate limiter'a taşınmalı.
- **Profil / şifre değiştirme:** `/auth/profile` sayfası + `/auth/profile/password`
  endpoint'i — mevcut şifre doğrulanır, yeni şifre ≥8 karakter olmalı, başarılı
  değişiklikte **o kullanıcının tüm oturumları** (mevcut tarayıcı dahil) düşürülür.

### 6.4 Güvenlik sıkılaştırması

- **Path traversal (POD indirme):** `web/routers/pod.py` ve
  `web/routers/tracking.py`'deki `{tracking_no}` path parametreleri artık
  FastAPI `Path(..., pattern=r"^[A-Za-z0-9]{1,20}$")` ile kısıtlı — `../`, `/`
  gibi karakterler içeren bir istek dosya sistemine hiç ulaşmadan 422/404 ile
  reddedilir.
- **XSS:** `web/routers/labels.py` — carrier API'den (GLS ShipIT/NL) dönen ham
  hata metni artık `html.escape()` ile temizlendikten sonra HTML'e gömülüyor
  (önceden reflected XSS'e açıktı).
- **SQL injection:** Tüm sorgular zaten `?` parametreli (`sqlite3`, ORM yok);
  dinamik SQL metni üretilen tek yerler sabit whitelist kullanıyor, kullanıcı
  girdisi hiç sorgu metnine karışmıyor.
- **Girdi tip kontrolü:** FastAPI + Pydantic, `Form(...)`/path parametrelerine
  dizi/set gönderilmesini otomatik olarak 422 ile reddediyor — ek bir önlem
  gerekmedi.

### 6.5 Tam Endpoint Listesi

| Router | Metod | Yol | Erişim | Açıklama |
|---|---|---|---|---|
| auth | GET | `/auth/login` | Herkese açık | Giriş sayfası |
| auth | POST | `/auth/login` | Herkese açık | Giriş (brute-force kilitli) |
| auth | GET | `/auth/profile` | Oturum açık (admin/operator) | Profil sayfası |
| auth | POST | `/auth/profile/password` | Oturum açık (admin/operator) | Şifre değiştir (tüm oturumları düşürür) |
| auth | POST | `/auth/logout` | Oturum açık (admin/operator) | Çıkış + refresh token iptali |
| auth | GET | `/auth/users` | **admin** | Kullanıcı listesi |
| auth | POST | `/auth/users/create` | **admin** | Yeni kullanıcı oluştur |
| auth | POST | `/auth/users/{user_id}/toggle` | **admin** | Kullanıcı aktif/pasif |
| dashboard | GET | `/` | Oturum açık (admin/operator) | Ana panel |
| labels | GET | `/labels` | Oturum açık (admin/operator) | Etiket oluşturma sayfası |
| labels | POST | `/labels/create` | Oturum açık (admin/operator) | NL/IE etiket oluştur (Eircode/NL posta kodu doğrulamalı) |
| tracking | GET | `/tracking` | Oturum açık (admin/operator) | Canlı takip sayfası |
| tracking | GET | `/tracking/rows` | Oturum açık (admin/operator) | HTMX kısmi tablo yenileme |
| tracking | GET | `/tracking/detail/{tracking_no}` | Oturum açık (admin/operator) | Kargo detayı (HTMX panel) |
| tracking | POST | `/tracking/refresh` | Oturum açık (admin/operator) | Manuel tarama tetikle |
| tracking | POST | `/tracking/upload` | Oturum açık (admin/operator) | Excel takip listesi içe aktar |
| pod | GET | `/pod` | Oturum açık (admin/operator) | POD arşiv sayfası |
| pod | GET | `/pod/download/{tracking_no}` | Oturum açık (admin/operator) | POD PDF indir (regex kısıtlı tracking_no) |
| addresses | GET | `/addresses` | Oturum açık (admin/operator) | Adres defteri |
| addresses | POST | `/addresses/create` | Oturum açık (admin/operator) | Yeni adres (ülkeye duyarlı posta kodu doğrulamalı) |
| addresses | POST | `/addresses/delete/{address_id}` | Oturum açık (admin/operator) | Adres sil |
| — | GET | `/health` | Herkese açık | Sağlık kontrolü (parça sayısı) |

"Oturum açık (admin/operator)" satırları `web/main.py`'de router seviyesinde
`dependencies=[Depends(get_current_user)]` ile uygulanır — tek tek endpoint'e
eklemeyi unutma riski yok. `/auth/*` router'ı kasıtlı olarak bu genel korumanın
dışında tutulur (aksi halde giriş sayfasına giriş yapmadan erişilemezdi); bunun
yerine login dışındaki auth endpoint'leri kendi `Depends(...)` imzalarını taşır.

---

## 7. Özet — Tek Cümlelik Cevaplar

1. **Kullanıcı girişi/rol/yetki:** 4 rol (admin/operator/viewer/auditor) + argon2 + cookie session + audit log ile 1 haftada eklenir.
2. **GLS'in tüm hesaplarını bağlayabiliyor muyuz:** Teknik olarak ~20 Avrupa ülkesi ShipIT REST'i aynı — kod hazır. **Engel API değil, ülke başına ayrı sözleşme.**
3. **Ürün olabilir mi:** Evet. İç araç → pilot → SaaS → enterprise. 12 ayda MVP SaaS gerçekçi. Ama önce iş tarafındaki 5 sorunun cevabı gerek.
