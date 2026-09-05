# Kullanım Kılavuzu — GLS Teslimat Merkezi (Masaüstü Uygulaması)

Bu belge yazılımdan anlamayan bir kullanıcı içindir. Kurulumdan sonra
uygulamayı tek çift-tıklamayla açıp beş sekmede işlerinizi halledebilirsiniz.

---

## 1. Kurulum (Tek Sefer)

### Ubuntu (ofis bilgisayarı — önerilen ortam)

```bash
# Sistem paketleri
sudo apt update
sudo apt install -y python3-venv python3-tk
# İsteğe bağlı: DOCX → PDF için (yoksa uygulama DOCX üretir, Word'de PDF'e çevirebilirsiniz)
sudo apt install -y libreoffice --no-install-recommends

# Uygulama
git clone <repo-url> gls-pod-system
cd gls-pod-system
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Şablonları üret (bir kere)
python -m templates_engine._bootstrap_templates
```

Masaüstü kısayolu (isteğe bağlı):
```bash
cat > ~/.local/share/applications/gls-teslimat.desktop <<EOF
[Desktop Entry]
Type=Application
Name=GLS Teslimat Merkezi
Exec=$(pwd)/venv/bin/python -m gui.app
Path=$(pwd)
Icon=applications-office
Terminal=false
Categories=Office;
EOF
```

### macOS (geliştirme / test)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m templates_engine._bootstrap_templates
```

---

## 2. Uygulamayı Başlat

```bash
python -m gui.app
```

İlk açılışta **Mock modu** aktif — kimlik bilgisi gerekmez, her şey yerel
sahte sunucu üzerinden çalışır. Tüm sekmeleri deneyebilirsiniz.

Ayrıca mock sunucusunu ayrı bir terminalde çalıştırın (POD ve Etiket
sekmeleri buna ihtiyaç duyar):

```bash
uvicorn gls_api.mock_server:app --port 8788
```

---

## 3. Sekmeler

### 📥 Teslimat Kanıtı
1. Excel dosyasını sürükleyip bırakın **veya** "Dosya Seç"e tıklayın.
2. (Opsiyonel) Sheet adı ve limit değerlerini ayarlayın.
3. **▶ Çalıştır** → API'den POD PDF'leri iner, ekrandaki logdan izleyin.
4. Bitince **ZIP Oluştur** ya da **📁 Çıktı Klasörünü Aç**.

### 🏷️ Etiket Oluştur
1. Kanal seçin: **GLS Netherlands** (varsayılan) veya **ShipIT**.
2. Gönderici + Alıcıyı adres defterinden seçin.
3. Ağırlık, koli sayısı, referansı girin.
4. **🏷️ Etiket Oluştur** → PDF olarak kaydedin.
5. Gün sonunda **Confirm** ile toplu onay verin.

### 📄 Şablon Doldur
1. Bir şablon seçin: `packing_list.docx`, `proforma.docx`, `shipping_label.docx`.
2. Gönderici / Alıcı seçin, Meta alanları doldurun.
3. **+ Kalem** ile içerik satırları ekleyin.
4. **📝 DOCX Oluştur** — Word ile açılabilir dosya.
   **📄 PDF'e Çevir** — LibreOffice varsa doğrudan PDF.
5. E-posta şablonları: seçin → **✉️ Oluştur & Panoya Kopyala** → istediğiniz
   e-posta programına yapıştırın.

### 👥 Adres Defteri
- **+ Ekle** ile yeni kart. Ad zorunlu, diğerleri opsiyonel.
- **⬇ İçe Aktar** — Sentez veya BlueCherry'den aldığınız CSV/XLSX'i seçin.
  Sütun adları otomatik tanınır (Cari Adı → name, Vergi No → tax_no vs).
- **⬆ Dışa Aktar** — yedek almak için.
- Arama kutusuna yazdıkça liste anında filtrelenir.

### ⚙️ Ayarlar
- Mod: `mock` / `test` / `prod`.
- GLS'ten kimlik geldiğinde bu sekmeden `.env` dosyasını güncelleyin.
- **🔌 Bağlantıyı Test Et** — ShipIT sunucusuna küçük bir istek atar.

---

## 4. Şablonu Word'de Nasıl Düzenlerim?

Tüm şablonlar `templates/` altındaki normal Word dosyalarıdır. Kelimeleri,
başlıkları, renkleri istediğiniz gibi değiştirebilirsiniz. **Sadece bir kural:**

`{{ shipper.name }}` gibi çift kaşlı ifadelere dokunmayın — bunlar
uygulamanın veriyi yerleştirdiği yerlerdir.

Yeni bir kalem satırı istiyorsanız: kalem tablosunda son satırın içine
tıklayın ve **Tab** tuşuna basın. Word otomatik olarak yeni satır ekler.
Yeni satırın hücrelerine bir sonraki index'i koyun:
`{% if items|length > 10 %}{{ items[10].description }}{% endif %}`.

Ya da yeni şablonlar oluşturmak istiyorsanız: `templates/` altına yeni bir
`.docx` dosyası koyun. Uygulama otomatik olarak listede gösterir.

---

## 5. Sıkça Sorulan Sorular

**S: Uygulama internete gizli veri gönderiyor mu?**
Hayır. Yalnızca GLS'in resmî API'lerine, sizin verdiğiniz parça numaralarını
sorgular. Adres defteri, şablonlar ve tüm veriler yerel bilgisayarınızda kalır
(`~/.gls_pod/addressbook.db`).

**S: GLS'e her API çağrısı için ücret ödeyecek miyiz?**
Hayır. Etiket + takip + teslimat kanıtı API'leri **ücretsiz** — sadece
gerçek kargo tarifesini ödüyorsunuz.

**S: Sentez ERP ile entegre mi?**
Evet, ancak **iki aşamalı**:
- **Bugün:** Sentez'in dışa aktardığı CSV/XLSX'i "İçe Aktar"la okur.
- **Faz 2:** IT'nin sağladığı MSSQL ODBC bağlantısıyla doğrudan okuyacak
  (`docs/SENTEZ_BLUECHERRY_ENTEGRASYON.md`).

**S: Hata alıyorum, ne yapmalıyım?**
1. Ayarlar sekmesi → **ℹ️ Sistem Bilgisi** — sürüm ve mod bilgisini alın.
2. Ekrandaki log alanının içeriğini seçip kopyalayın.
3. IT'ye yazın: mod + hata metni + hangi sekme.

---

## 6. Yedekleme

Yedeklenecek klasörler:
- `~/.gls_pod/` (adres defteri)
- `templates/` (şablonlar — Word'de değiştirdiyseniz)
- `.env` (kimlik ayarları)

Basit yedek: `zip -r gls_backup.zip ~/.gls_pod templates .env`
