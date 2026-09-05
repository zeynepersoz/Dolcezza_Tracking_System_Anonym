# IT Sunum Notu — GLS Teslimat Kanıtı Otomasyonu (Sürdürülebilir Çözüm Önerisi)

**Hazırlayan:** Zeynep Ersöz
**Amaç:** Teslimat kanıtı (POD) arşivleme sürecini manuel indirme yerine GLS'in
resmî API'leriyle, yasal ve sürdürülebilir şekilde otomatikleştirmek.

---

## 1. Mevcut Durum ve Maliyet

SP26 sevkiyatında ~1.000+ GLS parçasının teslimat kanıtı tek tek portaldan elle
indirilip yeniden adlandırılarak klasörleniyor. Parça başına ~1-2 dakika →
sevkiyat başına **2-3 iş günü** tekrarlanan manuel iş. Her yeni sevkiyatta (SP27,
SP28...) aynı yük tekrarlanacak.

## 2. Önerilen Çözüm

GLS'in **kurumsal müşterilere sunduğu resmî API'leri** kullanan, üç ortamlı
(mock/test/prod) bir Python sistemi. Kod hazır ve mock ortamda testleri geçiyor;
tek eksik GLS'ten alınacak erişim bilgileri.

### Kullanılacak resmî servisler

**A) GLS ShipIT Tracking API** (grup çapında; İrlanda hesabımız — DUBLD24 — bu kanaldan):
- `POST /rs/tracking/parceldetails` → takip geçmişi, teslim tarihi, imza
- `POST /rs/tracking/parcelpod` → Teslimat Kanıtı PDF'i (Base64)
- Kimlik: HTTP Basic; kullanıcı **GLS Field IT** tarafından tahsis edilir.
- Dokümantasyonda açıkça "ERP entegrasyonu ve POD belgesi alma" kullanım
  senaryosu olarak listeleniyor — yani tam bizim ihtiyacımız için tasarlanmış.

**B) GLS Netherlands API** (api-portal.gls.nl — Azure API Management):
- `POST /api/parcel/v1/details` → parça durum/detay sorgulama (toplu)
- Shipping API (v1.0): etiket oluşturma/onaylama/silme, ShopReturn, Pickup —
  ileride iade ve etiket süreçleri de otomatize edilebilir.
- Portal **self-servis**: hesap açılır, ürüne abone olunur, API anahtarı alınır.
  Çağrılar için ayrıca MyGLS kullanıcı bilgisi gerekir.
- Portal resmi akışı: geliştirme test ortamında yapılır, bitince üretime geçilir.

**C) GLS Grup Geliştirici Portalı** (dev-portal.gls-group.net):
- Grup seviyesindeki yeni nesil API kataloğu; hesapla giriş yapıp mevcut
  API'ler görülebilir. Hangi ülke API'lerinin burada toplandığını hesap
  açtıktan sonra netleştireceğiz.

## 3. IT'den / GLS'ten Talep Listesi

| # | Talep | Muhatap |
|---|---|---|
| 1 | ShipIT Tracking servisi için kullanıcı + endpoint URL (test ve prod) | GLS İrlanda hesap yöneticimiz / GLS Field IT |
| 2 | api-portal.gls.nl'de şirket hesabı + Track&Trace ürün aboneliği | Self-servis (IT e-postasıyla) |
| 3 | MyGLS API kullanıcı bilgilerinin teyidi (NL) | GLS NL temsilcisi |
| 4 | dev-portal.gls-group.net hesabı | Self-servis |

## 4. Neden Yasal ve Güvenli?

- **Resmî kanal:** Web kazıma değil; GLS'in müşterilere entegrasyon için
  yayınladığı, sözleşmeli API'ler.
- **Salt okuma:** Sistem hiçbir veri oluşturmaz/değiştirmez; yalnızca şirketin
  kendi gönderilerinin belgelerini çeker.
- **Hız sınırı:** İstekler arası bekleme yapılandırılabilir (varsayılan 0,5 sn) —
  sunucu yükü ihmal edilebilir.
- **Kimlik güvenliği:** Bilgiler `.env` dosyasında, kod deposu dışında; loglara
  yazılmaz.
- **Denetlenebilirlik:** Her koşu log üretir; hatalı parçalar rapor dosyasına düşer.

## 5. Yol Haritası

1. **Hafta 0 (tamamlandı):** Mock API + istemciler + pipeline + birim testler.
2. **Hafta 1:** GLS'ten test kimlikleri → `GLS_MODE=test` ile gerçek test
   ortamında doğrulama.
3. **Hafta 2:** IT onayıyla `prod` geçişi; Ubuntu sunucuya kurulum + cron.
4. **Sonrası (yerel — bizim kontrolümüzde):** Sentez Live'dan salt-okuma
   ODBC ile adres senkronu. Ayrıntılar için `docs/SENTEZ_BLUECHERRY_ENTEGRASYON.md`.
5. **Daha sonra (koordinasyon gerekli):** BlueCherry Kanada ekibinin
   onayı gelirse API entegrasyonu değerlendirilir. O gün gelene kadar
   BlueCherry tarafı manüel CSV içe aktarma ile çalışmaya devam eder —
   yani hiçbir şey durmaz.

## 6. Kazanım

Sevkiyat başına 2-3 iş günü manuel iş → **~15 dakikalık otomatik koşu**.
Adlandırma/klasörleme hatası sıfırlanır; süreç kişiden bağımsız hale gelir.
