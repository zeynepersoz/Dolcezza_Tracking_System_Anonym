"""Ana panel: statu ozet + haftalik grafik + top ulkeler.

Sales rep siralamasi ve nakliye tutari KALDIRILDI: iki alan da yalnizca eski Excel
ice aktarimindan geliyordu, veri kaynagi MSSQL olunca kalici bos kaldi (panelde
"Sales rep verisi yok" ve "€0.00" gorunuyordu).
"""
from fastapi import APIRouter, Request

import timez
from tracking.db import STATUS_CHOICES, STATUS_COLOR, status_label
from tracking.delivery_report import build_chart_data
from web.routers.tracking import season_options
from web.templating import templates
from web import deps

router = APIRouter()


# Ust seritteki kutularin KANAL kirilimi (kullanici istegi 2026-09-03:
# "icerisini gls nl fedex ve ie diye ayir"). Sira ekranda gorunen siradir;
# etiketler kisa cunku kutunun altinda tek satira sigmalari gerekiyor.
# Etiketler ERP'nin yazdigi tasiyici adlariyla AYNI (bkz.
# erp/sync.py:CARRIER_CHANNELS) — panelde ve Sentez'de ayni sey ayni
# adla gorunsun.
CHANNEL_CARDS = (("NL", "GLS-NL"), ("IE", "GLS-IE"), ("FEDEX", "FEDEX"))


def _status_flow(counts: dict, total: int, breakdown: dict) -> list[dict]:
    """Durum kutulari: {code, label, color, count, pct, href, channels}.

    created, in_transit, out_for_delivery, delivered, exception ve returned
    kutulari daima panelin ust seridinde sabit durur (0 olsa bile sayaciyla cizilir).
    cancelled ise yalnizca sayisi > 0 ise eklenir.

    `channels` her kutunun altindaki kanal kirilimidir ve FedEx'i ust seritten
    TUMDEN dislayan eski cozumun yerine gecti (2026-08-31 gerekcesi: "olcek cok
    farkli, GLS'in ozetini bozar"). Artik FedEx sayiya dahil ama kendi
    satirinda ayri durdugu icin GLS hacmini gizlemiyor — kullanici ikisini bir
    arada gormek istedi.
    """
    visible_codes = [s for s in STATUS_CHOICES if s != "cancelled" or counts.get(s, 0)]
    return [
        {
            "code": code,
            "label": status_label(code),
            "color": STATUS_COLOR.get(code, "slate"),
            "count": counts.get(code, 0),
            "pct": round(counts.get(code, 0) / total * 100, 1) if total else 0.0,
            "href": f"/tracking?status={code}",
            "channels": [
                {"code": ch, "label": label,
                 "count": breakdown.get(ch, {}).get(code, 0),
                 "href": f"/tracking?status={code}&channel={ch}"}
                for ch, label in CHANNEL_CARDS
            ],
        }
        for code in visible_codes
    ]


@router.get("/")
def dashboard(request: Request):
    db = deps.get_shipments_db()
    # FedEx 2026-08-31'de ust seritten TUMDEN dislanmisti ("olcek cok farkli").
    # 2026-09-03'te kullanici kutularin KANAL KIRILIMLI olmasini istedi; kirilim
    # ayni sorunu daha iyi cozuyor (FedEx kendi satirinda gorunuyor, GLS
    # hacmini gizlemiyor), bu yuzden dislama kaldirildi.
    counts = db.counts_by_status()
    exceptions = db.recent_exceptions(5)
    total = sum(counts.values())
    channel_breakdown = db.channel_breakdown()
    top_countries = db.top_countries(6)
    weekly = db.weekly_series(8)
    week = db.week_summary()
    problems = db.problem_counts()

    # "Bugun" kullanicinin gunu; konteyner UTC'de calisiyor (bkz. timez).
    today = timez.now().date().isoformat()

    # Son tur bilgisi DISKTEN (`tracker_health`), tarayici nesnesinden DEGIL:
    # nesnenin oznitelikleri her yeniden baslatmada sifirlaniyor, kart da her
    # aktarimdan sonra saatlerdir calisan bir sistem icin "Henüz çalışmadı"
    # diyordu. Kirmizi serit ve `/health` zaten ayni satiri okuyor.
    health = db.health()
    tracker = getattr(request.app.state, "tracker", None)
    tracker_info = {
        "last_run_at": health.get("last_run_at"),
        "last_error": health.get("last_error"),
        # Aralik durum degil ayar; tarayici yoksa varsayilana duseriz.
        "interval": getattr(tracker, "interval_minutes", 15) if tracker else 15,
    }

    ctx = {
        "request": request,
        "counts": counts,
        "total": total,
        "flow": _status_flow(counts, total, channel_breakdown),
        "exceptions": exceptions,
        "channel_breakdown": channel_breakdown,
        "top_countries": top_countries,
        "weekly": weekly,
        "week": week,
        "problems": problems,
        "today": today,
        "delivered_today": db.delivered_on(today),
        # Kullanici sayiyi degil LISTEYI gormek istiyor: panel acilir acilmaz
        # bugun ne teslim edildi, gun icinde ne teslim edilecek — takip no,
        # fatura, magaza dahil. Tamami icin kartin ustundeki bag /tracking'e.
        "delivered_today_rows": db.list_parcels(delivered_day=today, limit=10),
        "out_for_delivery_rows": db.list_parcels(status=["out_for_delivery"],
                                                 limit=10),
        # Hizli aramanin yanindaki sezon kutusu; takip sayfasiyla ayni kaynak.
        "season_options": season_options(db),
        "tracker_info": tracker_info,
        # /reports'un mini grafik surumu (kullanici istegi, 2026-08-31): ERP
        # urun-adedi sorgusu YOK, o yuzden /reports'tan cok daha hizli.
        "delivery_chart": build_chart_data(db),
        # ParcelShop'ta bekleyip HENUZ alinmayan (ya da iadeye donmeyen)
        # koliler — kullanici istegi (2026-08-31): "dashboarda delivered to
        # parcel shop uyarısı olan ayrı bir kutu ekleyelim". Yalnizca RISKTEKI
        # satirlar (bkz. ShipmentsDB.parcel_shop_parcels): musteri henuz
        # almadi, GLS de henuz iadeye dondurmedi — asil dikkat gerektiren bu.
        # Dashboard GLS-NL odakli (kullanici karari 2026-08-31); ParcelShop
        # karti da NL. IE/FEDEX kirilimi /reports sekmelerinde.
        "parcel_shop_at_risk": [
            r for r in db.parcel_shop_parcels("NL") if r["at_risk"]
        ][:8],
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "dashboard.html", ctx)
