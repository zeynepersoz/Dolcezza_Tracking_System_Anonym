# -*- coding: utf-8 -*-
"""GLS akışını mock modda uçtan uca doğrulayan senaryolar — kimlik gelmeden
tüm hattın çalıştığını gösterir.

Üç kanal, üç farklı akış (mimari bunu zorunlu kılıyor):

  ShipIT : etiket -> endofday -> takip -> POD (PDF) -> ZIP arşivi
  T&T    : takip -> POD (PDF)                      (etiket üretmez)
  GLS NL : etiket (takip/POD ayrı hostlardan çekilir, bkz. nl_track_client)

Gerçek kimlikler geldiğinde aynı senaryolar `GLS_MODE=test` ile de çalışır —
kod değişmez, sadece .env değişir.

Çalıştırma: pytest -q tests/test_e2e_mock_flow.py
"""
import os
import zipfile
from io import BytesIO

import pytest
from pypdf import PdfReader

from dispatch import engine
from dispatch.plan import build_plan
from gls_api.shipit_client import ShipITClient
from gls_api.tt_soap_client import TrackTraceClient
from gls_api.nl_client import GLSNLClient
from tracking.db import ShipmentsDB
from tracking.scheduler import map_shipit_details, map_tt_detail

# Port conftest.py tarafindan secilir (bkz. oradaki aciklama).
BASE = f"http://127.0.0.1:{os.environ['GLS_MOCK_PORT']}"


@pytest.fixture
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "e2e_shipments.db")


@pytest.fixture
def shipit():
    return ShipITClient(base_url=f"{BASE}/shipit")


def _ie_shipment():
    return {
        "Shipment": {
            "Product": "PARCEL",
            "Shipper": {"Address": {"Name1": "EMC Test Sender", "CountryCode": "IE"}},
            "Consignee": {"Address": {"Name1": "Test Consignee", "CountryCode": "IE"}},
            "ShipmentUnit": [{"Weight": 2.0}],
            "ShipmentReference": ["E2E-TEST"],
        }
    }


# ======================================================================
# ShipIT: etiket -> endofday -> takip -> POD -> ZIP
# ======================================================================
def test_shipit_flow_label_to_pod_zip(tmp_path, db, shipit):
    # 1) Etiket olustur (web/routers/labels.py ile ayni adim)
    result = shipit.create_parcels(_ie_shipment())
    track_id = str(result["CreatedShipment"]["ParcelData"][0]["TrackID"])
    db.add_parcel(tracking_no=track_id, channel="IE", reference="E2E-TEST",
                  country="IE", consignee_name="Test Consignee", status="created")

    # 2) Gun sonu raporu — bu calismadan parca takipte GORUNMEZ
    assert track_id in shipit.end_of_day_report()["EndOfDay"]["Parcels"]

    # 3) Takip taramasi (tracking/scheduler.py:Tracker.tick ile ayni mantik)
    info = map_shipit_details(shipit.parcel_details(track_id))
    assert info["status"] == "delivered"
    assert info["note"]
    assert db.update_status(track_id, info["status"], note=info["note"]) is True

    # 4) POD indir (web/routers/pod.py:_fetch_pod_bytes ile ayni akis)
    pdf_bytes = shipit.parcel_pod(track_id)
    assert pdf_bytes.startswith(b"%PDF")
    pod_path = tmp_path / f"{track_id}.pdf"
    pod_path.write_bytes(pdf_bytes)
    db.set_pod_path(track_id, str(pod_path), "pdf")
    assert db.get(track_id)["pod_path"] == str(pod_path)
    assert db.get(track_id)["pod_media_type"] == "pdf"

    # 5) Toplu ZIP arsivi (pod.py:download_pod_bulk ile ayni sema)
    buf = BytesIO()
    arcname = f"Genel Sevkiyatlar/Ireland/{track_id}.pdf"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(arcname, pod_path.read_bytes())
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        assert arcname in zf.namelist()
        assert zf.read(arcname).startswith(b"%PDF")


# ======================================================================
# Track & Trace: takip -> POD
# ======================================================================
def test_tt_flow_tracking_to_pod(tmp_path, db):
    tt = TrackTraceClient(endpoint=f"{BASE}/tt/services/Tracking",
                          username="mock", password="mock")
    track_id = "21569761234"
    db.add_parcel(tracking_no=track_id, channel="IE", reference="E2E-TT",
                  country="IE", consignee_name="J. DOE", status="in_transit")

    info = map_tt_detail(tt.get_tu_detail(track_id))
    assert info["status"] == "delivered"
    assert db.update_status(track_id, info["status"], note=info["note"]) is True

    data, filename = tt.get_tu_pod(track_id)
    assert data.startswith(b"%PDF")
    pod_path = tmp_path / filename
    pod_path.write_bytes(data)
    db.set_pod_path(track_id, str(pod_path), "pdf")
    assert db.get(track_id)["pod_path"] == str(pod_path)


# ======================================================================
# GLS NL: etiket
# ======================================================================
def test_nl_flow_label_is_created_and_recorded(db):
    nl = GLSNLClient(base_url=f"{BASE}/nl", username="mock", password="mock")

    result = nl.create_label({
        "reference": "E2E-NL",
        "shiptype": "p",
        "addresses": {"deliveryAddress": {"name1": "NL Consignee", "zipCode": "1012AB",
                                          "city": "Amsterdam", "countryCode": "NL"}},
        "units": [{"weight": 1.5}],
    })
    unit_no = str(result["units"][0]["unitNo"])
    db.add_parcel(tracking_no=unit_no, channel="NL", reference="E2E-NL",
                  country="NL", consignee_name="NL Consignee", status="created")

    row = db.get(unit_no)
    assert row["channel"] == "NL"
    assert row["status"] == "created"


# ======================================================================
# GLS NL: toplu etiket — paketleme tarihinden PDF klasorune
# ======================================================================
def test_bulk_flow_turns_a_packing_day_into_one_pdf_per_store(db, tmp_path):
    """Sentez satirlari -> plan -> GLS -> `2026-08-14 NL label/NUNAT1N.pdf`.

    Gercek `GLSNLClient` ile mock sunucuya cikar: `FakeGLS` kullanan birim
    testlerin aksine base64 cozme ve cok sayfali PDF birlestirme de dogrulanir.
    """
    rows = [
        {"rec_id": "1", "box_code": "8682500091", "store_code": "NUNAT1N",
         "invoice": "5000402", "weight_kg": 17.0, "customer_name": "Ahatler",
         "country": "AT"},
        {"rec_id": "2", "box_code": "8682500092", "store_code": "NUNAT1N",
         "invoice": "5000419", "weight_kg": 14.0, "customer_name": "Ahatler",
         "country": "AT"},
        {"rec_id": "3", "box_code": "8682500093", "store_code": "TTTGB1P",
         "invoice": "5000500", "weight_kg": 9.0, "customer_name": "Irish",
         "country": "IE"},
    ]
    address = {"name": "Ahatler", "street": "Hauptplatz", "house_number": "1",
               "postal_code": "1010", "city": "Wien", "country": "AT"}
    plan = build_plan(rows, lambda code: [address] if code == "NUNAT1N" else [])

    # Irlanda kodu kapsam disi kalmali — GLS NL'e hic gitmemeli.
    assert [s.store_code for s in plan.skipped] == ["TTTGB1P"]

    nl = GLSNLClient(base_url=f"{BASE}/nl", username="mock", password="mock")
    out = engine.run(plan.sendable, client=nl, db=db, day="2026-08-14",
                     base_dir=tmp_path / "labels")

    assert len(out.created) == 1 and not out.failed
    created = out.created[0]
    # Iki fatura tek adreste birlestirildi.
    assert created.reference == "5000402+5000419"
    # Dosya adi magaza kodu + fatura, klasor adi paketleme tarihi.
    assert created.pdf_path.endswith("/2026-08-14 NL label/NUNAT1N-5000402-5000419.pdf")
    # Iki koli -> iki sayfa, iki takip numarasi.
    assert len(PdfReader(created.pdf_path).pages) == 2
    assert len(created.tracking_numbers) == 2

    parcel = db.get(created.tracking_numbers[0])
    assert parcel["store_code"] == "NUNAT1N"
    assert parcel["status"] == "created"

    # Ayni gun ikinci kez calistirilirsa CIFT ETIKET basilmamali.
    again = build_plan(rows, lambda code: [address],
                       already_labeled=db.labeled_box_ids("2026-08-14"))
    assert again.sendable == []
