# -*- coding: utf-8 -*-
"""`/tracking/detail/{no}` acilista yaptigi canli yenileme.

Bu uc satira tiklaninca GLS NL'i tekrar sorar ve DB'yi gunceller — takip
turuyla AYNI kurallara uymali, yoksa turun zaten duzelttigi bir kaydi
buradan geri bozabilir (bkz. asagidaki test).
"""
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from tracking.db import ShipmentsDB
from web import deps
from web.routers import tracking as tracking_router

NO = "38120177011043"


@pytest.fixture
def db(tmp_path, monkeypatch):
    s = ShipmentsDB(path=tmp_path / "t.db")
    monkeypatch.setattr(deps, "_shipments_db", s)
    return s


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(tracking_router.router, dependencies=[Depends(get_current_user)])

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "test", "role": "operator"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app)


def _parcel(db, *, status="delivered"):
    db.add_parcel(tracking_no=NO, channel="NL", reference="5000173",
                  country="FR", consignee_name="Test", status=status,
                  store_code="GRFGFR1N", invoice_number="5000173")


class FakeNLTT:
    """Toplu uc sinir otesi teslimi bilmiyor ('depoda' takili); teslim
    tarama ucu (`delivery_details`) gercegi biliyor — canli olay 2026-09-04."""

    def __init__(self, delivered: bool):
        self.delivered = delivered

    def parcel_details(self, ids):
        return {NO: {"parcelNo": NO, "state": "NotDelivered", "events": [
            {"date": "2026-09-01T09:00:00Z",
             "descriptionEN": "The parcel has left the parcel center."}]}}

    def delivery_details(self, number, key=None):
        if not self.delivered:
            return {"deliveryScanInfo": {"isDelivered": False}}
        return {"deliveryScanInfo": {"isDelivered": True,
                                     "dateTime": "2026-09-04T12:00:00",
                                     "signedBy": "ROSSI"}}


def test_opening_the_panel_does_not_downgrade_a_crossborder_delivery(client, db, monkeypatch):
    """Kullanici 2026-09-04: liste 'delivered' gosterirken satira tiklaninca
    acilan panel 'in_transit' yaziyordu VE KAYDI OYLE EZIYORDU. Sebep: bu uc
    toplu ucun HAM (sinir otesi teslimi bilmeyen) cevabini dogrudan yaziyordu
    — takip turunun yaptigi `deliveryScanInfo` kontrolu burada yoktu."""
    _parcel(db, status="delivered")
    monkeypatch.setattr(tracking_router.providers, "tracking_provider_for",
                        lambda ch: ("nl_tt", FakeNLTT(delivered=True)))

    client.get(f"/tracking/detail/{NO}")

    assert db.get(NO)["status"] == "delivered"


def test_a_genuinely_undelivered_parcel_still_updates(client, db, monkeypatch):
    """Kontrol gercekten kurtarilmamis olani da eski haline dondurmemeli —
    toplu ucun statusu (in_transit) gecerli kalir."""
    _parcel(db, status="created")
    monkeypatch.setattr(tracking_router.providers, "tracking_provider_for",
                        lambda ch: ("nl_tt", FakeNLTT(delivered=False)))

    client.get(f"/tracking/detail/{NO}")

    assert db.get(NO)["status"] == "in_transit"
