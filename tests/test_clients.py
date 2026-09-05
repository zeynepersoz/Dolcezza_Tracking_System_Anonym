# -*- coding: utf-8 -*-
"""Mock sunucuya karşı istemci testleri — gerçek GLS şemalarını doğrular.

mock_server fixture'ı tests/conftest.py'de (tüm test dosyaları paylaşır).
Çalıştırma: pytest -q tests/test_clients.py
"""
import os

import pytest

from gls_api.shipit_client import ShipITClient, ShipITError
from gls_api.nl_client import GLSNLClient, GLSNLError
from gls_api.tt_soap_client import TrackTraceClient, TrackTraceError

# Port conftest.py tarafindan secilir (bkz. oradaki aciklama).
BASE = f"http://127.0.0.1:{os.environ['GLS_MOCK_PORT']}"

KNOWN = "21569761233"     # mock_server.KNOWN_PARCELS icinde, endofday'den gecmis
UNKNOWN = "00000000000"


@pytest.fixture
def shipit():
    return ShipITClient(base_url=f"{BASE}/shipit")


@pytest.fixture
def nl():
    return GLSNLClient(base_url=f"{BASE}/nl", username="mock", password="mock")


@pytest.fixture
def tt():
    return TrackTraceClient(endpoint=f"{BASE}/tt/services/Tracking",
                            username="mock", password="mock")


# ======================================================================
# A) ShipIT REST
# ======================================================================
def test_shipit_details_root_is_unit_detail(shipit):
    """Yanit koku `UnitDetail` (eski dokumanlardaki `TUDetail` DEGIL)."""
    data = shipit.parcel_details(KNOWN)
    assert "TUDetail" not in data
    unit = data["UnitDetail"]
    assert unit["TrackID"] == KNOWN
    # Gecmis olaylarinin metin alani `EvtDscr` (`Description` degil)
    assert "delivered" in unit["History"][-1]["EvtDscr"].lower()
    assert unit["History"][-1]["EvtCode"]


def test_shipit_error_comes_from_headers_not_body(shipit):
    """Hata govdesi BOS; metin `message`/`error`/`args` basliklarindadir + HTTP 490."""
    with pytest.raises(ShipITError) as exc:
        shipit.parcel_details(UNKNOWN)
    assert exc.value.status == 490
    assert exc.value.code == "VALIDATION.INVALID_FIELD"
    assert UNKNOWN in exc.value.gls_args
    assert "490" in str(exc.value)


def test_shipit_pod_is_nested_under_poditem(shipit):
    pdf = shipit.parcel_pod(KNOWN)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 100


def test_shipit_pod_wrong_state(shipit):
    with pytest.raises(ShipITError) as exc:
        shipit.parcel_pod(UNKNOWN)
    assert exc.value.code == "POD.WRONG_STATE"


def test_shipit_create_parcels_injects_printing_options(shipit):
    """`PrintingOptions` zorunlu; istemci eksikse otomatik ekler (yoksa mock 490 doner)."""
    result = shipit.create_parcels({
        "Shipment": {
            "Product": "PARCEL",
            "Shipper": {"Address": {"Name1": "EMC", "CountryCode": "IE"}},
            "Consignee": {"Address": {"Name1": "Alici", "CountryCode": "IE"}},
            "ShipmentUnit": [{"Weight": 2.0}],
        }
    })
    parcels = result["CreatedShipment"]["ParcelData"]
    assert parcels and parcels[0]["TrackID"]
    assert result["CreatedShipment"]["PrintData"][0]["LabelFormat"] == "PDF"


def test_shipit_parcel_invisible_until_end_of_day(shipit):
    """Yeni parca `endofday` calismadan takipte GORUNMEZ — en sik 'API bozuk'
    sanilan durum budur."""
    result = shipit.create_parcels({
        "Shipment": {
            "Shipper": {"Address": {"Name1": "EMC", "CountryCode": "IE"}},
            "Consignee": {"Address": {"Name1": "Alici", "CountryCode": "IE"}},
            "ShipmentUnit": [{"Weight": 1.0}],
        }
    })
    track_id = str(result["CreatedShipment"]["ParcelData"][0]["TrackID"])

    with pytest.raises(ShipITError) as exc:
        shipit.parcel_details(track_id)
    assert exc.value.code == "TRACKING.NOT_AVAILABLE"

    report = shipit.end_of_day_report()
    assert track_id in report["EndOfDay"]["Parcels"]

    assert shipit.parcel_details(track_id)["UnitDetail"]["TrackID"] == track_id


def test_shipit_find_parcels_handles_empty_body(shipit):
    """Sonuc yoksa GLS 200 + BOS govde doner; istemci bunu {} yapmali."""
    data = shipit.find_parcels("2020-01-01", "2020-01-02")
    assert isinstance(data, dict)


def test_shipit_allowed_services(shipit):
    data = shipit.allowed_services({"CountryCode": "IE"}, {"CountryCode": "IE"})
    names = {s["ProductName"] for s in data["AllowedServices"]}
    assert "PARCEL" in names


# ======================================================================
# B) Track & Trace SOAP
# ======================================================================
def test_tt_detail_parses_history_and_minut_typo(tt):
    detail = tt.get_tu_detail(KNOWN)
    assert detail["tracking_no"] == KNOWN
    assert detail["signature"] == "J. DOE"
    assert detail["history"]
    # GLS'in `<Minut>` yazim hatasi dogru okunuyorsa saat:dakika olusur
    assert ":" in detail["history"][-1]["date"]
    assert detail["delivered_at"]


def test_tt_truncates_reference_to_11_chars(tt):
    """Barkoddan okunan 12. hane kontrol basamagidir; atilir."""
    assert tt.get_tu_detail(KNOWN + "9")["tracking_no"] == KNOWN


def test_tt_no_data_exit_code(tt):
    with pytest.raises(TrackTraceError) as exc:
        tt.get_tu_detail(UNKNOWN)
    assert exc.value.is_no_data
    assert exc.value.exit_code == 998


def test_tt_pod_returns_bytes_and_filename(tt):
    data, filename = tt.get_tu_pod(KNOWN)
    assert data.startswith(b"%PDF")
    assert filename.endswith(".pdf")


def test_tt_pod_image_not_found(tt):
    with pytest.raises(TrackTraceError) as exc:
        tt.get_tu_pod(UNKNOWN)
    assert exc.value.exit_code == 999


def test_tt_auth_error_when_credentials_empty(tt):
    # Yapicidaki bos deger config'e duser; kimliksiz cagriyi taklit etmek icin
    # alanlari dogrudan bosaltiyoruz.
    tt.username, tt.password = "", ""
    with pytest.raises(TrackTraceError) as exc:
        tt.get_tu_detail(KNOWN)
    assert exc.value.is_auth_error
    assert exc.value.exit_code == 502


def test_tt_list(tt):
    rows = tt.get_tu_list("2024-01-01", "2030-01-01")
    assert rows
    assert all(r["tracking_no"] for r in rows)


# ======================================================================
# C) GLS Netherlands REST
# ======================================================================
def test_nl_validate_login_and_customer_no(nl):
    data = nl.validate_login()
    assert data["customers"][0]["subjects"][0]["custNos"] == ["1234567"]
    assert nl.resolve_customer_no() == "1234567"


def test_nl_create_label_returns_units(nl):
    result = nl.create_label({
        "reference": "E2E-NL",
        "shiptype": "p",
        "addresses": {"deliveryAddress": {"name1": "NL Alici", "zipCode": "1012AB",
                                          "city": "Amsterdam", "countryCode": "NL"}},
        "units": [{"weight": 1.5}],
    })
    assert result["units"][0]["unitNo"]
    assert result["units"][0]["label"]
    assert "labels" not in result
    assert result["shipmentTrackingLink"]


def test_nl_validation_error_envelope(nl):
    with pytest.raises(GLSNLError) as exc:
        nl.create_label({"reference": "BOZUK", "units": [{"weight": 1}]})
    assert exc.value.status == 400
    assert "addresses.deliveryAddress" in exc.value.errors


def test_nl_auth_error_when_credentials_empty(nl):
    nl.username, nl.password = "", ""
    with pytest.raises(GLSNLError) as exc:
        nl.validate_login()
    assert exc.value.is_auth_error
    assert "NL_USERNAME/NL_PASSWORD" in str(exc.value)


def test_nl_parcel_shops_capped_at_ten(nl):
    shops = nl.get_parcel_shops("1012AB", "NL", amount=50)
    assert 0 < len(shops) <= 10


def test_nl_delete_label_stops_a_wrongly_printed_parcel(nl):
    """Yanlis basilan etiketi kapatmanin TEK yolu.

    API ile uretilen sevkiyatlar Print&Ship ekraninda hic gorunmez, bu yuzden
    elle silinemiyorlar (GLS Servicedesk, kayit M2608 0415).
    """
    unit_no = nl.create_label({
        "reference": "IPTAL", "shiptype": "p",
        "addresses": {"deliveryAddress": {"name1": "NL Alici", "zipCode": "1012AB",
                                          "city": "Amsterdam", "countryCode": "NL"}},
        "units": [{"weight": 1.5}],
    })["units"][0]["unitNo"]

    assert nl.delete_label(unit_no)["error"] is False


def test_nl_delete_label_needs_a_parcel(nl):
    """unitNo'suz cagri sessizce basarili SAYILMAMALI — yanlis koli silinebilir."""
    with pytest.raises(GLSNLError) as exc:
        nl.delete_label("")
    assert exc.value.status == 400


def test_nl_has_no_tracking_endpoint():
    """GLS NL etiket API'sinde parca sorgulama UC NOKTASI YOK (ayri host)."""
    assert not hasattr(GLSNLClient, "parcel_details")
