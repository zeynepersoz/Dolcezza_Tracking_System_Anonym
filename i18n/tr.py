# -*- coding: utf-8 -*-
"""Turkce metinler. `en.py` ile anahtar kumesi BIREBIR ayni olmali (test eder).

Anahtar duzeni: `<ekran>.<eleman>`. Ortak metinler `common.` altinda toplanir —
ayni kelimeyi her ekranda yeniden cevirmemek icin.

`settings.*` anahtarlari BURADA YAZILMAZ, `settings/schema.py`den uretilir (bkz.
dosya sonu): alan etiketi okunakli olsun diye alanin yaninda duruyor, iki yerde
tutulursa kaciniilmaz olarak ayrisirdi. Ingilizcesi `en.py`de elle yazilir;
parite testi iki sozlugu karsilastirdigi icin eksik kalan hemen belli olur.
"""
from settings.schema import SECTIONS, TABS

STRINGS: dict[str, str] = {
    # ---------- toplu etiket ----------
    "dispatch.title": "Toplu Sevkiyat",
    "dispatch.note": "Paketleme tarihini seçin; fatura numarası girili ve henüz etiketlenmemiş koliler listelenir.",
    "dispatch.date_label": "Paketleme tarihi",
    "dispatch.date_option": "{date} — {boxes} koli · {stores} mağaza",
    "dispatch.no_dates": "Son 30 günde etiketlenmemiş koli bulunamadı.",
    "dispatch.prepare": "Hazırla",
    "dispatch.loading": "Koliler Sentez'den okunuyor…",
    "dispatch.empty": "Bu tarihte etiketlenecek koli yok.",
    # Onizleme bolumleri
    "dispatch.ready_title": "Hazır ({count})",
    "dispatch.search_ph": "Mağaza kodu, unvan, şehir…",
    "dispatch.search_empty": "Aramayla eşleşen mağaza yok.",
    "dispatch.select_all": "Tümünü seç",
    "dispatch.blocked_title": "Eksik bilgi ({count})",
    "dispatch.skipped_title": "Kapsam dışı ({count})",
    "dispatch.state.ready": "Hazır",
    "dispatch.state.merge_question": "Birleştirildi",
    "dispatch.state.no_address": "Adres yok",
    "dispatch.state.ambiguous_address": "Adres belirsiz",
    "dispatch.state.missing_weight": "Ağırlık yok",
    "dispatch.reason.not_gls_code": "GLS NL mağaza kodu değil (İrlanda/ShipIT)",
    "dispatch.reason.already_labeled": "Bu koliler zaten etiketlendi",
    "dispatch.help.no_address": "Adres defterinde bu mağaza kodu yok. Bir kez girin, sonraki günler otomatik eşleşir.",
    "dispatch.help.ambiguous_address": "Adres defterinde bu kod için {count} kayıt var. Doğru olanı bırakıp diğerlerini silin.",
    "dispatch.help.missing_weight": "Sentez'de koli ağırlığı (UD_KA) boş. Ağırlıksız etiket yanlış navlun demektir.",
    # Birlestirme sorusu — kullanicinin "sistem sorsun" kurali
    "dispatch.merged_note": "{count} fatura tek adreste birleştirildi",
    "dispatch.split": "Ayır",
    "dispatch.unsplit": "Birleştir",
    "dispatch.boxes": "{count} koli",
    "dispatch.address_title": "{store} için adres",
    "dispatch.address_save": "Adresi kaydet",
    "dispatch.enter_address": "Adres gir",
    "dispatch.fix_address": "Adresi düzelt",
    "dispatch.edit_address": "Adresi düzenle",
    "dispatch.detail": "Detay",
    "dispatch.detail_missing_weight": "ağırlık eksik",
    # Is akisi
    "dispatch.create_btn": "{count} sevkiyat için etiket oluştur",
    "dispatch.create_confirm": "{count} sevkiyat için etiket oluşturulacak. Etiket geri alınamaz. Devam edilsin mi?",
    "dispatch.progress_title": "Etiketler oluşturuluyor",
    "dispatch.progress_line": "{current} / {total} · {store}",
    "dispatch.done_title": "Etiketler hazır",
    "dispatch.done_line": "{created} sevkiyat oluşturuldu, {failed} hata",
    "dispatch.result_folder": "Klasör",
    "dispatch.download_pdf_job": "Bu yazdırmadakiler ({count} etiket)",
    "dispatch.download_pdf_day": "{day} gününün tamamı",
    "dispatch.download_pdf": "Tümünü tek PDF indir",
    "dispatch.err_merge_failed": "Etiketler birleştirilemedi.",
    "dispatch.download_zip": "Tümünü ZIP indir",
    "dispatch.failed_title": "Hatalı sevkiyatlar",
    # Hatalar
    "dispatch.err_title": "Toplu etiket işi hata verdi",
    "dispatch.err_bad_date": "Geçersiz tarih.",
    "dispatch.err_bad_store": "Geçersiz mağaza kodu.",
    "dispatch.err_no_shipper": "Gönderici adresi eksik: {fields}. Etiket basılmadan önce varsayılan gönderici adresi tanımlanmalı.",
    "dispatch.err_nothing": "Etiket oluşturulacak sevkiyat seçilmedi.",
    "dispatch.err_busy": "Zaten bir toplu etiket işi çalışıyor.",
    "dispatch.err_no_label": "{store} için etiket bulunamadı.",
    "dispatch.err_no_labels": "{day} tarihinde etiket bulunamadı.",
    # ---------- ortak ----------
    "app.title": "GLS Gönderi Takip Sistemi",
    "app.mode": "Mod",
    "common.search": "Ara",
    "common.filter": "Filtrele",
    "common.filters": "Filtreler",
    "common.clear": "Temizle",
    "common.cancel": "Vazgeç",
    "common.save": "Kaydet",
    "common.close": "Kapat",
    "common.download": "İndir",
    # Disa aktarma — ayni anahtarlar hem koli iceriginde hem listelerde kullanilir.
    "export.menu": "Dışa Aktar",
    "export.xlsx": "Excel indir",
    "export.pdf": "PDF indir",
    "export.style": "Model",
    "export.color": "Renk",
    "export.size": "Beden",
    "export.barcode": "Barkod",
    # "Adet" ile "barkod satiri" AYRI seylerdir; sutun basligi bunu soylemezse
    # 28 barkod ile 34 adet esit saniliyor (yasandi).
    "export.pieces": "QTY",
    "export.total_row": "TOPLAM",
    "export.note_column": "Not",
    # PDF'in ust satiri: dosya elden ele dolasiyor, hangi an ve kac satir
    # oldugu belgenin USTUNDE yazmazsa kimse bilemiyor.
    "export.subtitle": "{count} satır · {at}",
    "export.note": "Ekrandaki süzgeçlerle indirir.",
    # Coklu secim: musteri bazli koli icerigi Excel'i.
    "export.bulk_contents": "Seçili kolilerin içeriği",
    "export.selected": "{count} koli seçildi",
    "export.clear_selection": "Seçimi temizle",
    "export.select_all": "Hepsini seç",
    "export.too_many": "En fazla {limit} koli seçilebilir; {count} koli seçildi.",
    "export.none_selected": "Hiç koli seçilmedi.",
    "export.no_records": "kayıt yok",
    "export.sheet_summary": "Özet",
    "export.sheet_lines": "İçerik",
    "export.sheet_matrix": "Beden Matrisi",
    "common.refresh": "Yenile",
    "common.detail": "Detay",
    "common.all": "Tümü",
    "common.total": "Toplam",
    "common.none": "Yok",
    "common.yes": "Var",
    "common.logout": "Çıkış",
    "common.menu_toggle": "Menüyü aç/kapat",
    "common.language": "Dil",
    "common.parcel_count": "{count} kargo",
    "common.selected": "{count} seçili",
    "common.select_all": "Tümünü seç",
    "common.empty": "Kayıt yok",
    "common.no_data": "Veri yok",
    "common.loading": "Yükleniyor…",
    "common.apply": "Uygula",
    "common.action": "İşlem",
    "common.delete": "Sil",
    "common.confirm_delete": "Silinsin mi?",
    "common.truncated": "İlk {count} sonuç gösteriliyor.",
    "common.page_range": "{a}-{b} / {total}",
    "common.prev_page": "Önceki sayfa",
    "common.next_page": "Sonraki sayfa",

    # ---------- kargo alanlari (tablo basliklari) ----------
    "field.tracking_no": "Takip No",
    "field.status": "Durum",
    "field.season": "Sezon",
    "field.channel": "Taşıma Firması",
    "field.country": "Ülke",
    "field.consignee": "Alıcı",
    "field.store_code": "Mağaza Kodu",
    # Basliktaki "#" sutunu daraltir. HUCREYE konmaz: kullanici fatura numarasini
    # kopyalayip ERP'de ariyor, `#5000466` orada hicbir seyi bulmaz.
    "field.invoice_number": "Fatura #",
    "field.emc_invoices": "EMC Fatura",
    "field.sales_rep": "Satış Temsilcisi",
    "field.shipment_date": "Sevk Tarihi",
    "field.delivered_date": "Teslim Tarihi",
    "field.shipment_method": "Sevkiyat Yöntemi",
    "field.last_event": "Son Hareket",
    "field.last_check": "Son Kontrol",
    "field.pod": "POD",
    "field.zip_code": "Posta Kodu",
    "field.weight": "Ağırlık",
    "field.reference": "Referans",
    "field.explain": "Açıklama",

    # ---------- durumlar ----------
    "status.created": "Oluşturuldu",
    "status.in_transit": "Yolda",
    "status.out_for_delivery": "Dağıtımda",
    "status.delivered": "Teslim Edildi",
    "status.exception": "Sorun",
    "status.returned": "İade",
    "status.cancelled": "İptal",

    # GLS'in olay metinleri Ingilizce gelir ve oldugu gibi gosterilir; yazdigimiz
    # tek olay metni bu. TR bicimi veritabaninda saklanan bicimdir — degistirmek
    # eski kayitlarin cevrilmesini bozar (bkz. `tracking/db.py:event_text`).
    "event.delivered_to": "Teslim edildi, teslim alan: {name}",

    # ---------- gezinme ----------
    "nav.dashboard": "Ana Panel",
    "nav.labels": "Etiket Oluştur",
    "nav.dispatch": "Toplu Etiket",
    "nav.tracking": "Canlı Takip",
    "nav.pod": "POD Arşivi",
    "nav.problems": "Sorunlu Kargolar",
    "nav.archive": "Belge Arşivi",
    "nav.addresses": "Adres Defteri",
    "nav.reports": "Raporlar",
    "nav.reports_delivery_times": "Teslimat Süreleri",
    "nav.reports_parcel_shop": "ParcelShop Teslimatları",
    "nav.reports_completion": "Sipariş Tamamlanma",
    "completion.title": "Sipariş Tamamlanma Raporu",
    "completion.subtitle": "{season} · sipariş → sevkiyat → müşteriye teslim, ürün adedi üzerinden.",
    "completion.no_data": "Bu sezon için sipariş verisi bulunamadı (ERP görünümü henüz açılmamış olabilir).",
    "completion.card.ordered": "Sipariş Edilen",
    "completion.card.ordered_note": "{orders} sipariş · {customers} müşteri",
    "completion.card.shipped": "Sevk Edilen",
    "completion.card.shipped_note": "Türkiye'den çıktı (picklenen)",
    "completion.card.boxed": "Takipteki Koli İçeriği",
    "completion.card.boxed_note": "GLS kanalında kutulanmış",
    "completion.card.delivered": "Müşteriye Ulaşan",
    "completion.card.delivered_note": "Teslim edilmiş kolilerin içeriği",
    "completion.scope_title": "Kapsam Farkı",
    "completion.scope_body": "Sipariş ve sevkiyat rakamları ERP'nin tüm sipariş listesinden gelir (CA/US/RU/AU/NZ hariç — İrlanda, İngiltere, Tayvan gibi ülkeler dahildir). Teslim rakamı ise YALNIZCA bizim takip ettiğimiz GLS Hollanda kanalını kapsar. Bu yüzden aradaki fark eksik veri değil, kapsam farkıdır; teslim oranı ayrıca takipteki koli içeriğine göre de verilmiştir.",
    "completion.of_ordered": "siparişin",
    "completion.of_boxed": "takipteki koli içeriğinin",
    "completion.table_title": "Ülke Bazlı Tamamlanma",
    "completion.col.orders": "Sipariş",
    "completion.col.ordered": "Sipariş Adedi",
    "completion.col.shipped": "Sevk Edilen",
    "completion.col.boxed": "Kutulanmış",
    "completion.col.delivered": "Teslim Edilen",
    "completion.col.pct_shipped": "Sevk %",
    "completion.col.pct_delivered": "Teslim %",
    "completion.not_tracked": "takip dışı",
    "nav.settings": "Ayarlar",

    # ---------- raporlar ----------
    "reports.title": "Sevkiyat & Teslimat Süreleri",
    "reports.subtitle": "Paketlemeden alıcıya ulaşana kadar geçen süre, ülke ve sevkiyat yöntemine göre.",
    "reports.no_data": "Henüz tarihleri tam olan teslim edilmiş koli yok — rapor teslimatlar biriktikçe dolacak.",
    "reports.card.total_title": "Toplam Sevkiyat",
    "reports.card.total_note": "{items} ürün ({countries} ülke)",
    "reports.card.normal_title": "Normal Teslimat",
    "reports.card.normal_note": "Sorunsuz gönderiler",
    "reports.card.courier_title": "GLS Kurye Dağıtım",
    "reports.card.courier_note": "GLS girişi → alıcıya teslim",
    "reports.card.delayed_title": "Lokal Gecikmeli",
    "reports.card.delayed_note": "{items} ürün (ort. {days} gün GLS)",
    "reports.warning_title": "Lokal Teslimat Gecikmeleri Hakkında",
    "reports.warning_body": "Dağıtım esnasında alıcının mağazada bulunmaması, ileri tarihli teslimat randevusu gibi lokal müşteri nedenleriyle geciken gönderiler genel ortalamayı yükseltmektedir. Sorunsuz kargolarda GLS kurye kapı teslimatı ortalama {normal} günde tamamlanırken, bu tür lokal gecikmelerde süre ortalama {delayed} güne çıkmaktadır. Tablodaki sorunsuz gönderi süreleri ayrıca hesaplanarak sunulmuştur.",
    "reports.weekend_note": "Tüm süreler iş günü olarak hesaplanır — Cumartesi/Pazar sayılmaz.",
    "reports.country_table_title": "Ülke Bazlı Teslimat Süreleri ve Lokal Gecikme Analizi",
    "reports.method_table_title": "Sevkiyat Yöntemine Göre Teslimat Süreleri",
    "reports.col.country": "Ülke",
    "reports.col.method": "Sevkiyat Yöntemi",
    "reports.col.shipment": "Gönderi / Ürün",
    "reports.col.emc_gls": "Paketleme → GLS",
    "reports.col.gls_store": "GLS → Mağaza",
    "reports.col.avg_delivery": "Ort. Teslimat",
    "reports.col.local_delay": "Lokal Gecikme",
    "reports.col.metric": "Ölçüt",
    "reports.col.value": "Değer",
    "reports.normal": "Normal",
    "reports.delayed": "Gecikmeli",
    "reports.boxes": "{count} koli",
    "reports.general": "Genel: {days} gün",
    "reports.delay_count": "{count} kargo",
    "reports.no_value": "—",
    "reports.parcel_shop_title": "ParcelShop Teslimatları",
    "reports.parcel_shop_note": "GLS bir koliyi doğrudan alıcıya değil kendi ParcelShop'una (teslim noktası) bıraktığında bunu \"teslim edildi\" sayar; müşteri almazsa GLS koliyi bir süre sonra geri döndürür — bu koliler normal iade sayısını gerçekte olmayan bir \"başarısız teslimat\" gibi şişirir.",
    "reports.parcel_shop_empty": "Kayıtlarda ParcelShop üzerinden geçen koli bulunamadı.",
    "reports.parcel_shop.total": "ParcelShop'a Uğradı",
    "reports.parcel_shop.total_note": "Toplam koli",
    "reports.parcel_shop.at_risk": "Şu An Riskte",
    "reports.parcel_shop.at_risk_note": "Henüz alınmadı / teslim değil",
    "reports.parcel_shop.returned": "İadeye Döndü",
    "reports.parcel_shop.returned_note": "Alınmadığı için geri gitti",
    "reports.parcel_shop.collected": "Başarıyla Alındı",
    "reports.parcel_shop.collected_note": "Müşteri ParcelShop'tan aldı",
    "reports.parcel_shop.col.first_at": "İlk ParcelShop Olayı",
    "reports.parcel_shop.col.status": "Mevcut Durum",
    "reports.parcel_shop.col.timeout": "Zaman Aşımı",
    "reports.parcel_shop.timed_out": "Süresi doldu",

    # ---------- belge arsivi ----------
    # `nav.pod` ("POD Arşivi") teslimat listesidir; burasi DISKTEKI dosyalar.
    "archive.title": "Belge Arşivi",
    # Kirinti yolunun ilk halkasi; sabit `archive.subtitle` alt basligin yerini
    # aldi (klasorde gezerken "neredeyim" bilgisi daha degerli). Kok artik
    # sezon degil BELGE TURU seviyesidir.
    "archive.crumb_root": "Belge Arşivi",
    "archive.folder_count": "{count} belge",
    "archive.folder_unnamed": "(kodsuz)",
    "archive.search": "Ara",
    "archive.search_hint": "Takip no, mağaza, fatura veya referans",
    # Kok klasorlerin adi (cogul); `archive.pod`/`archive.label` indirme
    # dugmesinin metnidir, ikisi ayni sey degil.
    "archive.tree_pod": "PODlar",
    "archive.tree_label": "Etiketler",
    "archive.pod": "POD",
    "archive.label": "Etiket",
    "archive.date": "Tarih",
    "archive.documents": "Belgeler",
    "archive.empty": "Aramaya uyan arşivlenmiş belge yok.",
    "archive.err_no_label": "{no} için arşivlenmiş etiket dosyası bulunamadı.",

    # ---------- sorunlu kargolar ----------
    "problems.title": "Sorunlu Kargolar",
    "problems.subtitle": "Elle müdahale bekleyen kargolar. Sekmeler sorunun türünü ayırır.",
    "problems.kind.exception": "Sorun bildirildi",
    "problems.kind.damaged": "Hasar bildirildi",
    "problems.kind.partial": "Kısmi teslimat",
    "problems.kind.pod_missing": "POD eksik",
    "problems.kind.stale": "Yolda takıldı",
    "problems.kind.not_handed_over": "GLS teslim almadı",
    "problems.hint.exception": "GLS bir sorun bildirdi",
    "problems.hint.damaged": "Koli hasarlı; GLS yola devam ediyor, içeriği kontrol edin",
    "problems.hint.partial": "Aynı faturanın diğer kolileri teslim edildi",
    "problems.hint.pod_missing": "Teslim edildi, imza gelmedi",
    "problems.hint.stale": "GLS koliyi {days}+ gün önce aldı, hâlâ teslim edilmedi",
    "problems.hint.not_handed_over": "Etiket basıldı, koli GLS'e verilmedi",
    "problems.kind.returned": "İade ediliyor",
    "problems.hint.returned": "Koli göndericiye iade ediliyor",
    "problems.empty": "Bekleyen sorun yok.",
    "problems.reason": "Sebep",
    "problems.kind": "Sorun",
    "problems.reason_col": "Açıklama / Sebep",
    "problems.stale_threshold": "Kaç gün yolda kalan koli takılmış sayılsın",
    "problems.clear_filter": "Filtreyi temizle",
    "problems.showing": "{count} kayıt gösteriliyor.",
    "problems.try_pod": "POD dene",

    # ---------- GLS'e POD sorma mektubu (elle gonderilir) ----------
    "inquiry.open": "GLS'e sor",
    "inquiry.title": "GLS'e POD Sorgusu",
    "inquiry.subtitle": "Teslim edileli {days} günü geçmiş, teslimat kanıtı hâlâ "
                        "gelmemiş {count} kargo.",
    "inquiry.subject": "Konu",
    "inquiry.body": "Mektup",
    "inquiry.copy": "Kopyala",
    "inquiry.copy_rich": "📋 Şablonu Kopyala (Outlook/Gmail)",
    "inquiry.copy_plain": "📄 Düz Metin Kopyala",
    "inquiry.copy_subject": "Konuyu Kopyala",
    "inquiry.copied": "Kopyalandı ✓",
    "inquiry.tab_preview": "E-posta Görünümü",
    "inquiry.tab_plain": "Düz Metin",
    "inquiry.back": "Sorunlu listesine dön",
    "inquiry.note": "Bu sayfa yalnızca gösterir. Otomatik gönderim Ayarlar &rsaquo; "
                    "POD gelmedi, GLS'e sor sekmesinden açılır; açıkken aynı mektubu "
                    "sistem günlük özetle aynı saatte GLS'e yollar ve bir kargoyu "
                    "yalnızca bir kez sorar.",
    "inquiry.empty": "Bekleme süresi ({days} gün) dolmuş, teslimat kanıtı eksik kargo yok.",

    # ---------- canli takip ----------
    "tracking.list_title": "Canlı Takip Listesi",
    "tracking.list_note": "Otomatik güncellenir &middot; Her 15 dakikada arka planda GLS'e sorulur",
    "tracking.active_count": "{count} aktif",
    "tracking.f.search": "Genel Arama",
    "tracking.f.store_code": "Mağaza / Müşteri Kodu",
    "tracking.f.status": "Teslimat Durumu",
    "tracking.f.channel": "Gönderi Türü",
    "tracking.f.shipment_method": "Gönderim Şekli",
    "tracking.ph.search": "Takip no, referans, alıcı...",
    "tracking.ph.tracking_no": "Takip numarasını girin...",
    "tracking.ph.any": "Ara...",
    "tracking.ph.consignee": "Alıcı adı...",
    "tracking.ph.zip_code": "Posta kodunu girin...",
    "tracking.ph.country": "Ülke...",
    # Tarih deseni cevrilmez: alan ISO bicimi bekliyor.
    "tracking.ph.date": "YYYY-MM-DD...",
    "tracking.status_any": "Durum seç",
    "tracking.season_any": "Tüm sezonlar",
    "tracking.shipment_method_any": "Tüm gönderim şekilleri",
    "tracking.channel_any": "Tüm gönderiler",
    "tracking.channel_nl": "GLS-NL — GLS Hollanda",
    "tracking.channel_ie": "GLS-IE — GLS İrlanda",
    "tracking.channel_fedex": "FEDEX — FedEx (Kanada/İrlanda)",
    "tracking.reset_filters": "Filtreleri Sıfırla",
    "tracking.upload": "Toplu Excel Yükle",
    "tracking.upload_note": "Yedek yol: takip listesinin asıl kaynağı MSSQL'dir. Excel'i "
                            "yalnızca MSSQL'e ulaşılamadığında ya da orada olmayan bir "
                            "gönderi için kullanın.",
    "tracking.upload_sheet_ph": "Sheet adı (boş: ilk sheet)",
    "tracking.upload_all_sheets": "Tüm sayfaları aktar",
    "tracking.upload_all_sheets_note": "\"Problematic Packages\" / \"Return to Sender\" "
                                       "sayfaları sorunlu olarak işaretlenir.",
    "tracking.upload_submit": "Yükle &amp; İçe Aktar",
    "tracking.mssql": "MSSQL'den Yenile",
    "tracking.mssql_note": "Sezon görünümlerindeki takip numaralarını panele ekler ve "
                           "günceller. Paneldeki kayıtları silmez.",
    "tracking.mssql_submit": "Şimdi Aktar",
    "tracking.mssql_busy": "MSSQL sorgulanıyor, birkaç saniye sürebilir...",
    "tracking.scan_now": "Şimdi Tara",
    "tracking.col.invoice_store": "Fatura / Mağaza",
    "tracking.col.dates": "Sevk / Teslim",
    "tracking.col.carrier": "Nakliye",
    "tracking.empty": "Henüz kargo yok (veya filtreyle eşleşen kayıt yok). Etiket "
                      "sekmesinden ya da toplu Excel yükleyerek ekleyin.",
    "tracking.narrow_hint": "Daha az kayıt görmek için filtreleri daraltın.",
    # ice aktarma sonucu
    "import.ok": "{count} kayıt içe aktarıldı",
    "import.none": "Hiçbir kayıt içe aktarılamadı",
    "import.file": "Dosya",
    "import.totals": "Toplam satır: {total} &middot; Atlanan: {skipped}",
    "import.errors": "Hatalar ({count})",
    "import.refresh_table": "Tabloyu yenile",
    # etiket olusturuldu
    "labels.created": "Etiket oluşturuldu",
    "labels.reference": "Referans",
    "labels.see_in_tracking": "Takip listesinde gör",
    # detay yan paneli
    "tracking.detail.invoice": "Fatura",
    "tracking.detail.invoice_main": "Ana",
    "tracking.detail.invoice_emc": "EMC",
    "tracking.detail.shipment": "Sevkiyat",
    "tracking.detail.store_code": "Mağaza kodu",
    "tracking.detail.awaiting": "Bekleniyor",
    "tracking.detail.method": "Yöntem",
    "tracking.detail.est_cost": "Tahmini bedel",
    # Koli icerigi (beden matrisi). Veri canli MSSQL'den gelir, SQLite'ta yok —
    # bu yuzden "kayit yok" ile "ERP kapali" AYRI mesajlar: ikisi karisirsa
    # kullanici gercekten bos bir koli mi yoksa bozuk bir baglanti mi oldugunu
    # anlayamaz.
    "tracking.detail.contents": "Koli İçeriği",
    "tracking.detail.style": "Model / Renk",
    "tracking.detail.total": "Toplam",
    "tracking.detail.box_code": "EAN Code",
    "tracking.detail.box_no": "Koli No",
    "tracking.detail.contents_summary": "{models} model",
    "tracking.detail.contents_empty": "Bu koli için ERP kaydı bulunamadı.",
    "tracking.detail.contents_error": "İçerik alınamadı — ERP bağlantısı yanıt vermedi.",
    "tracking.detail.contents_off": "ERP bağlantısı yapılandırılmamış.",
    "tracking.detail.history": "Statü Geçmişi",
    "tracking.detail.no_events": "Statü olayı kaydedilmedi",
    "tracking.detail.download_pod": "POD PDF İndir",
    "tracking.detail.box_image": "Gönderi Detayları",
    "tracking.detail.box_image_title": "Gönderi Detayları Yükle",
    "tracking.detail.box_image_note": "FedEx'te resmi POD yok — koli fotoğrafını buradan yükleyin, otomatik olarak PDF'e gömülür.",
    "tracking.detail.box_image_ready": "Fotoğraf(lar) yüklendi — PDF hazır.",
    "tracking.detail.box_image_missing": "Henüz gönderi fotoğrafı yüklenmedi.",
    "tracking.detail.box_image_short": "Detaylar",
    "tracking.detail.box_image_download": "PDF İndir",
    "tracking.detail.box_image_remove": "Kaldır",
    "tracking.detail.box_image_remove_confirm": "Yüklenen fotoğraflar ve oluşturulan PDF silinecek. Sonra yeniden yükleyebilirsiniz. Devam edilsin mi?",
    "tracking.detail.box_image_dropzone": "Koli Fotoğraflarını Buraya Bırakın veya Seçin",
    "tracking.detail.box_image_dropzone_note": "Birden fazla resim seçebilirsiniz. Yükleme tamamlandıktan sonra üzerine ekleme yapılamaz; değiştirmek için önce “Kaldır” deyin.",
    # etiket iptali — geri alınamaz, bu yüzden metinler açıkça uyarır
    "tracking.cancel.start": "Etiketi iptal et",
    "tracking.cancel.do": "Evet, etiketi iptal et",
    "tracking.cancel.warn_title": "Bu işlem geri alınamaz",
    "tracking.cancel.warn_body": "Etiket GLS tarafında kapatılır ve koli hiçbir "
                                 "sürece girmez. Koli yine gönderilecekse yeni "
                                 "etiket basmanız gerekir.",
    "tracking.cancel.confirm": "{tracking_no} numaralı etiket kalıcı olarak iptal "
                               "edilecek. Emin misiniz?",
    "tracking.cancel.event_note": "Etiket panelden iptal edildi ({user})",
    "tracking.cancel.err_channel": "Yalnızca GLS Hollanda (NL) etiketleri iptal edilebilir.",
    "tracking.cancel.err_status": "Bu koli “{status}” durumunda — yola çıkmış bir "
                                  "koli iptal edilemez.",

    # ---------- ana panel ----------
    # OLCUT BASLIKTA: yan yana duran uc "teslim" sayisinin (bu hafta / bugun /
    # tum zamanlar) hangisinin neyi saydigi yazmiyordu. Kartlarin ALTINDAKI
    # aciklama satirlari kaldirildi (kullanici istegi), yalnizca yuzde kaldi —
    # bu yuzden olcut basliga tasindi.
    "dash.week": "Bu Hafta Sevk Edilen",
    "dash.total_parcels": "Toplam: {count}",
    "dash.pct_of_total": "%{pct} toplam",
    "dash.delivered_today": "Bugün Teslim Edilen",
    # Panelin alt kismindaki iki yeni liste: kullanici sayiyi degil KARGOLARI
    # gormek istedi — takip no, fatura, magaza panel acilir acilmaz gorunur.
    "dash.delivered_today_note": "Bugün alıcıya ulaşan kargolar",
    "dash.no_delivered_today": "Bugün henüz teslimat yok.",
    # Ayrac HTML varligi degil DUZ karakter: bu metin makroya parametre olarak
    # gidiyor, orada `|safe` yok ve `&middot;` ekrana harfi harfine dusuyordu.
    "dash.out_note": "Dağıtımda · bugün teslim edilmesi bekleniyor",
    "dash.no_out_for_delivery": "Şu an dağıtımda kargo yok.",
    "dash.problems_waiting": "{count} kargo elle müdahale bekliyor",
    "dash.problems_breakdown": "{exception} sorunlu &middot; {returned} iade &middot; {stale} takıldı "
                               "&middot; {pod_missing} POD eksik",
    "dash.open_list": "Listeyi aç",
    # Paneldeki hizli arama. Ipucu magaza koduyla basliyor: kullanicilarin
    # ezbere bildigi tek alan o.
    "dash.search_ph": "Mağaza kodu, fatura no, takip no veya alıcı adı yazın…",
    "dash.search_hint": "Aramak için yukarıya yazın — mağaza kodu (AHAAT01), "
                        "fatura no, takip no veya alıcı adı.",
    "dash.open_tracking": "Tüm liste",
    "dash.trend_title": "Haftalık Teslimat Trendi",
    "dash.trend_note": "Son 8 hafta &middot; teslim edilen kargo sayısı",
    "dash.avg": "Ortalama",
    "dash.per_week": "/ hafta",
    # Grafikteki hafta etiketi: "H32" / "W32". Tek harf, dar eksen.
    "dash.week_prefix": "H",
    "dash.channels_title": "Taşıma Firması Dağılımı",
    "dash.channels_note": "NL vs IE &middot; statülere göre",
    # Renk aciklamasi dar: uzun statu adlari sigmiyor.
    "dash.short.delivered": "Teslim",
    "dash.short.out_for_delivery": "Dağıtım",
    "dash.short.in_transit": "Yolda",
    "dash.short.exception": "Sorun",
    "dash.short.returned": "İade",
    "dash.countries_title": "En Çok Kargo Giden Ülkeler",
    "dash.countries_note": "İlk 6 ülke &middot; teslimat performansı",
    "dash.attention_title": "Dikkat Gerektiren Kargolar",
    "dash.attention_note": "Adres hatası, gümrük, teslim edilemedi vb.",
    "dash.see_all": "Tümünü gör",
    "dash.no_exceptions": "Şu an sorunlu kargo yok.",
    "dash.tracker_title": "Otomatik Takip Taraması",
    "dash.tracker_note": "Arka planda her {minutes} dakikada bir GLS'e sorulur",
    "dash.last_run": "Son tarama",
    "dash.never_run": "Henüz çalışmadı",
    "dash.health_error": "Hata",
    "dash.health_ok": "Sağlıklı",
    "dash.delivery_chart_title": "Teslimat Süreleri (Ülkeye Göre)",
    "dash.delivery_chart_note": "İş günü &middot; teslim edilmiş kargolar &middot; hafta sonu hariç",
    "dash.delivery_chart_open": "Detaylı rapor",
    "dash.delivery_chart_unit": "gün",
    "dash.parcel_shop_title": "ParcelShop'ta Bekleyen Kargolar",
    "dash.parcel_shop_note": "Teslim edilmiş görünüyor ama alıcı ParcelShop'tan henüz almadı — iadeye dönme riski var.",
    "dash.parcel_shop_empty": "Şu an ParcelShop'ta bekleyen riskli koli yok.",

    # ---------- POD arsivi ----------
    "pod.list_title": "Teslim Edilmiş Kargolar",
    "pod.list_note": "Teslimat kanıtı (POD) belgesini indirmek için sağdaki düğmeyi kullanın.",
    "pod.bulk_zip": "Seçilenleri ZIP Olarak İndir",
    "pod.customer_pdf": "Müşteri PDF",
    "pod.customer_pdf_title": "Müşteriye gönderilebilir teslimat kanıtı",
    "pod.internal_pdf": "İç Kayıt",
    "pod.internal_pdf_title": "Fatura no, mağaza kodu ve ölçüleri de içeren iç kayıt belgesi",
    "pod.col.delivered": "Teslim",
    "pod.missing": "POD yok",
    "pod.missing_title": "Kargo teslim edildi ama GLS teslimat görüntüsünü henüz üretmedi. "
                         "Sistem her turda yeniden soruyor; denemek için tıklayabilirsiniz.",
    "pod.empty": "Henüz teslim edilmiş kargo yok (veya filtreyle eşleşen kayıt yok).",

    # ---------- giris ----------
    "auth.login_title": "Giriş",
    "auth.username": "Kullanıcı adı",
    "auth.password": "Şifre",
    "auth.submit": "Giriş Yap",
    "auth.internal_use": "iç kullanım",
    "auth.bad_credentials": "Kullanıcı adı veya şifre hatalı.",
    "auth.locked_out": "Çok fazla başarısız deneme. Birkaç dakika sonra tekrar deneyin.",
    "auth.current_password_wrong": "Mevcut şifre hatalı.",
    "auth.passwords_differ": "Yeni şifreler eşleşmiyor.",
    "auth.password_too_short": "Yeni şifre en az 8 karakter olmalı.",
    "auth.ip_blocked": "Bu adresten giriş yapılamaz. Yöneticinize başvurun.",
    "auth.rate_limited": "Çok fazla istek. Bir süre sonra tekrar deneyin.",
    "auth.back_to_login": "Giriş ekranına dön",
    # sifremi unuttum
    "auth.forgot_link": "Şifremi unuttum",
    "auth.forgot_title": "Şifre sıfırlama",
    "auth.forgot_field": "Kullanıcı adı veya e-posta",
    "auth.forgot_hint": "Hesabınıza kayıtlı e-posta adresine sıfırlama bağlantısı "
                        "gönderilir. Adres kayıtlı değilse yöneticinize başvurun.",
    "auth.forgot_submit": "Bağlantı Gönder",
    # Hesap bulunsun bulunmasin AYNI mesaj doner — aksi halde bu ekran
    # "hangi kullanici adi var" sorusunun cevabini veren bir araca donusur.
    "auth.reset_sent": "Hesap kayıtlıysa sıfırlama bağlantısı e-posta ile gönderildi. "
                       "Gelen kutunuzu (ve gereksiz klasörünü) kontrol edin.",
    "auth.reset_title": "Yeni şifre belirle",
    "auth.new_password": "Yeni şifre",
    "auth.new_password_confirm": "Yeni şifre (tekrar)",
    "auth.reset_submit": "Şifreyi Değiştir",
    "auth.reset_done": "Şifreniz değiştirildi. Yeni şifrenizle giriş yapabilirsiniz.",
    "auth.reset_invalid": "Bağlantı geçersiz. Yeni bir sıfırlama isteği oluşturun.",
    "auth.reset_expired": "Bağlantının süresi dolmuş. Yeni bir sıfırlama isteği oluşturun.",
    "auth.reset_used": "Bu bağlantı zaten kullanılmış. Yeni bir sıfırlama isteği oluşturun.",

    # ---------- sifre sifirlama maili ----------
    "reset.mail.subject": "Şifre sıfırlama",
    "reset.mail.greeting": "Merhaba {username},",
    "reset.mail.body": "GLS Gönderi Takip Sistemi hesabınız için şifre sıfırlama "
                       "isteği aldık. Yeni şifrenizi belirlemek için aşağıdaki "
                       "bağlantıyı kullanın.",
    "reset.mail.button": "Yeni şifre belirle",
    "reset.mail.expires": "Bağlantı 30 dakika geçerlidir ve yalnızca bir kez kullanılabilir.",
    "reset.mail.ignore": "Bu isteği siz yapmadıysanız bu maili yok sayabilirsiniz; "
                         "şifreniz değişmez.",

    # ---------- guvenlik (giris kaydi + IP engelleme) ----------
    "security.blocked.title": "Engelli IP'ler",
    "security.blocked.note": "Engel yalnızca giriş ekranına uygulanır; açık oturumlar "
                             "düşmez. Kendi adresinizi engelleyemezsiniz.",
    "security.block_new": "+ IP Engelle",
    "security.block": "Engelle",
    "security.unblock": "Kaldır",
    "security.note_ph": "Not (örn. sürekli hatalı deneme)",
    "security.note_from_list": "Listeden engellendi",
    "security.confirm_block": "Bu IP'den giriş yapılamayacak. Emin misiniz?",
    "security.my_ip": "Sizin adresiniz",
    "security.you": "siz",
    "security.no_blocked": "Engellenmiş IP yok.",
    "security.err_no_ip": "IP adresi boş olamaz.",
    "security.err_own_ip": "Kendi adresinizi engelleyemezsiniz — panele giremezdiniz.",
    "security.ips.title": "IP özeti",
    "security.ips.note": "Aynı adresten yığınla başarısız deneme saldırı işaretidir.",
    "security.log.title": "Giriş kaydı",
    # "Son 200 deneme" ifadesi kalkti: liste artik sayfalaniyor, tavan yok.
    "security.log.note": "Kayıtlar 90 gün saklanır.",
    "security.only_failed": "Yalnızca başarısız",
    "security.no_events": "Kayıt yok.",
    "security.ok": "Başarılı",
    "security.col.note": "Not",
    "security.col.by": "Engelleyen",
    "security.col.at": "Zaman",
    "security.col.users": "Denenen kullanıcılar",
    "security.col.ok": "Başarılı",
    "security.col.failed": "Başarısız",
    "security.col.last": "Son deneme",
    "security.col.agent": "Tarayıcı",
    "security.reason.bad_credentials": "Hatalı şifre",
    "security.reason.inactive": "Pasif hesap",
    "security.reason.locked_out": "Kilitli",
    "security.reason.ip_blocked": "IP engelli",

    # ---------- etiket olusturma ----------
    "labels.new": "Yeni Etiket",
    "labels.new_note": "GLS NL kanalında parça oluştur, PDF'i indir, otomatik takibe ekle.",
    "labels.gls_nl": "GLS Hollanda",
    "labels.gls_ie": "GLS İrlanda (ShipIT)",
    "labels.filled_from_book": "Adres defterinden dolduruldu — teyit edin / düzenleyin",
    "labels.search_address": "Adres ara: mağaza kodu, isim, şehir… ({count} kayıt)",
    "labels.book_empty": "Alıcı listesi boş. Aşağıdaki alanlara adresi manuel girebilirsiniz.",
    "labels.ph.reference": "Örnek: SP26-4471",
    "labels.note1": "Not 1",
    "labels.note2": "Not 2",
    "labels.ph.note1": "Etikete ek satır (opsiyonel)",
    "labels.ph.note2": "Etikete ek satır (opsiyonel)",
    "labels.multi_store": "birden fazla mağaza",
    "labels.multi_store_hint": "Bu ada bağlı birden fazla mağaza var — doğru mağaza kodunu seçtiğinizden emin olun.",
    "labels.weight_kg": "Koli Ağırlıkları (kg)",
    "labels.add_box": "Koli ekle",
    "labels.services": "Servisler (opsiyonel)",
    "labels.submit": "Etiket Üret",

    # ---------- adres defteri ----------
    "addr.count": "{count} kayıt",
    "addr.new": "+ Yeni Adres",
    "addr.import": "İçe Aktar",
    "addr.imported": "{total} kayıttan {imported} tanesi içe aktarıldı.",
    "addr.type": "Tip",
    "addr.business": "Kurumsal",
    "addr.private": "Bireysel",
    "addr.shipper": "Gönderici",
    "addr.set_shipper": "Gönderici Yap",
    "addr.confirm_shipper": "{name} sabit gönderici adresi yapılsın mı?",
    "addr.empty": "Henüz adres yok (veya filtreyle eşleşen kayıt yok).",
    "addr.col.name": "Ad",
    "addr.col.company": "Şirket",
    "addr.col.address": "Adres",
    "addr.col.city": "Şehir",
    "addr.col.email": "E-posta",
    "addr.ph.search": "İsim, şirket, şehir, e-posta...",
    # `Name 2` / `Name 3` / `Street` GLS alan adlaridir, cevrilmez.
    "addr.ph.name": "Ad / Name *",
    "addr.ph.company": "Şirket",
    "addr.ph.contact": "İletişim kişisi",
    "addr.ph.street": "Street *",
    "addr.ph.house_number": "No",
    "addr.ph.house_number_req": "No *",
    "addr.ph.addition": "Addition",
    "addr.ph.addition_full": "Addition (kat/daire)",
    "addr.ph.postal_code": "Posta Kodu (IE: D02 XY45 · NL: 1234 AB)",
    "addr.ph.city": "Şehir",
    "addr.ph.country": "Ülke (NL, IE, FR...)",
    "addr.ph.country_short": "NL, IE, FR...",
    "addr.ph.phone": "Telefon",
    "addr.ph.mobile": "Mobil",
    "addr.ph.email": "E-posta",

    # Vurgu icin <span> icerir, `|safe` ile basilir.
    # parti durumlari
    # satir durumlari
    # adresin nereden geldigi
    # ozet kartlari
    # onizleme
    # uretim / sonuc

    # ---------- profil ----------
    "profile.title": "Profilim",
    "profile.note": "Hesap bilgileri ve şifre değişikliği",
    "profile.role": "Rol",
    "profile.current_password": "Mevcut şifre",
    "profile.new_password": "Yeni şifre",
    "profile.new_password_confirm": "Yeni şifre (tekrar)",
    "profile.logout_note": "Şifre değiştirildiğinde bu tarayıcı dahil tüm oturumlar kapatılır; "
                           "yeni şifrenizle tekrar giriş yapmanız gerekir.",
    "profile.submit": "Şifreyi Güncelle",

    # ---------- ayarlar ekrani (alan etiketleri semadan gelir) ----------
    "settings.saved": "Kaydedildi",
    "settings.saved_mock": "Kaydedildi — mod 'mock' olduğu için GLS ayarları uygulanmadı",
    "settings.unknown_section": "Bilinmeyen ayar bölümü: {section}",
    "settings.secret_saved": "Kayıtlı",
    "settings.secret_empty": "Boş",
    "settings.secret_ph": "Kayıtlı — değiştirmek için yazın",
    "settings.secret_clear": "sil",
    "settings.test_saved": "Kaydedilen ayarlarla dene",
    "settings.test_connection": "Bağlantıyı dene (kaydedilen ayarlarla)",
    "settings.test_dirty": "Test kaydedilmiş değerlerle çalışır — önce Kaydet'e basın.",
    "settings.test_error": "HATA",
    "settings.mock_blocked": "Mod: <strong>mock</strong> — buradaki değerler kaydedilir ama "
                             "<strong>uygulanmaz</strong>. Gerçek GLS sunucularına istek gitmez.",
    "settings.mail_missing": "Mail kimliği eksik — önce "
                             "<a href=\"/settings/mail\" class=\"underline font-semibold\">Mail</a> "
                             "sekmesindeki Tenant/Client/Secret alanlarını doldurun. O olmadan "
                             "özet gönderilemez.",
    "settings.mail_verify": "Kimlik doğrula",
    "settings.mail_verify_note": "Bu düğme yalnızca belirteç alır — <strong>mail göndermez</strong>.",
    "settings.notify_send_now": "Özeti şimdi gönder",
    "settings.notify_dirty": "Gönderim kaydedilmiş değerlerle çalışır — önce Kaydet'e basın.",
    "settings.notify_last_run": "Son çalışma",
    "settings.wa_status": "Durum",
    "settings.wa_test_soon": "Test mesajı: sağlayıcı seçildikten sonra",
    "settings.wa_no_provider": "Sağlayıcı seçilmedi",
    "settings.wa_incomplete": "{label} seçildi — hesap no ve belirteç eksik",
    "settings.wa_ready": "{label} kimlik bilgileri kayıtlı (gönderim henüz yok)",
    # baglanti testi sonuclari (Ayarlar > Test dugmeleri)
    "check.not_configured": ".env'de yapılandırılmamış",
    "check.fields_missing": "Alanlar eksik — önce kaydedin",
    "check.ok": "bağlantı ve kimlik doğrulama başarılı",
    "check.ok_test_address": "bağlantı ve kimlik doğrulama başarılı "
                             "(test adresi reddedildi, normal)",
    "check.ok_test_ref": "bağlantı ve kimlik doğrulama başarılı "
                         "(test referansı bulunamadı, normal)",
    "check.shipit_auth": "kimlik reddedildi — SHIPIT_USERNAME/SHIPIT_PASSWORD hatalı",
    "check.nl_auth": "kimlik reddedildi — NL_USERNAME/NL_PASSWORD hatalı",
    "check.fedex_auth": "kimlik reddedildi — FEDEX_API_KEY/FEDEX_API_SECRET hatalı",
    "check.tt_auth": "kimlik reddedildi — TT_USERNAME/TT_PASSWORD gls-group.eu "
                     "Uni-Portal kullanıcısı olmalı (ShipIT kimliği değil)",
    "check.nl_no_customer": "kimlik doğrulandı ama hesapta müşteri numarası yok — GLS'e sorun",
    "check.nl_ok": "bağlantı başarılı — müşteri no: {nos}",
    "check.glsg_auth": "token alındı ama Track & Trace v1 bu App ID'ye açık değil "
                       "(ya da GLSG_CLIENT_ID/GLSG_CLIENT_SECRET hatalı)",
    "check.glsg_ok": "bağlantı ve kimlik doğrulama başarılı — {count} olay kodu okunabiliyor",
    "check.mssql_ok": "bağlantı başarılı — {count} görünüm okunabiliyor: {views}",
    "check.mail_ok_no_sender": "Kimlik doğrulandı — gönderen adresi henüz girilmedi",
    "check.mail_ok": "Kimlik doğrulandı (gönderen: {sender})",
    "check.mail_missing": "Kiracı/uygulama/gizli anahtar eksik — önce kaydedin",
    "check.digest_title": "Günlük özet",
    "check.digest_off": "Zamanlayıcı çalışmıyor",
    # mail gonderimi (Graph)
    "mail.creds_missing": "Tenant ID / Client ID / Client Secret alanlarını doldurun",
    "mail.no_connection": "Bağlantı kurulamadı: {detail}",
    "mail.authenticated": "Kimlik doğrulandı",
    "mail.rejected": "Reddedildi ({status}): {detail}",
    "mail.no_recipients": "Alıcı listesi boş",
    "mail.no_sender": "Gönderen adresi (MAIL_SENDER) girilmedi",
    "mail.mock": "mock — gönderilmedi ({count} alıcı)",
    "mail.attachments_too_big": "Ekler çok büyük ({size} KB) — Graph sınırı {limit} KB",
    "mail.timeout": "Yanıt gelmedi (zaman aşımı) — mail gitmiş olabilir, "
                    "tekrar göndermeden önce gelen kutusunu kontrol edin",
    "mail.send_failed": "Gönderilemedi: {detail}",
    "mail.sent": "Gönderildi ({count} alıcı)",
    "mail.send_failed_status": "Gönderilemedi ({status}): {detail}",
    # ice aktarim / ERP hatalari
    'imp.row': 'Satır {row}: {detail}',
    'imp.excel_unreadable': 'Excel okunamadı: {detail}',
    'imp.missing_columns': 'Zorunlu sütun(lar) bulunamadı: {missing}. Dosyadaki sütunlar: {found}',
    'imp.csv_encoding': 'CSV kodlaması tespit edilemedi: {path}',
    'imp.xlsx_needs_pandas': 'XLSX için pandas gerekli.',
    'imp.bad_json': "JSON gövdesi bir liste ya da {{'addresses': [...]}} olmalı",
    'erp.bad_identifier': 'Geçersiz tablo/görünüm adı: {name}',
    'erp.no_pymssql': 'pymssql kurulu değil (pip install -r requirements.txt)',
    'erp.not_configured': "MSSQL yapılandırması eksik — .env'de MSSQL_* değerlerini doldurun",
    'erp.no_connection': 'MSSQL bağlantısı kurulamadı ({server}:{port}): {detail}',
    'erp.read_only': 'Bu istemci salt okunurdur; yalnızca SELECT/WITH çalıştırılabilir',
    'erp.query_failed': 'MSSQL sorgusu başarısız: {detail}',
    'erp.write_not_allowed': 'İzin verilmeyen yazma ifadesi',
    'erp.update_failed': 'ERP güncellemesi başarısız: {detail}',
    # ayar dogrulama hatalari
    "validate.required": "{label}: boş bırakılamaz",
    "validate.not_number": "{label}: sayı olmalı ('{value}' verildi)",
    "validate.min": "{label}: en az {minimum} olmalı",
    "validate.max": "{label}: en fazla {maximum} olabilir",
    # kullanici rolleri (auth/db.py:ROLES) — sablonlarda `t_or` ile cagrilir,
    # cevirisi olmayan bir rol ham adiyla gorunur.
    "role.admin": "Yönetici",
    "role.operator": "Operatör",
    "role.viewer": "İzleyici",
    # sunucu hata mesajlari (HTTPException) — kullanici bunlari ekranda gorur
    "err.forbidden": "Bu bölüm için yetkiniz yok.",
    "err.parcel_not_found": "Kargo bulunamadı",
    "err.parcel_not_found_no": "Kargo bulunamadı: {no}",
    "err.address_not_found": "Adres bulunamadı",
    "err.user_not_found": "Kullanıcı bulunamadı",
    "err.mssql_not_configured": "MSSQL yapılandırılmamış (.env: MSSQL_*)",
    "err.excel_only": "Yalnızca Excel dosyaları desteklenir ({exts}).",
    "err.supported_formats": "Desteklenen biçimler: {exts}",
    "err.file_empty": "Dosya boş.",
    "err.file_too_large": "Dosya çok büyük (en fazla {limit_mb} MB).",
    "err.file_unreadable": "Dosya okunamadı: {detail}",
    "err.pod_failed": "POD alınamadı ({no}): {detail}",
    "err.box_image_none_valid": "Geçerli bir resim dosyası bulunamadı (PNG, JPG, BMP veya WEBP olmalı).",
    "err.box_image_pdf_failed": "Koli görseli PDF'i üretilemedi (sunucuda uygun font bulunamadı).",
    "err.box_image_exists": "Bu koli için zaten fotoğraf yüklenmiş. Değiştirmek için önce “Kaldır” deyin.",
    "err.box_image_not_found": "Bu koli için henüz koli görseli yüklenmemiş.",
    "err.pod_no_provider": "POD alınamadı ({no}): {channel} kanalı için POD "
                           "sağlayıcısı yapılandırılmamış (.env).",
    "err.invalid_tracking_no": "Geçersiz takip numarası: {nos}",
    "err.select_at_least_one": "En az bir kargo seçmelisiniz",
    "err.invalid_role": "Geçersiz rol",
    "err.username_taken": "Bu kullanıcı adı zaten kayıtlı",
    "err.cannot_disable_self": "Kendi hesabınızı devre dışı bırakamazsınız",
    "err.cannot_delete_self": "Kendi hesabınızı silemezsiniz",
    "err.cannot_change_own_role": "Kendi rolünüzü değiştiremezsiniz",
    "err.last_admin": "Son yönetici hesabı devre dışı bırakılamaz, silinemez veya "
                      "rolü düşürülemez",
    "err.invalid_email": "Geçersiz e-posta adresi",
    # kullanicilar
    "users.count": "{count} kayıt",
    "users.new": "+ Yeni Kullanıcı",
    "users.password_ph": "Şifre (min 8)",
    "users.create": "Oluştur",
    "users.col.full_name": "Ad Soyad",
    "users.col.role": "Rol",
    "users.col.created": "Oluşturulma",
    "users.active": "Aktif",
    "users.inactive": "Devre dışı",
    "users.deactivate": "Devre dışı bırak",
    "users.activate": "Aktifleştir",
    "users.confirm_toggle": "Durum değiştirilsin mi?",
    "users.email": "E-posta",
    "users.no_email": "E-posta yok — bu hesap şifresini kendi sıfırlayamaz.",
    "users.edit": "Düzenle",
    "users.save": "Kaydet",
    "users.set_password": "Şifre ata",
    "users.delete": "Kullanıcıyı sil",
    "users.confirm_delete": "Bu kullanıcı tamamen silinecek. Emin misiniz?",
    "users.delete_hint": "Hesap kalıcı olarak silinir; giriş kayıtları denetim izi "
                         "olarak kalır.",
    "users.mail_not_ready": "E-posta gönderimi yapılandırılmamış (Ayarlar → Bildirim). "
                            "Şifre sıfırlama maili gitmez; şifreyi buradan elle atayın.",
    "users.panel_url_missing": "Panel adresi (NOTIFY_PANEL_URL) boş. Sıfırlama bağlantısı "
                               "üretilemez — Ayarlar → Bildirim bölümünden girin.",
    "users.session_note": "Rol değişikliği ve devre dışı bırakma açık oturumlara en geç "
                          "20 dakika içinde yansır.",

    # ---------- Excel eki ----------
    # Sutun BASLIKLARI cevrilmez: elle tutulan "FA26 TRACKING LIST.xlsx" ile
    # birebir ayni kalmali (bkz. notify/excel.py).
    "excel.sheet.all": "Tüm Liste",
    "excel.sheet.delivered": "Teslim Edilenler",
    "excel.sheet.problems": "Sorunlular",
    "excel.flag.delivered": "Bugün teslim",
    "excel.flag.not_handed_over": "GLS teslim almadı",
    "excel.flag.pod_missing": "POD eksik",
    "excel.flag.stale": "{days}+ gündür yolda",
    "excel.flag.partial": "Kısmi teslimat",
    "excel.flag.damaged": "HASARLI",
    "excel.flag.exception": "SORUNLU",
    "excel.flag.returned": "İADE",
    "excel.filename": "Takip_Listesi",

    # ---------- tarama ariza uyarisi ----------
    # Ozet mailinden ayri: ozet kapaliyken de gider (bkz. notify/alert.py).
    "alert.tracker.subject": "GLS takip DURDU — {hours} saattir başarılı tur yok",
    "alert.tracker.headline": "Takip taraması {hours} saattir başarıyla tamamlanamıyor. "
                              "Panel açık olsa da kargo durumları güncellenmiyor.",
    "alert.whatsapp.banner": "WhatsApp bildirimleri GİTMİYOR — OpenWA oturumu bağlı değil.",
    "alert.whatsapp.detail": "Oturum durumu: {status}. Bildirimlerin tekrar akması için OpenWA arayüzünden QR kodunu telefondan yeniden okutun.",
    "alert.tracker.banner": "Takip taraması {hours} saattir çalışmıyor — "
                            "bu sayfadaki kargo durumları güncel DEĞİL.",
    "alert.tracker.last_success": "Son başarılı tur",
    "alert.tracker.last_run": "Son deneme",
    "alert.tracker.last_error": "Son hata",
    "alert.tracker.footer": "Bu uyarı arıza başına bir kez gönderilir; "
                            "tarama düzelince otomatik susar.",

    # ---------- gunluk ozet maili ----------
    # Bazi degerler HTML icerir (kalin/renkli vurgu) ve sablonda `|safe` ile
    # basilir — kaynak bu dosya, disaridan veri girmiyor.
    # Basliktaki "GLS" sablonda sari renklidir — bu yuzden metnin disinda.
    "mail.title": "Günlük Durum Özeti",
    "mail.subject": "{system} · GLS {season}günlük özet {day} — "
                    "{delivered} teslim, {problems} sorun",
    "mail.card.total": "TOPLAM",
    "mail.card.in_transit": "YOLDA",
    "mail.card.out_for_delivery": "DAĞITIMDA",
    "mail.card.delivered": "TESLİM",
    "mail.card.exception": "SORUNLU",
    "mail.card.returned": "İADE",
    "mail.summary.season": "<strong>{season}</strong> sezonunda <strong>{total} gönderiden "
                           "{delivered} tanesi</strong> teslim edildi (%{pct}), "
                           "{on_the_way} tanesi yolda.",
    "mail.summary.total": "Toplam <strong>{total} gönderiden {delivered} tanesi</strong> "
                          "teslim edildi (%{pct}), {on_the_way} tanesi yolda.",
    "mail.summary.today": "Son 24 saatte <strong style=\"color:#059669;\">{count} teslimat"
                          "</strong> gerçekleşti.",
    "mail.summary.exceptions": "GLS <strong style=\"color:#e11d48;\">{count} kargoda sorun"
                               "</strong> bildirdi.",
    "mail.summary.no_exceptions": "GLS'in sorun bildirdiği kargo yok.",
    "mail.summary.stale": "<strong style=\"color:#d97706;\">{count} kargo</strong> {days}+ "
                          "gündür GLS'te ve hâlâ teslim edilmedi.",
    "mail.summary.partial": "<strong style=\"color:#d97706;\">{count} koli</strong>, aynı "
                            "faturanın diğer kolileri teslim edilmiş olmasına rağmen hâlâ "
                            "ulaşmadı.",
    "mail.summary.not_handed_over": "{count} kolinin etiketi basıldı ama koli hâlâ GLS'e "
                                    "teslim edilmedi.",
    "mail.summary.pod_missing": "{count} teslimatın POD'u henüz alınamadı.",
    "mail.today.heading": "{day} — {count} teslimat",
    "mail.today.more": "+{count} daha",
    "mail.today.none": "Bugün teslim edilen kargo yok.",
    "mail.today.pods": "{count} teslimatın POD belgesi (PDF) <strong>ekteki zip</strong> "
                       "içinde — hepsi arşivde ve panelde de duruyor.",
    "mail.problems.heading": "{count} kargo elle müdahale bekliyor",
    "mail.problems.breakdown": "{exception} sorunlu &middot; {damaged} hasarlı "
                               "&middot; {partial} kısmi teslimat "
                               "&middot; {pod_missing} POD eksik &middot; {stale} takıldı "
                               "&middot; {not_handed_over} GLS teslim almadı "
                               "&middot; {returned} iade",
    "mail.excel.note": "<strong>Ekteki Excel</strong> sezonun tam listesidir — {rows} satır. "
                       "Teslim edilenler yeşil boyalı. İkinci sayfa yalnızca teslim edilenler, "
                       "üçüncü sayfa sorunlular ({problems} kayıt).",
    "mail.excel.none": "Ekte listelenecek kargo yok.",
    "mail.open_panel": "Takip Sistemini Aç",
    "mail.footer": "Bu mail GLS Gönderi Takip Sistemi tarafından otomatik gönderilmiştir. "
                   "Gönderim saatini ve alıcıları Ayarlar &rsaquo; Bildirimler bölümünden "
                   "değiştirebilirsiniz.",
}


# ---------- ayarlar: semadan uretilir ----------
def _from_schema() -> dict[str, str]:
    out = {f"settings.{slug}.title": title for slug, title in TABS}
    for section in SECTIONS:
        if section.note:
            out[f"settings.{section.slug}.note"] = section.note
        for f in section.fields:
            out[f"settings.field.{f.key}"] = f.label
            if f.help:
                out[f"settings.help.{f.key}"] = f.help
            for value, label in f.choices:
                out[f"settings.choice.{f.key}.{value}"] = label
    return out


STRINGS.update(_from_schema())
