# Proje Yol Haritası — GLS Öncelikli, ERP Entegrasyonu Kademeli

**Hazırlayan:** Zeynep Ersöz
**Amaç:** Bu belge, GLS Teslimat Merkezi projesinin **hangi işi ne zaman
teslim edeceğini** ve hangilerinin **başka ekiplerle koordinasyon
gerektirdiğini** netleştirir.

> **Önemli:** Bu proje **GLS teslimat süreçlerini** çözer. ERP tarafı
> (Sentez, BlueCherry) ancak GLS omurgası oturduktan sonra, ve o
> sistemlerin **sahibi olan ekiplerin onayıyla** eklenir. BlueCherry'yi
> Kanada tarafı yönetiyor; onun API katmanına biz tek başımıza dokunmayız.

---

## Faz 1 — GLS Teslimat Merkezi (BUGÜN — TESLİM EDİLDİ)

### Kapsam
- POD (teslimat kanıtı) otomasyonu — mevcut CLI + yeni masaüstü GUI.
- GLS Etiket oluşturma (NL + ShipIT), Track & Trace, Confirm/Delete.
- Yerel adres defteri (SQLite) + CSV/XLSX içe aktarma.
- Şablon motoru: packing list, proforma, shipping label DOCX + e-posta.
- Üç modlu ayar (mock/test/prod), `.env` tabanlı.
- 22 otomatik test — mock modda uçtan uca doğrulandı.

### IT'den Gerekenler
- **Sadece GLS erişim bilgileri** (`docs/IT_SUNUM_NOTU.md`).
- Ubuntu ofis bilgisayarına Python + Tk kurulumu (5 satır).

### Değer
Sevkiyat başına 2-3 iş günlük manuel POD toplamayı **~15 dakikaya**
düşürür. Etiket ve şablon üretimini kişiden bağımsızlaştırır.

**Bu faz kimseye bağlı değil; yalnız GLS'e ait.**

---

## Faz 2 — Sentez Live'dan Adres Çekme (YEREL — KISA VADE)

### Neden Sentez önce, BlueCherry sonra?
- **Sentez sunucusu şirket içinde** — IT ile aynı ofisteyiz, karar süreci hızlı.
- Sentez okuması **salt-okuma**, ERP tarafında hiçbir yazma yok → risk minimal.
- BlueCherry ile ilgilenen ekip Kanada'da; farklı zaman dilimi + farklı
  sözleşme süreçleri → aynı hızda hareket edemez.

### Kapsam
Adres Defteri sekmesine tek bir düğme: **⚡ Sentez'den Çek**.
- Sentez SQL Server'a **read-only ODBC** bağlantısı.
- Cari (müşteri) tablosundan yeni/güncellenmiş kayıtları senkron çeker.
- Manüel CSV içe aktarma her zaman yedek olarak kalır.

### IT'den İstenecekler (yalnızca üç madde)

| # | Talep |
|---|---|
| 1 | `gls_pod_reader` isimli **read-only** SQL kullanıcısı (yalnızca SELECT yetkisi) |
| 2 | Ofis bilgisayarından Sentez SQL sunucusuna 1433/TCP erişimi (VPN veya IP whitelist) |
| 3 | Cari Hesap tablosunun gerçek adı ve önemli kolonların Türkçe adları |

### Teknik Not
- Ubuntu tarafı: `msodbcsql18` + `unixodbc-dev` + `pip install pyodbc`.
- Kod kancası hazır: `address_book/importers.py:155` — `import_sentez_odbc`
  bugün `NotImplementedError` fırlatıyor; kimlik geldiğinde 40 satır kodla
  aktifleşir.
- Örnek sorgu taslağı:
  ```sql
  SELECT CariUnvan, VergiNumarasi, Adres1, Sehir, Ulke, Telefon, Email
  FROM dbo.CariHesap WHERE Aktif = 1;
  ```
  (Gerçek kolon adları IT tarafından teyit edilir.)

**Süre:** IT talep tamamlandıktan ~2 gün.

---

## Faz 3 — BlueCherry (KOORDİNASYON GEREKLİ — UZUN VADE)

### Neden ayrı ve sonra?
- BlueCherry sistemini **Kanada ekibi (CGS Inc.)** yönetiyor.
- API Suite anahtarı **hesap yöneticisi üzerinden ve süreçle** alınır;
  bizim tek taraflı hareket etme yetkimiz yok.
- Kanada tarafının kendi entegrasyon önceliği olabilir; **onlarla
  çakışmadan** ilerlemek şart.

### Ön Koşul
Bu adıma başlamadan önce **açıkça** şunlar sağlanmalı:
- [ ] Kanada tarafındaki BlueCherry sahibiyle görüşme yapılmış ve
  entegrasyona onay verilmiş.
- [ ] Şirket içi karar mercii "BlueCherry API kullanımı" için yazılı
  onay vermiş.
- [ ] BlueCherry hesap yöneticimiz varsa muhatabı belirlenmiş.

### Bu ön koşullar tamamlanmadan bu fazda **hiçbir şey yapılmaz.**

### Yalnızca ön koşullar tamamlanırsa yapılabilecekler
- CGS destek üzerinden salt okuma API anahtarı talebi.
- Sadece **lojistik ile ilgili** minimum endpoint (`/customer`, `/shipment`).
- Üretim/stok/faturalama modüllerine **erişim istenmez**.

### Notu
Bugünkü sürüm BlueCherry'nin dışa aktardığı **CSV/XLSX'i zaten
okuyabiliyor**. Yani BlueCherry tarafı hiçbir zaman API'ye açılmasa bile,
manüel export ile çalışmaya devam ederiz.

---

## Karar Matrisi

| Adım | Bizim yetki alanımızda mı? | Diğer ekiple çakışma riski? | Öncelik |
|---|---|---|---|
| GLS omurgası | Evet | Yok | **Yapıldı** |
| Sentez ODBC | Evet (yerel IT) | Yok | **Sıradaki** |
| BlueCherry API | Hayır (Kanada) | **Yüksek** | Ertelendi, koordinasyon sonrası |

---

## IT Sunumu — Vurgulanacak Mesaj

1. **Bugün teslim edilen:** GLS için tam otomasyon. Manüel POD indirme dönemi bitti.
2. **Sonraki adım (bizim kontrolümüzde):** Sentez'den adres senkronu —
   sadece read-only kullanıcı ve ağ erişimi gerekiyor.
3. **BlueCherry:** Kanada ekibinin görüş bildirmesi bekleniyor; biz o
   kararı zorlamıyoruz. Manüel CSV her zaman yedek.

Bu sıralama:
- **Hızlı somut değer** üretir (GLS bugün, Sentez birkaç hafta).
- **Kimseyle çatışmaz** — her adım ilgili ekibin onayıyla ilerler.
- **Geri çekilebilir** — herhangi bir adım durursa üstteki katman
  çalışmaya devam eder.
