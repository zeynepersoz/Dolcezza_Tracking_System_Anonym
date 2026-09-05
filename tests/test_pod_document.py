# -*- coding: utf-8 -*-
"""POD teslimat belgesi (web/pod_document.py) + indirme surumleri."""
from io import BytesIO

import pytest
from PIL import Image

from web import pod_document
from web.routers.pod import pod_filename, build_document


def _image(mode="RGB", size=(300, 120)) -> bytes:
    buf = BytesIO()
    Image.new(mode, size, "white" if mode == "RGB" else 1).save(buf, format="PNG")
    return buf.getvalue()


PARCEL = {
    "tracking_no": "38120177001846", "channel": "NL", "reference": "1040019",
    "consignee_name": "ZBAES5N", "zip_code": "17250", "country": "Spain",
    "invoice_number": "1040019", "store_code": "ZBAES5N",
    "shipment_method": "Genel Sevkiyatlar", "delivered_date": "2026-07-20T18:35:00",
}
INFO = {
    "uniqueNo": "GLS0021", "custName": "Dolcezza Europe B.V.",
    "product": "EuroBusinessParcel", "services": "FlexDeliveryService",
    "suppliedWeight": 6.5, "weighedWeight": 6.8, "length": 60, "width": 40, "height": 30,
    "addressInfo": {"recipient": {
        "name": "Monsierra Gestion, S.L", "name2": "Club Golf D'Aro", "name3": "ZBAES5N",
        "street": "Urbanitzacio Mas Nou", "houseNo": "14", "zipcode": "17250",
        "city": "Platja d'Aro", "country": "Spain"}},
    "deliveryScanInfo": {"dateTime": "2026-07-20T18:35:00", "signedBy": "ROSSI"},
    "parcels": [{"parcelNo": "38120177001846", "uniqueNo": "GLS0021", "suppliedWeight": 6.5},
                {"parcelNo": "38120177001847", "uniqueNo": "GLS0022", "suppliedWeight": 4.2}],
}


# ======================================================================
# facts() — iki kaynagin birlestirilmesi
# ======================================================================
def test_gls_recipient_wins_over_db_store_code():
    """DB'deki consignee_name cogu kayitta magaza kodudur ("ZBAES5N")."""
    data = pod_document.facts(PARCEL, INFO)
    assert data["recipient"][0] == "Monsierra Gestion, S.L"
    assert "Urbanitzacio Mas Nou 14" in data["recipient"]
    # name3 magaza kodudur, unvan satirina karistirilmaz.
    assert "ZBAES5N" not in data["recipient"]
    assert data["store_code"] == "ZBAES5N"


def test_falls_back_to_db_row_without_gls_data():
    """ShipIT/T&T kanalinda ya da oturum acilamadiginda bilgiler yereldan gelir."""
    data = pod_document.facts(PARCEL)
    assert data["recipient"] == ["ZBAES5N", "17250", "Spain"]
    assert data["delivered_date"] == "20-07-2026"
    assert data["signed_by"] == ""


def test_placeholder_values_are_treated_as_empty():
    """Excel kaynakli satirlarda bos hucre "nan" olarak gelir."""
    data = pod_document.facts({**PARCEL, "reference": "nan", "invoice_number": "-"})
    assert (data["reference"], data["invoice_number"]) == ("", "")


def test_sibling_parcels_are_listed():
    rows = pod_document._parcel_rows(pod_document.facts(PARCEL, INFO))
    assert [r[0] for r in rows] == ["38120177001846", "38120177001847"]


# ======================================================================
# Belge uretimi
# ======================================================================
@pytest.mark.parametrize("builder", [pod_document.customer_document,
                                     pod_document.internal_document])
def test_documents_are_valid_pdfs(builder):
    pdf = builder(PARCEL, _image(), INFO)
    assert pdf.startswith(b"%PDF")
    from pypdf import PdfReader
    assert len(PdfReader(BytesIO(pdf)).pages) >= 1


def test_bilevel_signature_image_is_embedded():
    """GLS imzasi 1-bit ("1" modu) gelir; fpdf2 dogrudan almaz, normalize edilir."""
    assert pod_document.customer_document(PARCEL, _image("1"), INFO).startswith(b"%PDF")


def test_document_is_built_without_any_gls_info():
    """Bilgi yoksa belge yine uretilir; yalnizca o satirlar bos kalir."""
    assert pod_document.customer_document({"tracking_no": "X1"}, _image()).startswith(b"%PDF")


# ======================================================================
# Indirme: hangi surum, hangi ad
# ======================================================================
def test_ready_made_pdf_is_served_untouched():
    """ShipIT/T&T zaten eksiksiz bir belge dondurur — sarmalanmaz."""
    assert build_document(PARCEL, b"%PDF-1.4 hazir", "pdf", {}) == (b"%PDF-1.4 hazir", "pdf")


def test_broken_image_falls_back_to_raw_bytes():
    """Kanit hic inmemektense eksik insin — ham goruntu kendi uzantisiyla verilir."""
    assert build_document(PARCEL, b"bozuk", "png", {}) == (b"bozuk", "png")


def test_unknown_kind_falls_back_to_customer_document():
    document, ext = build_document(PARCEL, _image(), "png", INFO, "uydurma")
    assert (ext, document[:4]) == ("pdf", b"%PDF")


def test_two_versions_get_different_filenames():
    """Ikisi de ayni klasore inebilir; ic kayit surumu onekle ayrilir."""
    customer = pod_filename(PARCEL, "pdf")
    internal = pod_filename(PARCEL, "pdf", "internal")
    assert internal == f"IC-{customer}"


def test_filename_is_store_code_and_invoice():
    """Kullanicinin istedigi bicim: NUNAT1N-5000202.pdf — takip numarasi YOK."""
    assert pod_filename(PARCEL, "pdf") == "ZBAES5N-1040019.pdf"


def test_two_invoices_in_one_box_both_appear():
    """Adresler ayniysa bir koli iki fatura tasiyabilir; ikisinden de bulunmali."""
    parcel = {**PARCEL, "store_code": "ZHPDE1N", "emc_invoices": "5000095, 5000119"}
    assert pod_filename(parcel, "pdf") == "ZHPDE1N-5000095+5000119.pdf"


def test_stored_name_wins_over_computed_one():
    """Sira numarasi arsivleme aninda verilir; sonradan hesaplanmaz."""
    assert pod_filename({**PARCEL, "pod_name": "WQSCZ1N-5000314-07"}, "pdf") \
        == "WQSCZ1N-5000314-07.pdf"


def test_filename_falls_back_to_tracking_no():
    """Magaza da fatura da yoksa ad bos kalamaz."""
    parcel = {"tracking_no": "38120177001846", "store_code": "", "invoice_number": ""}
    assert pod_filename(parcel, "pdf") == "38120177001846.pdf"


def test_filename_strips_path_characters():
    """Ad artik diske de yaziliyor: yol karakterleri dosyayi klasorden cikarmamali."""
    name = pod_filename({**PARCEL, "store_code": "../../etc",
                         "invoice_number": "A/B 1", "emc_invoices": ""}, "pdf")
    assert "/" not in name and ".." not in name


def test_zip_path_is_season_shipment_country(monkeypatch):
    """Kullanicinin istedigi duzen: FA26_POD/1st Truck Shipment/ES/MAGAZA-FATURA.pdf.

    Sezon kodu ayri bir ayar degil, sezon gorunumlerinden turer; sevkiyat adi
    ERP'deki `UD_GonderimSekli`den gelir.
    """
    from gls_api import config
    from web.routers.pod import pod_archive_path

    monkeypatch.setattr(config, "season_label", lambda: "FA26")
    parcel = {**PARCEL, "shipment_method": "1st Truck Shipment"}
    assert pod_archive_path(parcel) == "FA26_POD/1st Truck Shipment/Es/ZBAES5N-1040019.pdf"


def test_zip_path_has_no_season_root_when_no_view_is_configured(monkeypatch):
    """Sezon gorunumu tanimli degilse kok klasor de olusmaz — 'None_POD/' yazilmaz."""
    from gls_api import config
    from web.routers.pod import pod_archive_path

    monkeypatch.setattr(config, "season_label", lambda: "")
    assert pod_archive_path(PARCEL) == "Genel Sevkiyatlar/Es/ZBAES5N-1040019.pdf"


def test_document_is_archived_on_arrival(tmp_path):
    """POD sisteme dustugu anda musteri belgesi klasorde hazir olmali."""
    from web.routers.pod import archive_document

    written = archive_document(PARCEL, _image(), "png", INFO, tmp_path)
    assert written == tmp_path / pod_filename(PARCEL, "pdf")
    assert written.read_bytes()[:4] == b"%PDF"
