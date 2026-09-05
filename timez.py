"""Saat dilimi: veriyi UTC depola, kullaniciya Istanbul saatiyle goster.

NEDEN BU MODUL VAR: konteyner UTC calisiyor (`docker-compose.yml`'de `TZ` yok),
kullanici ise UTC+3'te. Panelde her saat 3 saat geride gorunuyordu.

EN KOLAY COZUM YANLIS: konteynere `TZ=Europe/Istanbul` vermek veriyi bozar.
Kod bugun `datetime.now()` ile `datetime.utcnow()` degerlerini BIRBIRIYLE
karsilastiriyor (ornek: `notify/digest.py` `since` penceresi, `tracking/
scheduler.py` saglik damgasi) ve UTC konteynerde ikisi ayni oldugu icin bu
sessizce calisiyor. `TZ` verilseydi `now()` 3 saat kayar, `utcnow()` kalirdi:
gunluk ozet yanlis teslimat listesi gonderir, tarayici sahte alarm verirdi.

Bu yuzden bolunme sudur:
    DEPOLAMA  -> `utc_iso()`  (naive UTC, bugunku `_now()` ile birebir ayni sekil)
    GOSTERIM  -> `stamp()` / `local()`  (Europe/Istanbul)

Turkiye 2016'dan beri yaz saati uygulamiyor (sabit UTC+3), yine de `ZoneInfo`
kullanilir: 2016 oncesi damgalar ve ileride olabilecek bir degisiklik icin
dogru olan budur.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")

# Panelde, ERP aciklamasinda ve Excel'de gorunen ortak sekil.
STAMP_FMT = "%d.%m.%Y %H:%M"


def parse(value: str | datetime | None) -> datetime | None:
    """Metni saat dilimi BILINEN (UTC) bir `datetime`a cevirir. Bozuksa `None`.

    Kabul edilen sekiller — hepsi canli veritabaninda mevcut:
        `2026-08-14T19:48:42.724Z`   GLS, salise ekli
        `2026-07-31T09:33:47Z`       GLS, salisesiz
        `2026-08-19T09:04:59`        bizim `_now()`; naive -> UTC SAYILIR
        `2026-08-19 09:04:59`        SQLite `datetime()` ciktisi
        `2026-08-19T09:04:59+00:00`  `tracker_health.last_run_at`

    YALNIZ TARIH (`2026-08-19`) `None` doner. `shipment_date` gibi alanlar gun
    bilgisidir; saat cevirisi uygulanirsa gun kayar. Cagiran taraf `None`
    gorunce metni oldugu gibi basar.

    Ayristirma elle yapilir: `datetime.fromisoformat` GLS'in `Z` ekini
    Python 3.10'da kabul etmiyor.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(
            tzinfo=timezone.utc)

    text = (value or "").strip()
    if not text:
        return None
    date, sep, time = text.partition("T")
    if not sep:
        date, sep, time = text.partition(" ")
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        return None

    # Saat kismini eklerinden ayikla: `Z`, salise, `+03:00` gibi bir kaydirma.
    # Kaydirma varsa GERCEKTEN uygulanir — koru bir `[:8]` kirpmasi
    # `…T09:00:00+03:00` degerini 09:00 UTC sanip 3 saat hata yapardi.
    offset = 0
    time = time.rstrip("Z")
    for sign, yon in (("+", 1), ("-", -1)):
        head, marker, tail = time.partition(sign)
        if marker and len(tail) == 5 and tail[2] == ":":
            time = head
            offset = yon * (int(tail[:2]) * 60 + int(tail[3:]))
            break
    time = time.partition(".")[0][:8]
    if len(time) < 5 or time[2] != ":":
        return None
    if len(time) == 5:
        time += ":00"
    try:
        parsed = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) - timedelta(minutes=offset)


def local(value: str | datetime | None) -> datetime | None:
    """UTC degeri Europe/Istanbul'a tasir. Bozuk/bos ise `None`."""
    parsed = parse(value)
    return parsed.astimezone(TZ) if parsed else None


def stamp(value: str | datetime | None, fmt: str = STAMP_FMT) -> str:
    """`2026-08-14T19:48:42.724Z` -> `14.08.2026 22:48`. Bozuk/bos ise bos metin.

    Bos metin donmesi SART: sablonlarda dogrudan basiliyor, `None` yazisi
    kullanicinin ekranina dusmemeli.
    """
    when = local(value)
    return when.strftime(fmt) if when else ""


def now() -> datetime:
    """Su an, Istanbul saatiyle (aware)."""
    return datetime.now(timezone.utc).astimezone(TZ)


def utc_now() -> datetime:
    """Su an, NAIVE UTC. Depolanan damgalarla ayni duzlemde aritmetik icin.

    `datetime.now()` YERINE bu kullanilir: konteynere bir gun `TZ` verilirse
    `now()` 3 saat kayar ama veritabanindaki damgalar UTC kalir — pencere
    hesaplari (`since = simdi - 24 saat`) sessizce yanlislanirdi.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_iso() -> str:
    """Depolama damgasi: naive UTC, `2026-08-19T09:04:59`.

    NAIVE olmasi zorunlu — `+00:00` eki eklenirse metin karsilastirmasi
    (`WHERE at >= ?`) mevcut satirlarla tutmaz. `tracking/db.py:_now()` ile
    ayni sekil.
    """
    return utc_now().isoformat(timespec="seconds")
