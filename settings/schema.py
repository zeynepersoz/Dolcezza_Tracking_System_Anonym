# -*- coding: utf-8 -*-
"""
Panelden düzenlenebilir ayarların TEK kaynağı.

Buradaki her `Field.key` aynı zamanda `gls_api/config.py`'deki bir öznitelik adıdır —
`settings/apply.py` değerleri o adla config'e iter. İkisi ayrışırsa
`test_schema_keys_match_env_defaults` derhal patlar; `config.__getattr__` sessizce
`AttributeError` verdiği için bu testi silmeyin.

Buraya EKLENMEYEN hiçbir anahtar panelden yazılamaz (`store.save_section` beyaz liste
uygular). `AUTH_*`, `GLS_MODE` ve `OUTPUT_DIR` bilerek dışarıdadır —
gerekçeler `docs`/plan dosyasında.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str = "text"          # text | int | csv | select
    secret: bool = False
    help: str = ""
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[tuple[str, str], ...] = ()   # (deger, etiket)


@dataclass(frozen=True)
class Section:
    slug: str
    title: str
    fields: tuple[Field, ...] = field(default_factory=tuple)
    note: str = ""
    # Bolumu gorebilen roller. VARSAYILAN KAPALI (yalnizca admin) — ileride
    # eklenen bir bolum kazara operatore acilmasin diye.
    roles: tuple[str, ...] = ("admin",)


SECTIONS: tuple[Section, ...] = (
    Section(
        slug="mssql",
        title="MSSQL Bağlantısı",
        note="Takip numaralarının birincil kaynağı. Geri yazma kapalıyken erişim "
             "salt okumadır; açıkken yalnızca erp_Box'ın üç taşıma sütunu güncellenir.",
        fields=(
            Field("MSSQL_SERVER", "Sunucu", help="Örn. 192.0.2.30"),
            Field("MSSQL_PORT", "Port", type="int", minimum=1, maximum=65535),
            Field("MSSQL_DATABASE", "Veritabanı", help="Örn. EMCLiveVogue"),
            Field("MSSQL_USERNAME", "Kullanıcı"),
            Field("MSSQL_PASSWORD", "Şifre", secret=True),
            Field("MSSQL_SEASON_VIEWS", "Sezon görünümleri", type="csv",
                  help="Virgülle ayrılmış, sırayla işlenir. Yeni sezon sona eklenir."),
            Field("ERP_WRITEBACK_ENABLED", "ERP'ye geri yaz", type="select",
                  choices=(("0", "Kapalı"), ("1", "Açık")),
                  help="Her takip turundan sonra erp_Box'ın taşıma durumu, açıklama "
                       "ve teslim tarihi sütunları güncellenir. Yalnızca değişen "
                       "koliler yazılır."),
        ),
    ),
    Section(
        slug="ui",
        title="Arayüz",
        note="Panelin varsayılan dili. Kullanıcı üst çubuktaki TR|EN anahtarıyla "
             "kendi tarayıcısı için değiştirebilir; günlük özet maili ve Excel eki "
             "buradaki dili kullanır.",
        fields=(
            Field("UI_LANG", "Dil", type="select", choices=(
                ("tr", "Türkçe"),
                ("en", "English"),
            )),
        ),
    ),
    Section(
        slug="tracking",
        title="Takip ve POD",
        note="Tur başına tavanlar GLS'in saatlik istek sınırını korur.",
        fields=(
            Field("TRACKER_INTERVAL_MINUTES", "Tarama aralığı (dk)", type="int",
                  minimum=1, maximum=1440,
                  help="0 olamaz — sıfır aralık GLS'i aralıksız döverdi."),
            Field("TRACKER_MAX_PER_TICK", "Tur başına parça", type="int",
                  minimum=1, maximum=5000,
                  help="NL takip ucu 20'şerli gruplar alır."),
            Field("TRACKER_MAX_POD_PER_TICK", "Tur başına POD", type="int",
                  minimum=1, maximum=500,
                  help="POD toplu sorgulanamaz; parça başına ayrı istek gider."),
            Field("ERP_SYNC_INTERVAL_MINUTES", "ERP'den çekme aralığı (dk)",
                  type="int", minimum=0, maximum=1440,
                  help="Sentez'den yeni kolilerin panele alınma sıklığı. "
                       "Tarama aralığının aksine 0 = kapalı olabilir (MSSQL'siz "
                       "kurulumda anlamsızdır); o zaman yalnızca Takip "
                       "sayfasındaki “MSSQL'den Yenile” düğmesi çalışır."),
        ),
    ),
    Section(
        slug="gls",
        title="GLS Sağlayıcıları",
        note="Hangi kanalın açık olduğu müşteriden müşteriye değişir; dolu olan kullanılır.",
        fields=(
            Field("SHIPIT_BASE_URL", "ShipIT taban URL", help="Müşteriye özeldir, GLS verir."),
            Field("SHIPIT_USERNAME", "ShipIT kullanıcı"),
            Field("SHIPIT_PASSWORD", "ShipIT şifre", secret=True),
            Field("SHIPIT_CONTACT_ID", "ShipIT ContactID",
                  help="'<müşteri no> <depo kodu>' biçiminde. Yalnızca etiket için gerekir."),
            Field("TT_ENDPOINT", "Track&Trace uç noktası"),
            Field("TT_USERNAME", "Track&Trace kullanıcı",
                  help="gls-group.eu Uni-Portal kullanıcısı — ShipIT kimliği DEĞİL."),
            Field("TT_PASSWORD", "Track&Trace şifre", secret=True),
            Field("NL_BASE_URL", "GLS NL etiket URL"),
            Field("NL_USERNAME", "GLS NL kullanıcı", help="Etiket ve takip için aynı."),
            Field("NL_PASSWORD", "GLS NL şifre", secret=True,
                  help="En fazla 20 karakter — GLS NL uzun şifreyi kabul etmez."),
            Field("NL_CUSTOMER_NO", "GLS NL müşteri no",
                  help="Boş bırakılırsa ValidateLogin'den otomatik çözülür."),
            Field("NL_TT_BASE_URL", "GLS NL takip URL", help="Etiketten AYRI host."),
            Field("NL_POD_BASE_URL", "GLS NL POD URL",
                  help="Üçüncü ayrı host; bu uç kimlik istemez."),
            Field("GLSG_TOKEN_URL", "GLS Grup token URL",
                  help="OAuth 2.0. Canlı: https://api.gls-group.net/oauth2/v2/token"),
            Field("GLSG_BASE_URL", "GLS Grup geçit URL",
                  help="Track & Trace v1'in host'u. Canlı: https://api.gls-group.net"),
            Field("GLSG_CLIENT_ID", "GLS Grup App Key",
                  help="dev-portal.gls-group.net'te açılan App'in API Key'i."),
            Field("GLSG_CLIENT_SECRET", "GLS Grup App Secret", secret=True),
            Field("GLSG_SCOPE", "GLS Grup scope",
                  help="Genelde 'all'. Boş bırakılırsa scope hiç gönderilmez."),
            Field("GLSG_DAILY_LIMIT", "GLS Grup günlük istek tavanı",
                  type="int", minimum=1, maximum=1000000,
                  help="GLS'in verdiği kota 500/gün (istek başına 10 parça). "
                       "Kota dolunca IE taraması o gün durur, ertesi gün kaldığı "
                       "yerden devam eder. GLS kotayı yükseltirse burayı artırın."),
        ),
    ),
    Section(
        slug="notify",
        title="Bildirimler",
        note="Belirlenen her saatte iç ekibe tek mail: yönetici özeti + ekte "
             "ham Excel. Mail kimliği için önce Mail sekmesini doldurun.",
        # Operatorler de gorur: ekip istedigi zaman ozeti elle gonderebilsin.
        roles=("admin", "operator"),
        fields=(
            Field("NOTIFY_ENABLED", "Günlük özet", type="select", choices=(
                ("0", "Kapalı"),
                ("1", "Açık"),
            )),
            Field("NOTIFY_RECIPIENTS", "Alıcılar", type="csv",
                  help="Virgülle ayrılmış e-posta adresleri. Yalnızca iç ekip — "
                       "müşteriye/ajansa gitmez."),
            Field("NOTIFY_HOURS", "Gönderim saatleri", type="csv", minimum=0, maximum=23,
                  help="Virgülle ayrılmış saatler, Türkiye saatiyle, dakika 00. "
                       "Örn. “9,18” — sabah bir önceki günün kapanışı, akşam gün "
                       "içinde teslim edilenler."),
            Field("NOTIFY_STALE_DAYS", "Hareketsiz sayılma süresi (gün)", type="int",
                  minimum=1, maximum=90,
                  help="Bu süredir yeni olayı olmayan kargolar özete girer."),
            Field("NOTIFY_PANEL_URL", "Panel adresi",
                  help="Maildeki düğmelerin ve şifre sıfırlama bağlantısının kökü. "
                       "Kullanıcının tarayıcısından açılabilen adres olmalı — iç ağ "
                       "adresi yazılırsa dışarıdan gelen sıfırlama bağlantısı ölü "
                       "olur. Örn. https://tracking.dolcezza.tr"),
        ),
    ),
    Section(
        slug="gls_inquiry",
        title="POD gelmedi, GLS'e sor",
        note="Bu mail DIŞARIYA, GLS müşteri hizmetlerine gider. Teslim edilip "
             "teslimat kanıtı gelmeyen kargolar için, bekleme süresi dolduktan "
             "sonra günlük özetle aynı saatte yazılır. Bir kargo yalnızca BİR KEZ "
             "sorulur. Alıcılar (Bildirimler sekmesi) bilgi (CC) olarak eklenir.",
        # Bildirimler'le birlikte operatorde: otomatik mailler tek ekip isi.
        roles=("admin", "operator"),
        fields=(
            Field("GLS_INQUIRY_ENABLED", "Otomatik sorgu", type="select", choices=(
                ("0", "Kapalı"),
                ("1", "Açık"),
            )),
            Field("GLS_INQUIRY_RECIPIENTS", "GLS adresi", type="csv",
                  help="Virgülle ayrılmış. Varsayılan: klantenservice@gls-netherlands.com"),
            Field("GLS_INQUIRY_WAIT_DAYS", "Teslimattan sonra beklenecek gün",
                  type="int", minimum=1, maximum=90,
                  help="POD teslimattan saatler sonra oluşabiliyor; erken sorulmaz."),
        ),
    ),
    Section(
        slug="mail",
        title="Mail (Microsoft Graph)",
        note="Günlük özetin gönderim kimliği. Test düğmesi mail göndermez, yalnızca "
             "kimliği doğrular.",
        fields=(
            Field("MAIL_TENANT_ID", "Tenant ID"),
            Field("MAIL_CLIENT_ID", "Client ID"),
            Field("MAIL_CLIENT_SECRET", "Client Secret", secret=True),
            Field("MAIL_SENDER", "Gönderen adresi",
                  help="Azure uygulamasının Mail.Send izni olan posta kutusu. "
                       "Gelen kutusunda görünen ad bu kutunun M365'teki adıdır — "
                       "buradan değiştirilemez."),
        ),
    ),
    Section(
        slug="whatsapp",
        title="WhatsApp",
        note="OpenWA sunucusu üzerinden teslimat ve sorun şablon mesajları gönderir.",
        fields=(
            Field("WA_PROVIDER", "Sağlayıcı", type="select", choices=(
                ("", "— seçilmedi —"),
                ("openwa", "OpenWA"),
                ("meta", "Meta Cloud API"),
                ("twilio", "Twilio"),
                ("360dialog", "360dialog"),
            )),
            Field("OPENWA_ENABLED", "WhatsApp Bildirimleri", type="select", choices=(
                ("1", "Açık"),
                ("0", "Kapalı"),
            )),
            Field("OPENWA_URL", "OpenWA Sunucu URL"),
            Field("OPENWA_SESSION_ID", "Session ID"),
            Field("OPENWA_API_KEY", "API Key", secret=True),
            Field("OPENWA_CHAT_ID", "Hedef Grup Chat ID"),
            Field("OPENWA_START_DATE", "Başlangıç Tarihi"),
        ),
    ),
)

# Sekme seridi: ayar bolumleri + kullanicilar ve guvenlik. Son ikisinin ayar
# alani yoktur (kayit yonetimi), bu yuzden SECTIONS'ta degiller; sayfalarini
# `web/routers/settings.py` ayri ayri doner.
TABS: tuple[tuple[str, str], ...] = tuple(
    [(s.slug, s.title) for s in SECTIONS]
    + [("users", "Kullanıcılar"), ("security", "Güvenlik")]
)

BY_SLUG: dict[str, Section] = {s.slug: s for s in SECTIONS}

ALL_FIELDS: dict[str, Field] = {f.key: f for s in SECTIONS for f in s.fields}

# GLS kimlikleri mock modda UYGULANMAZ (saklanir ama devreye girmez): aksi halde
# bir gelistirme makinesi ya da CI canli api.gls.nl'e istek atardi.
GLS_KEYS: frozenset[str] = frozenset(
    f.key for f in BY_SLUG["gls"].fields
)
