# -*- coding: utf-8 -*-
"""Bu oturumda bulunan iki hatanin regresyon testleri:
1. Excel'den gelen tracking_no HTML/JS'e gomuluyor (POD sayfasi) — sanitize edilmeli.
2. Content-Disposition header'i Latin-1'e kisitli — gercek musteri isimlerindeki
   Macarca/Cekce vb. karakterler sunucuyu 500'e dusurmemeli.
"""
from starlette.responses import Response

from web.importers.tracking_list import row_to_parcel, build_column_map
from web.routers.pod import _content_disposition


def test_tracking_no_strips_dangerous_characters():
    colmap = build_column_map(["PARCEL NUMBER", "COUNTRY"])
    row = {"PARCEL NUMBER": 'abc"onmouseover="alert(1)', "COUNTRY": "FR"}
    parcel = row_to_parcel(row, colmap)
    assert parcel is not None
    assert '"' not in parcel["tracking_no"]
    assert parcel["tracking_no"] == "abconmouseoveralert1"


def test_tracking_no_only_punctuation_rejected():
    colmap = build_column_map(["PARCEL NUMBER"])
    row = {"PARCEL NUMBER": '"><>://'}
    parcel = row_to_parcel(row, colmap)
    assert parcel is None


def test_content_disposition_handles_non_latin1_names():
    header = _content_disposition("POD_test_Erdős-Kőrösi.pdf")
    # Onceden UnicodeEncodeError firlatiyordu; artik Response olusturulabilmeli.
    r = Response(b"x", headers={"Content-Disposition": header})
    assert r is not None
    assert "filename*=UTF-8" in header


def test_content_disposition_ascii_fallback_is_plain():
    header = _content_disposition("POD_simple_ref.pdf")
    assert 'filename="POD_simple_ref.pdf"' in header
