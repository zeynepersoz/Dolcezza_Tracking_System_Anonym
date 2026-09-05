# GLS'ten Ne İsteyeceğiz? — IT / GLS İrtibat Notu

> Bu not, GLS temsilcisine **tam olarak neyin isteneceğini** söyler. GLS tek bir "API"
> vermez; birbirinden bağımsız **üç** kanal vardır ve hangisinin verildiği müşteriden
> müşteriye değişir. Uygulama üçünü de destekler — hangisi gelirse `.env`'e yazılır,
> `python3 cli.py doctor` hangisinin canlı olduğunu satır satır söyler.

Kısa özet — GLS'e sorulacak tek cümle:

> "Bize **ShipIT REST**, **Track & Trace (SOAP web servisi)** ve **GLS Netherlands API**
> kanallarından hangileri açıldı? Her biri için aşağıdaki bilgileri rica ediyoruz."

> ⚠️ **Geliştirici portalından (dev-portal.gls-group.net) aldığınız "API Key + Secret"
> tek başına yeterli DEĞİLDİR** — o anahtarlar yalnızca portal uygulamasını tanımlar.
> Asıl bağlantı bilgileri (Contact ID, kullanıcı adı/şifre, uç nokta adresleri)
> GLS müşteri temsilcisinden ayrıca e-posta ile istenir. Ayrıntı: **D bölümü**.

---

## A) GLS ShipIT REST — etiket + takip + POD (PDF)

En kapsamlı kanal. Bu verilirse ayrıca bir şey gerekmez.

**İstenecekler:**

| # | Bilgi | Açıklama |
|---|---|---|
| 1 | **Taban URL (test)** | Müşteriye özeldir, örn. `https://<host>.gls-group.eu:8443/backend` |
| 2 | **Taban URL (prod)** | Test'ten farklıdır |
| 3 | **Kullanıcı adı / şifre** | HTTP Basic auth |
| 4 | **ContactID** | `"<müşteri no> <depo kodu>"` biçimi, örn. `276a0b1c IES`. **Etiket oluşturmak için zorunlu.** |
| 5 | **Sunucu IP'mizin whitelist'e eklenmesi** | GLS çoğu kurulumda IP kısıtlar |
| 6 | **Açılan uç noktalar** | `parceldetails`, `parcelpod`, `parcels`, `endofday`, `allowedservices` |

**Sorulacak kritik soru:**
> "Gün sonu raporu (`endofday`) otomatik mi çalışıyor, yoksa biz mi tetiklemeliyiz?"

Bir parça `endofday`'den geçmeden **takipte görünmez** — bu, "API çalışmıyor" sanılan
en yaygın nedendir.

**`.env` karşılığı:**
```
SHIPIT_BASE_URL=...
SHIPIT_USERNAME=...
SHIPIT_PASSWORD=...
SHIPIT_CONTACT_ID=...
```

---

## B) GLS Track & Trace SOAP — sadece takip + POD

Etiket üretmez. Elimizde zaten takip numarası varsa (bizim durumumuz: Excel'den gelen
11 haneli IE parçaları) tek başına yeterlidir.

**İstenecekler:**

| # | Bilgi | Açıklama |
|---|---|---|
| 1 | **Uni-Portal kullanıcı adı / şifre** | `gls-group.eu` portalına giriş yapan hesap |
| 2 | **Hesabın web servisi yetkisi** | Portal girişi olması yeterli değil; ayrıca *web service* yetkisi açılmalı |
| 3 | **POD görüntüleme izni — ülke bazında** | İmzayı hangi ülkelerdeki teslimatlar için görebileceğimiz ayrı yetkilendirilir. **Teslimat yaptığımız tüm ülkeler için isteyin:** FR, DE, ES, IT, NL, BE, IE, ... |

Uç nokta zaten sabittir, GLS'ten istenmesine gerek yok:
`https://www.gls-group.eu/276-I-PORTAL-WEBSERVICE/services/Tracking`

**`.env` karşılığı:**
```
TT_USERNAME=...
TT_PASSWORD=...
```

Doğrulama: `cli.py doctor` sahte bir referansla sorgu atar.
- `ExitCode 502` → kullanıcı adı/şifre yanlış
- `ExitCode 998` (veri yok) → **kimlik doğru, bağlantı iyi**

---

## C) GLS Netherlands REST — sadece etiket

**API anahtarı YOKTUR.** Kimlik doğrulama JSON gövdesinde `username`/`password` ile
yapılır. (Eski dokümanlarda geçen `Ocp-Apim-Subscription-Key` artık kullanılmıyor.)

**İstenecekler:**

| # | Bilgi | Açıklama |
|---|---|---|
| 1 | **MyGLS test hesabı** | kullanıcı adı + şifre |
| 2 | **MyGLS prod hesabı** | kullanıcı adı + şifre |
| 3 | **Müşteri numarası (`customerNo`)** | Boş bırakılırsa `ValidateLogin`'den otomatik çözülür, yine de teyit edin |

⚠️ **Şifre en fazla 20 karakter olabilir.** Daha uzunu API tarafından sessizce
reddedilir. Şifreyi belirlerken bunu söyleyin.

Taban URL'ler sabittir:
- Test: `https://api.gls.nl/test/v1/api`
- Prod: `https://api.gls.nl/v1/api`

**`.env` karşılığı:**
```
NL_BASE_URL=https://api.gls.nl/test/v1/api
NL_USERNAME=...
NL_PASSWORD=...
NL_CUSTOMER_NO=
```

### C2) NL webhook kaydı — **ayrıca istenmesi gereken şey**

GLS Netherlands'ta **parça sorgulama ve POD indirme uç noktası yoktur.** Takip
olayları ve imza (PNG) GLS tarafından bize **itilir**. Bu yüzden GLS'e kendi
URL'lerimizi kaydettirmemiz gerekir — **bunun API'si yoktur, elle yapılır.**

**GLS'e verilecekler:**

```
Takip olayları : POST https://<alan-adimiz>/webhooks/gls-nl/parcel
Teslimat kanıtı: POST https://<alan-adimiz>/webhooks/gls-nl/pod
Kimlik         : Authorization: Bearer <bizim ürettiğimiz token>
```

**GLS'e sorulacak:**
> "Webhook doğrulaması için Bearer token mı kullanıyorsunuz, yoksa `Digest`
> başlığında RSA-SHA512 imza mı? İmza kullanıyorsanız public key'i (PEM) rica ederiz."

Buna göre `.env`:
```
NL_WEBHOOK_VERIFY=token            # veya digest
NL_WEBHOOK_TOKEN=<ürettiğimiz gizli token>
NL_WEBHOOK_PUBLIC_KEY_PATH=        # digest ise GLS'in verdiği PEM dosyasının yolu
```

⚠️ **Önemli:** GLS, yavaş yanıt veren webhook ucunu yeniden dener; **4 gün boyunca
başarısız kalırsa aboneliği kalıcı olarak durdurur.** Uygulama bu yüzden hemen `200`
döner ve işlemeyi arka plana atar — sunucunun sürekli ayakta olması gerekir.

---

## D) GLS Grup Geliştirici Portalı (dev-portal.gls-group.net) — **elimizde bu var**

Portalda bir "App" oluşturulup **Authentication API v2** ve **ShipIT-Farm API v1**
için erişim istendiğinde şu değerler üretilir: *App ID, App Key, **API Key**, **Secret***.

Elimizdeki **API Key + Secret** `.env`'e yazılır (`GLSG_CLIENT_ID` / `GLSG_CLIENT_SECRET`).

**Durum (2026-09-02, canlı doğrulandı — `api.gls-group.net`, 24 teslim edilmiş IE parçası):**

- ✅ **Track & Trace v1 kodda uygulandı, canlıda çalışıyor, IE takibine bağlandı.**
  OAuth 2.0 ile token → `GET /track-and-trace-v1/tracking/simple/trackids/{id1,..}`
  (≤10 numara) HTTP 200; yanıt parçanın **tam olay geçmişini** de veriyor
  (`unitno`, `status`, `statusDateTime`, `events[]` yeni→eski). Tek takip ucu
  budur — `.../trackids/` ve `.../references/` prod'da 404 "No static resource".
  İstemci `gls_api/tt_v1_client.py`; `Tracker.tick()` artık IE parçalarını bu
  uçtan 10'arlı toplu tarıyor (`tracking/scheduler.py:_glsg_results`).
  `cli.py doctor` satırı: *"GLS Track & Trace v1"*. Sınır: günlük 500 istek
  (kota artışı için onboarding formu GLS'e gönderildi).
- ⏳ **ShipIT-Farm v1 aynı token'la HTTP 401 "Authorization failed" dönüyor.**
  Token geçerli (JWT `appName=GLS SYSTEM`, T&T v1 kabul ediyor); sorun bu App ID'ye
  **ShipIT-Farm ürün entitlement'ının** açılmamış olması. `/rs/parcelshop/country/IE`
  gibi hesaba özgü olmayan uçlar bile 401. GLS'e açması için talep gönderildi.
  Sandbox'ta (`api-sandbox.gls-group.net`) ShipIT-Farm **açık** ve 200 dönüyor.
- **POD (`/rs/tracking/parcelpod`) yalnızca ShipIT-Farm'da var** — o yüzden entitlement
  gelene kadar POD arşivi klasik kanallardan (A/B/C) yürüyor.

Bu, A bölümündeki klasik ShipIT'ten **farklı** bir yoldur:

| | Klasik ShipIT (A) | Portal / ShipIT-Farm (D) |
|---|---|---|
| Kimlik | HTTP Basic (kullanıcı+şifre) | Önce **token al** (OAuth 2.0), sonra `Bearer` ile çağır |
| Adres | Müşteriye özel `<host>.gls-group.eu:8443/backend` | Ortak geçit: `https://api.gls-group.net` |
| Anahtar | yok | API Key + Secret (portal App'i) |

**GLS müşteri temsilcisinden e-posta ile istenecekler** (bunlar portalda **görünmez**,
portalda oluşturulamaz):

| # | Bilgi | Neden |
|---|---|---|
| 1 | **Contact ID** | Etiket oluşturmak için zorunlu |
| 2 | **Login / kullanıcı adı** | Token almak için |
| 3 | **Şifre** | Token almak için |
| 4 | **Authentication API v2 token uç noktasının tam adresi** | Dokümanda yayınlanmıyor, temsilciyle geliyor |
| 5 | **ShipIT-Farm API v1 uç nokta listesi** | Takip / POD / etiket yolları |
| 6 | **Test + prod adresleri ayrı ayrı** | İkisi farklı |
| 7 | **API dokümanı (PDF/Swagger)** | Token'ın hangi başlıkla gönderileceği buradan doğrulanır |

**GLS'e yazılacak cümle:**

> "dev-portal.gls-group.net üzerinden App oluşturduk ve Authentication API v2 +
> ShipIT-Farm API v1 için API Key/Secret üretildi. Entegrasyonu tamamlayabilmemiz için
> Contact ID, login kullanıcı adı/şifresi, Authentication API v2 token uç noktasının
> tam adresi ve ShipIT-Farm API v1 uç nokta dokümanını (test + prod) rica ediyoruz."

⚠️ Pickware/PlentyONE gibi hazır entegrasyonların notu: **yeni API'ye geçildiğinde eski
ShipIT kimlik bilgileri çalışmaz.** Yani D verildiyse A muhtemelen kapanacaktır — GLS'e
"eski ShipIT erişimimiz devam edecek mi?" diye sorun.

**`.env` karşılığı:**
```
GLSG_TOKEN_URL=https://api.gls-group.net/oauth2/v2/token   # canlı (sabit)
GLSG_BASE_URL=https://api.gls-group.net                    # canlı (sabit)
GLSG_CLIENT_ID=...        # portaldan App API Key (elimizde var)
GLSG_CLIENT_SECRET=...    # portaldan App Secret (elimizde var)
GLSG_SCOPE=all            # boş bırakılırsa scope hiç gönderilmez
```

> Track & Trace v1 için **Contact ID / login / şifre GEREKMEZ** — App Key/Secret yeterli.
> Contact ID ve login yalnızca ShipIT-Farm'da (etiket + POD) gerekecek; entitlement
> geldiğinde ayrıca istenir.

---

## Ortak talepler (hangi kanal verilirse verilsin)

1. **Test ortamı önce.** Prod'a geçmeden önce test hesabıyla uçtan uca doğrulayacağız.
2. **Sunucu IP'sinin whitelist'e eklenmesi** (ShipIT ve gerekirse T&T için).
3. **Günlük çağrı limiti / rate limit** var mı? Varsa değeri.
4. **Teknik irtibat kişisi** — sorun çıktığında yazılacak e-posta/telefon.

---

## Bilgiler geldiğinde bizim yapacağımız

```bash
# 1) .env'e sadece verilen kanalı doldur
cp .env.example .env && $EDITOR .env
#    GLS_MODE=test

# 2) Hangisi canlı, satır satır gör
python3 cli.py doctor

# 3) Sorun yoksa paneli aç
uvicorn web.main:app --host 0.0.0.0 --port 8000
```

`cli.py doctor` her sağlayıcıya **yan etkisiz** bir çağrı yapar (parça oluşturmaz,
etiket basmaz):

| Sağlayıcı | Test çağrısı |
|---|---|
| ShipIT | `POST /allowedservices` |
| Track & Trace | sahte referansla `GetTuDetail` |
| GLS Netherlands | `Authentication/ValidateLogin` |
