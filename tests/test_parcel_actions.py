"""Kargo detay panelinden POD yukleme ve Teslimat / PIN Notu testleri."""
from io import BytesIO
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracking.db import ShipmentsDB
from web import deps
from web.routers import pod as pod_router, tracking as tracking_router


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = ShipmentsDB(path=tmp_path / "shipments.db")
    monkeypatch.setattr(deps, "_shipments_db", d)
    monkeypatch.setattr(deps, "OUTPUT_DIR", tmp_path)
    return d


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(pod_router.router)
    app.include_router(tracking_router.router)
    return TestClient(app)


def test_upload_pod_from_detail_drawer(db, client):
    db.add_parcel("38120177009999", channel="NL", store_code="TEST1", invoice_number="5000001",
                  status="in_transit", consignee_name="Test Store", country="NL")

    pdf_bytes = b"%PDF-1.4 sample pod content"
    resp = client.post(
        "/pod/upload/38120177009999",
        files={"file": ("pod.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 200
    assert "Resmi POD belgesi sistemde mevcut" in resp.text

    p = db.get("38120177009999")
    assert p["status"] == "delivered"
    assert p["pod_path"] is not None


def test_update_parcel_note_and_pin_code(db, client):
    db.add_parcel("38120177009998", channel="NL", store_code="TEST2", invoice_number="5000002",
                  status="delivered", consignee_name="Test Store 2", country="NL",
                  delivered_date="2026-08-10T10:00:00Z")

    resp = client.post(
        "/tracking/note/38120177009998",
        data={"note": "cnee collected from PS with pin code, no pod available."},
    )
    assert resp.status_code == 200
    assert resp.headers.get("HX-Trigger") == "refresh"
    assert "cnee collected from PS with pin code" in resp.text

    p = db.get("38120177009998")
    assert p["issue_text"] == "cnee collected from PS with pin code, no pod available."

    # Teslimat notu / PIN kodu girildikten sonra pod_missing_since ve problem_parcels'te cikmamali
    missing = db.pod_missing_since(wait_days=1)
    assert not any(m["tracking_no"] == "38120177009998" for m in missing)

    problems = db.problem_parcels(stale_days=1)
    assert not any(prob["tracking_no"] == "38120177009998" for prob in problems)

    # Not girildiginde resmi PDF diskte otomatik olusmali ve pod_path dolmali
    assert p["pod_path"] is not None
    from pathlib import Path
    pod_file = Path(p["pod_path"])
    assert pod_file.exists()
    assert pod_file.suffix == ".pdf"

    # PDF indirme basarili olmali
    dl_resp = client.get("/pod/download/38120177009998")
    assert dl_resp.status_code == 200
    assert dl_resp.content.startswith(b"%PDF")


def test_update_parcel_note_from_pod_modal_target(db, client):
    """HX-Target: pod-modal-container ile gonderilen form pod_modal partial'i dondurmeli."""
    db.add_parcel("38120177009991", channel="NL", store_code="TESTMODAL", invoice_number="5000010",
                  status="delivered", consignee_name="Test Modal Store", country="NL")

    resp = client.post(
        "/tracking/note/38120177009991",
        data={"note": "Delivered to safe place / porch."},
        headers={"HX-Target": "pod-modal-container"},
    )
    assert resp.status_code == 200
    assert "Resmi POD belgesi sistemde mevcut ve arşivlenmiş." in resp.text
    assert "Delivered to safe place / porch." in resp.text


def test_parcel_with_note_renders_note_badge_not_no_pod(db, client):
    """Teslim edilmis ve notu olan kargo tablolarda 'No POD' yerine not rozeti gostermeli."""
    db.add_parcel("38120177009990", channel="NL", store_code="TESTNOTE", invoice_number="5000009",
                  status="delivered", consignee_name="Test Note Store", country="NL")
    db.update_tracking_info("38120177009990", issue_text="cnee collected from PS with pin code, no pod available.")

    from web.templating import templates
    from fastapi import Request

    # Tracking rows render
    parcel = db.get("38120177009990")
    rendered = templates.get_template("_partials/tracking_rows.html").render(
        parcels=[parcel],
        **deps.template_context(),
    )
    assert "🔑" in rendered
    assert "cnee collected from PS" in rendered
    assert "POD yok" not in rendered
    assert "No POD" not in rendered


def test_upload_image_pod_embeds_into_template(db, client):
    from PIL import Image

    img = Image.new("RGB", (100, 100), color="blue")
    buf = BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    db.add_parcel("38120177009997", channel="NL", store_code="TESTIMG", invoice_number="5000003",
                  status="in_transit", consignee_name="Test Image Store", country="NL")

    resp = client.post(
        "/pod/upload/38120177009997",
        files={"file": ("signature.png", BytesIO(png_bytes), "image/png")},
    )
    assert resp.status_code == 200
    assert "Resmi POD belgesi sistemde mevcut" in resp.text

    p = db.get("38120177009997")
    assert p["status"] == "delivered"
    assert p["pod_path"] is not None
    assert p["pod_media_type"] == "png"


def test_upload_jpeg_pod_detected_by_magic_header(db, client):
    """JPEG magic header (ff d8) ile yuklenen dosya 'jpg' olarak algılanmalı."""
    db.add_parcel("38120177009996", channel="NL", store_code="TESTJPG", invoice_number="5000004",
                  status="in_transit", consignee_name="Test JPG Store", country="NL")

    # Gerçek JPEG magic header
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 200

    resp = client.post(
        "/pod/upload/38120177009996",
        files={"file": ("photo.jpg", BytesIO(jpeg_bytes), "image/jpeg")},
    )
    assert resp.status_code == 200

    p = db.get("38120177009996")
    assert p["status"] == "delivered"
    assert p["pod_path"] is not None
    assert p["pod_media_type"] == "jpg"


def test_upload_pod_nonexistent_parcel_returns_404(db, client):
    """Olmayan tracking_no icin 404 donmeli."""
    resp = client.post(
        "/pod/upload/NOEXIST9999999",
        files={"file": ("pod.pdf", BytesIO(b"%PDF-1.4 test"), "application/pdf")},
    )
    assert resp.status_code == 404


def test_upload_empty_pod_file_returns_400(db, client):
    """Bos dosya icin 400 donmeli."""
    db.add_parcel("38120177009995", channel="NL", store_code="TESTEMPTY", invoice_number="5000005",
                  status="in_transit", consignee_name="Test Empty", country="NL")

    resp = client.post(
        "/pod/upload/38120177009995",
        files={"file": ("empty.pdf", BytesIO(b""), "application/pdf")},
    )
    assert resp.status_code == 400


def test_pdf_upload_creates_archive_file_on_disk(db, client, tmp_path):
    """PDF yuklenince hem ham dosya hem arsiv dosyasi diske yazılmalı."""
    db.add_parcel("38120177009994", channel="NL", store_code="TESTDISK", invoice_number="5000006",
                  status="in_transit", consignee_name="Test Disk", country="NL")

    pdf_bytes = b"%PDF-1.4 full archive test content"
    resp = client.post(
        "/pod/upload/38120177009994",
        files={"file": ("pod.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 200

    p = db.get("38120177009994")
    from pathlib import Path
    pod_file = Path(p["pod_path"])
    assert pod_file.exists(), f"Ham POD dosyasi diskte bulunamadi: {pod_file}"
    assert pod_file.read_bytes() == pdf_bytes

    # Arsiv klasorunde musteri belgesi de olmali
    parent = pod_file.parent
    all_pdfs = list(parent.glob("*.pdf"))
    assert len(all_pdfs) >= 1, f"Arsiv PDF bulunamadi: {list(parent.iterdir())}"


def test_image_upload_creates_archive_pdf_from_template(db, client, tmp_path):
    """Resim yuklendiginde arsiv klasorunde sablondan uretilenPDF olmali."""
    from PIL import Image

    img = Image.new("RGB", (200, 300), color="red")
    buf = BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    db.add_parcel("38120177009993", channel="NL", store_code="TPLTEST", invoice_number="5000007",
                  status="in_transit", consignee_name="Template Test", country="NL")

    resp = client.post(
        "/pod/upload/38120177009993",
        files={"file": ("signature.png", BytesIO(png_bytes), "image/png")},
    )
    assert resp.status_code == 200

    p = db.get("38120177009993")
    from pathlib import Path
    pod_file = Path(p["pod_path"])
    parent = pod_file.parent

    # Ham goruntu .png olmali
    assert pod_file.suffix == ".png"
    assert pod_file.exists()

    # Arsiv PDF sablon ile uretilmis olmali
    archive_pdfs = [f for f in parent.glob("*.pdf")]
    assert len(archive_pdfs) >= 1, f"Arsiv PDF yok: {list(parent.iterdir())}"
    pdf_content = archive_pdfs[0].read_bytes()
    assert pdf_content.startswith(b"%PDF"), "Arsiv dosyasi gecerli PDF degil!"


def test_sync_all_events_from_gls_api_into_status_history(db, client):
    """GLS API'sinden gelen tum gecmis olaylar status_events tablosuna islenmeli."""
    db.add_parcel("38120177009992", channel="NL", store_code="TESTSYNC", invoice_number="5000008",
                  status="in_transit", consignee_name="Test Sync", country="FR")

    raw_events = [
        {
            "date": "2026-08-05T15:08:44Z",
            "depot": "NL0100",
            "depotName": "Hub Utrecht",
            "descriptionEN": "The parcel data was entered into the GLS IT system; the parcel was not yet handed over to GLS."
        },
        {
            "date": "2026-08-06T15:23:44Z",
            "depot": "NL1000",
            "depotName": "Amsterdam",
            "descriptionEN": "The parcel was handed over to GLS."
        },
        {
            "date": "2026-08-10T03:45:58Z",
            "depot": "FR0094",
            "depotName": "Fleury FR0094",
            "descriptionEN": "Not out for delivery/delivery not accepted that day"
        },
        {
            "date": "2026-08-21T02:14:09Z",
            "depot": "FR0094",
            "depotName": "Fleury FR0094",
            "descriptionEN": "The parcel is stored in the parcel center."
        }
    ]

    inserted = db.sync_events("38120177009992", raw_events)
    assert inserted == 4

    events = db.events_for("38120177009992")
    assert len(events) == 5
    # En yeni tarih en ustte
    notes = [e["note"] for e in events]
    assert any("The parcel is stored in the parcel center" in n for n in notes)
    assert any("Fleury FR0094" in n for n in notes)
    assert any("Not out for delivery" in n for n in notes)
    assert any("Hub Utrecht" in n for n in notes)

    # Tekrar cagrilirsa mukerrer eklememeli
    inserted_again = db.sync_events("38120177009992", raw_events)
    assert inserted_again == 0
    assert len(db.events_for("38120177009992")) == 5


