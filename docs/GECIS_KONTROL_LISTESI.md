# GLS Kimlikleri Geldiğinde Ne Yapacağım — Geçiş Kontrol Listesi

> Kapsam yalnızca **GLS** (ShipIT + GLS NL). Sentez/BlueCherry bu projenin
> kapsamı dışında (bkz. proje kararı — GLS dışında entegrasyon yapılmıyor).

Kod tarafı üç modda da (`mock` / `test` / `prod`) **birebir aynı** çalışır;
değişen tek şey `.env` içeriğidir. Bu listedeki adımlar sırayla, atlamadan
uygulanır.

---

## Faz A — `mock → test`

Gerekli: GLS'ten (İrlanda/Grup hesabı + NL portalı) **test ortamı** kimlikleri.

- [ ] **1. Kimlikleri iste** — `docs/IT_TALEP_NOTU.md`'yi GLS irtibat kişisine
      ilet. GLS tek bir API vermez; **hangisinin verildiği belirsizdir**, o yüzden
      not üç kanalı da ayrı ayrı sorar:
      ShipIT (etiket+takip+POD) · Track&Trace (takip+POD) · GLS NL (sadece etiket).
      **Size hangisi verilirse yalnızca onu doldurursunuz** — üçü birden gerekmez.
- [ ] **2. GLS'ten en az 1-2 gerçek test parça/takip numarası iste.** Mock
      sunucudaki sabit örnekler (`21569761233` vb.) yalnızca `mock` modda
      geçerlidir — test ortamında GLS'in kendi verdiği numaralar kullanılır.
- [ ] **3. `.env` dosyasını doldur** (`cp .env.example .env` henüz yapılmadıysa)
      — **size verilen kanalı**:
      ```
      GLS_MODE=test

      # A) ShipIT verildiyse
      SHIPIT_BASE_URL=https://<host>.gls-group.eu:8443/backend
      SHIPIT_USERNAME=...
      SHIPIT_PASSWORD=...
      SHIPIT_CONTACT_ID=<müşteri no> <depo kodu>

      # B) Track & Trace verildiyse (gls-group.eu Uni-Portal kullanıcısı)
      TT_USERNAME=...
      TT_PASSWORD=...

      # C) GLS Netherlands verildiyse (API anahtarı YOK)
      NL_BASE_URL=https://api.gls.nl/test/v1/api
      NL_USERNAME=...
      NL_PASSWORD=...          # en fazla 20 karakter
      NL_WEBHOOK_VERIFY=token
      NL_WEBHOOK_TOKEN=<ürettiğiniz gizli token>
      ```
- [ ] **4. Bağlantı testi:**
      ```bash
      python3 cli.py doctor
      ```
      Her yapılandırılmış sağlayıcıya **yan etkisiz gerçek bir çağrı** yapar
      (parça oluşturmaz, etiket basmaz) ve `✅ canlı` / `❌ hata sebebi` /
      `⚪ yapılandırılmamış` olarak listeler. En az bir `✅` çıkana kadar düzeltin.
- [ ] **5. Birim testleri çalıştır** (bunlar `.env`'den bağımsız, kendi mock
      sunucusuna karşı çalışır — GLS_MODE test'e geçse de kırılmamalı):
      ```bash
      pytest -q
      ```
- [ ] **6. Gerçek test ortamına karşı manuel doğrulama** — adım 2'de alınan
      gerçek takip numarasıyla:
      ```bash
      python3 cli.py demo   # gerektiğinde track_id'yi kodda/argümanla güncelle
      ```
      veya doğrudan Python ile `ShipITClient().parcel_details("<gerçek-takip-no>")`.
      Beklenen: 400/401 değil, gerçek takip geçmişi dönmeli.
- [ ] **7. Excel ile uçtan uca dene** (birkaç satırla, `--limit`):
      ```bash
      python3 cli.py run --excel "SP26 TRACKING LIST.xlsx" --limit 3
      ```

**Test ortamı yeşil olmadan Faz B'ye geçilmez.**

---

## Faz B — `test → prod`

Gerekli: **IT/yönetici onayı** + GLS'ten prod kimlikleri.

- [ ] **1. Yazılı onay al** — prod kimlikleriyle gerçek sevkiyat verisine
      erişileceği için IT/sorumlu yöneticiden onay.
- [ ] **2. `.env`'i prod değerleriyle güncelle:**
      ```
      GLS_MODE=prod
      SHIPIT_BASE_URL=<prod URL>
      SHIPIT_USERNAME=...        # prod kullanıcı, test'ten FARKLI olabilir
      SHIPIT_PASSWORD=...
      # ve/veya
      TT_USERNAME=...  TT_PASSWORD=...
      # ve/veya
      NL_BASE_URL=https://api.gls.nl/v1/api    # test yolundaki /test/ kalkar
      NL_USERNAME=...  NL_PASSWORD=...
      ```
      **GLS NL webhook URL'ini prod alan adınıza göre GLS'e yeniden kaydettirin**
      — test ve prod abonelikleri ayrıdır ve kayıt elle yapılır, API'si yoktur.
- [ ] **3. Auth güvenliğini sabitle** (prod'da bu iki adım atlanmaz):
      ```bash
      AUTH_SECRET_KEY=$(openssl rand -hex 32)   # .env'e sabit değer olarak yaz
      ```
      `.env` içinde ayrıca `AUTH_COOKIE_SECURE=true` (HTTPS arkasındaysa).
- [ ] **4. Bağlantı kontrolü (prod modda ekstra kontrolleri de kapsar):**
      ```bash
      python3 cli.py doctor
      ```
      Prod modda ayrıca `AUTH_SECRET_KEY` sabitlenmiş mi ve
      `AUTH_COOKIE_SECURE=true` mu diye de bakar.
- [ ] **5. Web uygulamasını HTTPS arkasında başlat** (reverse proxy — nginx/
      Caddy/Traefik). `AUTH_COOKIE_SECURE=true` iken HTTP üzerinden
      cookie gönderilmez, bu yüzden HTTPS şart.
- [ ] **6. İlk gerçek sevkiyatı küçük ölçekte dene** (`--limit 1-3`), çıktı
      klasörünü ve PDF içeriğini elle doğrula.
- [ ] **7. Cron'a bağla** (Ubuntu ofis sunucusu — README'deki örnek):
      ```
      0 7 * * * cd /opt/gls-pod-system && ./venv/bin/python cli.py run --excel /data/liste.xlsx --zip >> /var/log/gls-pod.log 2>&1
      ```

---

## Geri dönüş (rollback)

Herhangi bir adımda sorun çıkarsa `.env`'de `GLS_MODE=mock` yapıp uygulamayı
yeniden başlatmak yeterli — kod değişmez, mock sunucuya (`uvicorn
gls_api.mock_server:app --port 8788`) geri döner. Prod kimlikleri `.env`'de
kalabilir, sadece `GLS_MODE` okunan davranışı belirler.

## Notlar

- `cli.py doctor` her zaman güncel durumu gösterir — geçiş sırasında hangi
  fazda olduğunuzu bundan takip edin.
- **ShipIT kullanıyorsanız:** bir parça `endofday` (gün sonu raporu)
  çalıştırılmadan takip sisteminde **görünmez**. "API çalışmıyor" sanılan
  hataların en yaygın sebebi budur.
- **GLS NL kullanıyorsanız:** takip ve POD **çekilmez, itilir**. Webhook ucunuz
  (`/webhooks/gls-nl/parcel`, `/webhooks/gls-nl/pod`) internetten erişilebilir
  olmalı; GLS 4 gün boyunca ulaşamazsa aboneliği **kalıcı olarak durdurur**.
- `AUTH_ADMIN_PASSWORD` boş bırakılırsa ilk açılışta rastgele üretilip loglara
  yazılır; prod'a geçmeden önce `/auth/profile` üzerinden değiştirin.
- Bu belge yalnızca GLS kimlik geçişini kapsar. Sentez/BlueCherry ile ilgili
  hiçbir adım burada yok ve eklenmeyecek.
