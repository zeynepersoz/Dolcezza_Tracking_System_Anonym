# -*- coding: utf-8 -*-
"""Tek etiket formu — koli basina AYRI agirlik.

Form uzun sure tek bir agirligi koli sayisina yayiyordu; 3 koli girildiginde
ucu de ayni kiloyla gidiyordu. Bu YANLIS NAVLUN demek: GLS her koliyi kendi
agirligiyla faturaliyor ve etiket geri alinamiyor. Toplu akis (dispatch/engine)
bunu bastan koli basina yolluyordu, tek etiket formu geride kalmisti.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from address_book.db import AddressBook
from tracking.db import ShipmentsDB
from web import deps
from web.routers import labels as labels_router

ALICI = {
    "consignee_name": "AHA Austria", "consignee_street": "Hauptplatz",
    "consignee_house_number": "1", "consignee_postal_code": "1010",
    "consignee_city": "Wien", "consignee_country": "AT",
}


class FakeNL:
    def __init__(self):
        self.payload = None
        self._next = 38120177000000

    def create_label(self, payload):
        self.payload = payload
        units = []
        for _ in payload["units"]:
            self._next += 1
            units.append({"unitNo": str(self._next)})
        return {"error": False, "units": units}

    def confirm_label(self, unit_no, shipping_date=None):
        return {"error": False}


class FakeShipIT:
    def __init__(self):
        self.payload = None

    def create_parcels(self, payload):
        self.payload = payload
        parcels = [{"TrackID": f"2156992788{i}"}
                   for i, _ in enumerate(payload["Shipment"]["ShipmentUnit"])]
        return {"CreatedShipment": {"ParcelData": parcels}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    s = ShipmentsDB(path=tmp_path / "t.db")
    monkeypatch.setattr(deps, "_shipments_db", s)
    monkeypatch.setattr(deps, "_address_book", AddressBook(db_path=tmp_path / "ab.db"))
    return s


@pytest.fixture
def gls(db, monkeypatch):
    client = FakeNL()
    monkeypatch.setattr(labels_router.providers, "label_provider_for",
                        lambda channel: ("nl", client))
    return client


def _post(weights: list[str], channel: str = "NL"):
    """Tarayicidaki tekrarlanan `weight_kg` alani = koli basina bir satir."""
    app = FastAPI()
    app.include_router(labels_router.router)
    return TestClient(app).post("/labels/create", data={
        "channel": channel, **ALICI, "weight_kg": weights})


def test_each_box_is_sent_with_its_own_weight(gls):
    _post(["17.0", "14.5"])

    assert gls.payload["units"] == [{"weight": 17.0}, {"weight": 14.5}]


def test_the_number_of_weight_rows_is_the_number_of_boxes(gls):
    response = _post(["5", "5", "5"])

    assert response.status_code == 200
    assert len(gls.payload["units"]) == 3


def test_every_box_is_tracked_with_the_weight_it_was_sent_with(gls, db):
    _post(["17.0", "14.5"])

    assert sorted(p["weight_kg"] for p in db.list_parcels()) == [14.5, 17.0]


def test_the_ie_channel_also_carries_per_box_weights(db, monkeypatch):
    """ShipIT govdesi de koli basina agirlik tasir — iki kanal tek formdan besleniyor."""
    client = FakeShipIT()
    monkeypatch.setattr(labels_router.providers, "label_provider_for",
                        lambda channel: ("shipit", client))

    _post(["17.0", "14.5"], channel="IE")

    assert client.payload["Shipment"]["ShipmentUnit"] == [
        {"Weight": 17.0}, {"Weight": 14.5}]


def test_a_box_with_an_impossible_weight_is_refused(gls):
    """Bir satir bozuksa HICBIR etiket basilmaz — etiket geri alinamaz."""
    response = _post(["12.0", "0"])

    assert response.status_code == 422
    assert gls.payload is None
