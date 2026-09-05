# -*- coding: utf-8 -*-
"""FedEx kolileri icin "Koli Görseli" yuklemesi (POD'un FedEx'teki karsiligi).

FedEx'te resmi POD YOK (bkz. gls_api/providers.py:pod_provider_for) — operator
bunun yerine koli fotografini yukluyor, biz onu ERP'den gelen koli icerigiyle
birlikte TEK bir PDF'e gomuyoruz (kullanici istegi, 2026-08-31).
"""
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from tracking.db import ShipmentsDB
from web import box_image_document, deps
from web.routers import tracking as tracking_router


def _png_bytes(color="blue") -> bytes:
    img = Image.new("RGB", (80, 60), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = ShipmentsDB(path=tmp_path / "shipments.db")
    monkeypatch.setattr(deps, "_shipments_db", d)
    monkeypatch.setattr(deps, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tracking_router, "BOX_IMAGE_DIR", tmp_path / "BOX_IMAGES")
    return d


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(tracking_router.router)
    # Silme ucu `dependencies=WRITE` tasiyor (yikici islem); test istemcisi
    # yazma yetkili bir kullanici taklit eder (bkz. tests/test_dashboard.py).
    from auth.dependencies import get_current_user
    from fastapi import Request

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "k", "role": "admin"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- build() (PDF)

def test_build_produces_a_pdf_with_images_only():
    parcel = {"tracking_no": "876365567966", "store_code": "GOLIT01",
              "consignee_name": "Test CA", "country": "CA"}
    data = box_image_document.build(parcel, [_png_bytes()])
    assert data.startswith(b"%PDF")


def test_build_works_without_any_images():
    """Modal acilip henuz hic fotograf yuklenmemisse bile belge uretilebilmeli
    (koli bilgisi + icerik tablosu tek basina anlamli kalir)."""
    parcel = {"tracking_no": "876365567966"}
    data = box_image_document.build(parcel, [])
    assert data.startswith(b"%PDF")


def test_build_includes_the_contents_table_when_given():
    parcel = {"tracking_no": "876365567966"}
    headers = ["Model", "Renk", "S", "M", "Toplam"]
    rows = [["ST1", "Kırmızı", 2, 3, 5]]
    data = box_image_document.build(parcel, [_png_bytes()], headers, rows)
    assert data.startswith(b"%PDF")


def test_build_handles_multiple_images():
    data = box_image_document.build({"tracking_no": "1"}, [_png_bytes("red"), _png_bytes("green")])
    assert data.startswith(b"%PDF")


def test_build_is_entirely_in_english():
    """Kullanici karari (2026-09-02): "pdflerin de ingilizce olması lazım
    daha panelden oluştururkende" — FedEx WhatsApp grubuna giden metinlerle
    aynı dil kuralı, PDF için de geçerli."""
    from pypdf import PdfReader

    parcel = {"tracking_no": "876365567966", "store_code": "GOLIT01",
              "invoice_number": "5000885", "consignee_name": "Test CA",
              "country": "CA", "season": "FA26", "shipment_method": "FedEx"}
    headers = ["Style", "Colour", "S", "M", "QTY"]
    rows = [["ST1", "Red", 2, 3, 5]]
    data = box_image_document.build(parcel, [_png_bytes()], headers, rows)

    text = "".join(p.extract_text() or "" for p in PdfReader(BytesIO(data)).pages)
    for kelime in ("SHIPMENT DETAILS", "Box Information", "Tracking No", "Store",
                  "Invoice", "Consignee", "Country", "Season",
                  "Shipment Method", "Box Contents", "Box Photo"):
        assert kelime in text, kelime
    for turkce in ("KOLİ GÖRSELİ", "Mağaza", "Alıcı", "Ülke", "Koli İçeriği",
                  "Koli Fotoğrafı", "oluşturulma"):
        assert turkce not in text, turkce


# ---------------------------------------------------------------- db setter

def test_set_box_image_path_persists(db):
    db.add_parcel("876365567001", channel="FEDEX", country="CA", consignee_name="c")
    db.set_box_image_path("876365567001", "/tmp/x/876365567001.pdf")
    assert db.get("876365567001")["box_image_path"] == "/tmp/x/876365567001.pdf"


# ---------------------------------------------------------------- upload ucu

def test_upload_box_image_embeds_into_pdf_and_saves_path(db, client):
    db.add_parcel("876365567002", channel="FEDEX", country="CA",
                  consignee_name="Test FedEx", status="delivered")

    resp = client.post(
        "/tracking/box-image/upload/876365567002",
        files={"files": ("box.png", BytesIO(_png_bytes()), "image/png")},
    )
    assert resp.status_code == 200
    assert "Gönderi Detayları" in resp.text

    p = db.get("876365567002")
    assert p["box_image_path"]
    assert Path(p["box_image_path"]).exists()
    assert Path(p["box_image_path"]).read_bytes().startswith(b"%PDF")


def test_upload_accepts_multiple_files_at_once(db, client):
    db.add_parcel("876365567003", channel="FEDEX", country="CA", consignee_name="c")

    resp = client.post(
        "/tracking/box-image/upload/876365567003",
        files=[
            ("files", ("a.png", BytesIO(_png_bytes("red")), "image/png")),
            ("files", ("b.png", BytesIO(_png_bytes("green")), "image/png")),
        ],
    )
    assert resp.status_code == 200

    raw = list((deps.OUTPUT_DIR / "BOX_IMAGES" / "876365567003").glob("raw_*.*"))
    assert len(raw) == 2


def test_a_second_upload_is_refused_while_a_document_exists(db, client):
    """Kullanici karari (2026-09-02): "tekrar uzerine ekleme yapamasin, eski
    olusturdugunu kaldirmasi gereksin". Arayuz yukleme alanini gizliyor; bu da
    dogrudan istek atilirsa belgenin sessizce buyumesini engelleyen kilit."""
    db.add_parcel("876365567004", channel="FEDEX", country="CA", consignee_name="c")

    first = client.post("/tracking/box-image/upload/876365567004",
                        files={"files": ("a.png", BytesIO(_png_bytes("red")), "image/png")})
    assert first.status_code == 200

    second = client.post("/tracking/box-image/upload/876365567004",
                         files={"files": ("b.png", BytesIO(_png_bytes("green")), "image/png")})
    assert second.status_code == 409

    raw = list((deps.OUTPUT_DIR / "BOX_IMAGES" / "876365567004").glob("raw_*.*"))
    assert len(raw) == 1, "reddedilen yukleme diske dokunmamali"


def test_remove_clears_everything_and_reopens_upload(db, client):
    """"Kaldir" fotograflari, PDF'i ve DB isaretini birlikte siler; sonra
    yeniden yuklenebilir. Ham fotograflar da gitmeli — kalirlarsa bir sonraki
    yukleme onlari yeni PDF'e yeniden gomerdi."""
    db.add_parcel("876365567005", channel="FEDEX", country="CA", consignee_name="c")
    client.post("/tracking/box-image/upload/876365567005",
                files={"files": ("a.png", BytesIO(_png_bytes("red")), "image/png")})
    assert db.get("876365567005")["box_image_path"]

    assert client.post("/tracking/box-image/delete/876365567005").status_code == 200

    assert not db.get("876365567005")["box_image_path"]
    assert not (deps.OUTPUT_DIR / "BOX_IMAGES" / "876365567005").exists()

    # kilit acildi: yeniden yuklenebiliyor
    again = client.post("/tracking/box-image/upload/876365567005",
                        files={"files": ("b.png", BytesIO(_png_bytes("green")), "image/png")})
    assert again.status_code == 200
    raw = list((deps.OUTPUT_DIR / "BOX_IMAGES" / "876365567005").glob("raw_*.*"))
    assert len(raw) == 1


def test_the_document_is_named_shipment_details_plus_tracking_no(db, client):
    """Kullanici karari (2026-09-02): once "Shipment Details", sonra takip no.
    Diskteki dosya, indirme adi ve WhatsApp ekinin adi AYNI olmali."""
    db.add_parcel("876365567006", channel="FEDEX", country="CA", consignee_name="c")
    client.post("/tracking/box-image/upload/876365567006",
                files={"files": ("a.png", BytesIO(_png_bytes("red")), "image/png")})

    beklenen = "Shipment Details 876365567006.pdf"
    assert (deps.OUTPUT_DIR / "BOX_IMAGES" / "876365567006" / beklenen).exists()

    r = client.get("/tracking/box-image/download/876365567006")
    assert r.status_code == 200
    assert beklenen in r.headers["content-disposition"]


def test_the_pdf_stays_under_the_20mb_cap(db):
    """Kullanici karari (2026-09-02): "Pdf boyutu maksimum 20 mb olacak".
    Sikistirma tek basina yetmez — tavan fotograf SAYISIYLA da asilir
    (canli ornek 876634105532: 3 fotograf, 46.8 MB), bu yuzden belge
    olculup gerekirse daha sert ayarla yeniden kurulur."""
    import random
    def gurultu(seed):                       # sikismayan, buyuk fotograf
        random.seed(seed)
        img = Image.new("RGB", (1500, 1200))
        img.putdata([(random.randrange(256), random.randrange(256),
                      random.randrange(256)) for _ in range(1500 * 1200)])
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    images = [gurultu(i) for i in range(4)]
    assert sum(len(i) for i in images) > box_image_document.MAX_PDF_BYTES

    pdf = box_image_document.build({"tracking_no": "876634105532"}, images)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) <= box_image_document.MAX_PDF_BYTES


def test_upload_rejects_non_image_files(db, client):
    db.add_parcel("876365567005", channel="FEDEX", country="CA", consignee_name="c")

    resp = client.post(
        "/tracking/box-image/upload/876365567005",
        files={"files": ("not-an-image.txt", BytesIO(b"hello world"), "text/plain")},
    )
    assert resp.status_code == 400
    assert db.get("876365567005")["box_image_path"] is None


def test_upload_for_an_unknown_parcel_is_404(client):
    resp = client.post(
        "/tracking/box-image/upload/00000000000000",
        files={"files": ("a.png", BytesIO(_png_bytes()), "image/png")},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------- indirme ucu

def test_download_returns_the_pdf_when_uploaded(db, client):
    db.add_parcel("876365567006", channel="FEDEX", country="CA", consignee_name="c")
    client.post("/tracking/box-image/upload/876365567006",
                files={"files": ("a.png", BytesIO(_png_bytes()), "image/png")})

    resp = client.get("/tracking/box-image/download/876365567006")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_download_is_404_before_any_upload(db, client):
    db.add_parcel("876365567007", channel="FEDEX", country="CA", consignee_name="c")
    resp = client.get("/tracking/box-image/download/876365567007")
    assert resp.status_code == 404


# ---------------------------------------------------------------- detay paneli

def test_fedex_detail_shows_box_image_button_not_pod(db, client):
    """Kullanici istegi: FedEx'te ikinci dugme POD/Not degil Koli Görseli olmali."""
    db.add_parcel("876365567008", channel="FEDEX", country="CA", consignee_name="c")

    html = client.get("/tracking/detail/876365567008").text

    assert "Gönderi Detayları" in html
    assert "POD / Not" not in html
    assert "/tracking/box-image/upload/876365567008" in html


def test_nl_detail_still_shows_pod_not_box_image(db, client):
    """Diger kanallar (NL/IE) eskisi gibi POD/Not gormeli — davranis degismemeli."""
    db.add_parcel("38120177050001", channel="NL", country="DE", consignee_name="c")

    html = client.get("/tracking/detail/38120177050001").text

    assert "POD / Not" in html
    assert "Koli Görseli" not in html
    assert "/pod/upload/38120177050001" in html


def test_contents_table_headers_stay_english_even_when_the_panel_is_turkish():
    """Kullanici karari (2026-09-02): FedEx PDF'i her zaman Ingilizce,
    operatorun panel dili Turkce olsa bile — `lang="en"` sabit gecilir
    (bkz. web/routers/tracking.py:upload_box_images)."""
    import i18n
    from erp.contents import build_matrix
    from web.routers.tracking import _contents_table

    matrix = build_matrix([{"style": "ST1", "color": "Red", "size": "M",
                            "qty": 2, "barcode": "BC1"}])

    with i18n.use("tr"):
        headers, rows = _contents_table(matrix, lang="en")

    assert headers[0] == "Style"          # "Model" DEGIL
    assert headers[-1] == "QTY"
    assert rows[-1][0] == "TOTAL"         # "TOPLAM" DEGIL


def test_a_tall_photo_keeps_its_caption_on_the_same_page():
    """Kullanici bildirdi (2026-09-02, ekran goruntusu): baslik ("Box Photo
    1/1") bir sayfada, resim BASLIKSIZ bir sonraki sayfada kaliyordu — fpdf2
    `image()`'in `y` verilmeden akan modda KENDI KENDINE sayfa atlamasi
    yuzunden (bkz. web/box_image_document.py:_picture). Uzun/dikey bir foto
    ile bu senaryoyu tekrar uretip duzeldigini dogrular: resmi tasiyan sayfada
    "Box Photo" basligi da BULUNMALI.
    """
    from pypdf import PdfReader

    # Once koli bilgisi + icerik tablosuyla sayfayi doldur, sonra DIKEY
    # (uzun) bir fotograf ekle — MAX_IMAGE_HEIGHT'a (230mm) yakin bir oran.
    img = Image.new("RGB", (400, 1600), color="blue")  # cok dikey oran
    buf = BytesIO()
    img.save(buf, format="PNG")

    parcel = {"tracking_no": "876365567966", "store_code": "GOLIT01",
              "invoice_number": "5000885", "consignee_name": "Test CA",
              "country": "CA", "season": "FA26", "shipment_method": "FedEx"}
    headers = ["Style", "Colour", "S", "M", "QTY"]
    rows = [[f"ST{i}", "Red", 2, 3, 5] for i in range(20)]   # sayfayi doldur

    data = box_image_document.build(parcel, [buf.getvalue()], headers, rows)
    reader = PdfReader(BytesIO(data))

    photo_page = next(i for i, pg in enumerate(reader.pages) if pg.images)
    caption_page = next(i for i, pg in enumerate(reader.pages)
                        if "Box Photo" in (pg.extract_text() or ""))
    assert photo_page == caption_page
