# Nasıl Çalıştırılır + Günlük Kullanım (Docker)

> Kapsam: GLS web paneli (FastAPI). GUI (masaüstü Tk uygulaması) Docker
> imajına dahil değildir — ekran gerektirdiği için ayrı, doğrudan makinede
> (`python -m gui.app`) çalıştırılır (bkz. README).

## 1. İlk Kurulum

```bash
cp .env.example .env
nano .env              # GLS_MODE=mock ile başlayın, kimlik gelince test/prod'a geçin
mkdir -p output         # ÖNEMLİ — bkz. not aşağıda
docker compose build
```

> **Neden `mkdir -p output` şart?** Bu klasör önceden yoksa Docker onu ilk
> `up`'ta kendisi (root olarak) oluşturur ve konteyner içindeki `appuser`
> (root değil) içine yazamaz — `PermissionError: [Errno 13] Permission
> denied: '/data/output/...'` ile karşılaşırsınız. Klasörü kendi kullanıcınızla
> önceden oluşturmak bunu önler. Gerçek `docker compose build` + `up` ile
> test edilip doğrulanmış bir bulgudur.

## 2. Çalıştırma

**mock modda (kimlik gerekmez, hemen deneyebilirsiniz):**
```bash
# .env içinde:
#   GLS_MODE=mock
#   SHIPIT_BASE_URL=http://mock:8788/shipit   <- container-to-container, 127.0.0.1 DEĞİL
#   NL_BASE_URL=http://mock:8788/nl
docker compose --profile dev up -d
```
`mock` servisi yalnızca `--profile dev` ile ayağa kalkar; prod'da bu servise
hiç gerek yok. Panel: http://localhost:8000

**test/prod modda (gerçek GLS kimlikleri `.env`'de):**
```bash
docker compose up -d web
```
`mock` servisi başlatılmaz (profile dışı kaldığı için varsayılan `up`
komutuna dahil olmaz).

## 3. İlk Admin Kullanıcısı

Uygulama ilk açılışta kullanıcı tablosu boşsa otomatik bir admin oluşturur;
şifre `.env`'de `AUTH_ADMIN_PASSWORD` boşsa rastgele üretilip container
loglarına yazılır:
```bash
docker compose logs web | grep "Ilk admin"
```
Bilinen bir şifreyle deterministik kurulum isterseniz (loglara bakmadan):
```bash
docker compose exec web python3 cli.py seed-admin --username admin --password "GucluBirSifre!23"
```
Kullanıcı zaten varsa şifresini sıfırlamak için `--force` ekleyin (tüm
oturumlar düşer).

## 4. Hazırlık Kontrolü

`.env`'i her değiştirdiğinizde (özellikle mock→test veya test→prod
geçişinde):
```bash
docker compose exec web python3 cli.py doctor
```
Yapılandırılmış her GLS sağlayıcısına **yan etkisiz gerçek bir çağrı** yapar
(parça oluşturmaz, etiket basmaz) ve `✅ canlı` / `❌ hata sebebi` /
`⚪ yapılandırılmamış` olarak listeler.

Adım adım geçiş prosedürü: `docs/GECIS_KONTROL_LISTESI.md`.

## 5. Günlük Kullanım

| İş | Komut |
|---|---|
| Servisi başlat | `docker compose up -d web` |
| Logları izle | `docker compose logs -f web` |
| Servisi durdur | `docker compose down` |
| Kod güncellendikten sonra yeniden derle | `docker compose build web && docker compose up -d web` |
| Konteyner içinde kabuk aç | `docker compose exec web bash` |
| Sağlık kontrolü | `curl http://localhost:8000/health` |

## 6. Kalıcı Veri

- **Veritabanları** (`shipments.db`, `auth.db`, `addressbook.db`) — `gls_data`
  adlı Docker volume'ünde (`docker-compose.yml`), konteyner silinse de kalır.
- **POD/etiket PDF çıktıları** — proje kökündeki `./output` klasörüne
  bağlanır (`OUTPUT_DIR=/data/output`), doğrudan host'tan erişilebilir.
- Yedek almak için: `docker compose down` sonrası `gls_data` volume'ünü ve
  `./output` klasörünü kopyalayın; ya da `docker run --rm -v
  gls-pod-system_gls_data:/data -v $(pwd):/backup alpine tar czf
  /backup/gls_data_backup.tar.gz -C /data .`

## 7. Excel Pipeline (CLI, cron ile)

Web panelin dışında, toplu Excel→POD indirme akışı da konteyner içinde
çalıştırılabilir:
```bash
docker compose exec web python3 cli.py run --excel /data/output/liste.xlsx --zip
```
Ubuntu ofis sunucusunda cron ile otomatikleştirmek isterseniz (host'ta,
Docker olmadan) README'deki "Ubuntu'ya Taşıma" bölümüne bakın — iki kurulum
şekli (Docker / doğrudan venv) birbirini dışlamaz, ihtiyaca göre seçilir.
