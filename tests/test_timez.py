"""`timez` — UTC depola, Istanbul goster."""
from datetime import datetime, timezone

import pytest

import timez


@pytest.mark.parametrize("raw, beklenen", [
    ("2026-08-14T19:48:42.724Z", "14.08.2026 22:48"),   # GLS, salise ekli
    ("2026-07-31T09:33:47Z", "31.07.2026 12:33"),       # GLS, salisesiz
    ("2026-08-19T09:04:59", "19.08.2026 12:04"),        # bizim _now(), naive
    ("2026-08-19 09:04:59", "19.08.2026 12:04"),        # SQLite datetime()
    ("2026-08-19T09:04", "19.08.2026 12:04"),           # saniyesiz
    ("2026-08-19T09:04:59+00:00", "19.08.2026 12:04"),  # tracker_health
])
def test_dort_giris_bicimi_de_cevrilir(raw, beklenen):
    assert timez.stamp(raw) == beklenen


@pytest.mark.parametrize("raw", ["", None, "   ", "bozuk", "2026-13-99T00:00:00Z",
                                 "19.08.2026", "2026-08-19T9:4"])
def test_bozuk_giris_bos_metin_doner(raw):
    """Sablonlarda dogrudan basiliyor — `None` yazisi ekrana dusmemeli."""
    assert timez.stamp(raw) == ""


def test_yalniz_tarih_cevrilmez():
    """`shipment_date` gun alanidir; saat cevirilirse gun kayardi."""
    assert timez.parse("2026-08-19") is None
    assert timez.stamp("2026-08-19") == ""


def test_gece_yarisi_gun_atlar():
    assert timez.stamp("2026-08-19T22:30:00Z") == "20.08.2026 01:30"


def test_yaz_saati_donemi_zoneinfo_ile_hesaplanir():
    """2016 oncesi Turkiye yaz saati uyguluyordu — sabit +3 DEGIL."""
    assert timez.stamp("2015-07-01T12:00:00Z") == "01.07.2015 15:00"
    assert timez.stamp("2015-01-01T12:00:00Z") == "01.01.2015 14:00"


def test_datetime_girisi_de_kabul_edilir():
    aware = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    assert timez.stamp(aware) == "19.08.2026 12:00"
    assert timez.stamp(datetime(2026, 8, 19, 9, 0)) == "19.08.2026 12:00"


def test_kaydirma_eki_gercekten_uygulanir():
    """`+03:00` kirpilip yok sayilsaydi 3 saat hata olurdu."""
    assert timez.stamp("2026-08-19T12:00:00+03:00") == "19.08.2026 12:00"
    assert timez.stamp("2026-08-19T09:00:00-03:00") == "19.08.2026 15:00"


def test_ozel_bicim():
    assert timez.stamp("2026-08-19T09:04:59Z", "%d.%m.%Y") == "19.08.2026"


def test_local_istanbul_doner():
    when = timez.local("2026-08-19T09:00:00Z")
    assert when.hour == 12 and when.utcoffset().total_seconds() == 3 * 3600


def test_now_istanbul_saatinde():
    fark = timez.now().utcoffset().total_seconds()
    assert fark == 3 * 3600


def test_utc_iso_naive_doner():
    """`+00:00` eki metin karsilastirmasini (`WHERE at >= ?`) bozardi."""
    text = timez.utc_iso()
    assert "+" not in text and not text.endswith("Z")
    assert len(text) == 19 and text[10] == "T"


def test_utc_iso_now_ile_uc_saat_fark_eder():
    """Depolama UTC kalir; gosterim yereldir. Ikisi karistirilmamali."""
    depolanan = datetime.fromisoformat(timez.utc_iso())
    gosterilen = timez.now().replace(tzinfo=None)
    assert 2.9 * 3600 < (gosterilen - depolanan).total_seconds() < 3.1 * 3600
