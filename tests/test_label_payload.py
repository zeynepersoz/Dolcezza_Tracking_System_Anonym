# -*- coding: utf-8 -*-
"""Etikete ne basildigi.

GLS NL alanlari uzunluk asiminda kirpmaz, sevkiyati 400 ile REDDEDER. Musteri
listemizin 261 unvanindan 28'i `name1` sinirini (30) asiyor — kirpma olmadan o
sevkiyatlar toplu etiket sirasinda tek tek patlar.

Etiketin sol alt kosesinde ayri bir "not" alani YOKTUR (2026-08-17'de canli
ucta dogrulandi): "Note:" satirlari `name2` ve `reference`'in yankisidir.
Calistirma: pytest -q
"""
import pytest

from dispatch.engine import _payload
from dispatch.plan import Box, Shipment
from gls_api.label_payload import (NL_MAX, NL_REFERENCE_MAX, address_to_nl,
                                    build_nl_payload, build_shipit_payload)

UZUN = "Lili Mode-Boutique am Goldenen Reiter"   # 37 karakter, gercek musteri


def _nl(**addr):
    return address_to_nl({"company": "Mode Francine", "street": "Hoofdstraat",
                          "postal_code": "1012AB", "city": "Amsterdam",
                          "country": "NL", **addr})


# ------------------------------------------------------------------ uzun unvan
def test_a_long_name_is_not_thrown_away():
    """Tasan kisim silinmez, `contact` satirinda devam eder."""
    address = _nl(company=UZUN)

    assert address["name1"] + " " + address["contact"] == UZUN


def test_a_long_name_is_split_between_words():
    """Kelime ortasindan bolunen unvan etikette okunmaz hale gelir."""
    address = _nl(company=UZUN)

    assert address["name1"] == "Lili Mode-Boutique am Goldenen"
    assert address["contact"] == "Reiter"


def test_a_single_word_too_long_is_cut_rather_than_rejected():
    """Boslugu olmayan unvanda sert kesmek, GLS'in etiketi reddetmesinden iyidir."""
    address = _nl(company="A" * 40)

    assert len(address["name1"]) == NL_MAX["name1"]


def test_a_short_name_is_repeated_on_the_contact_line():
    """Etiketteki "Contact:" satiri bos kalmasin."""
    address = _nl(company="Mode Francine")

    assert address["name1"] == "Mode Francine"
    assert address["contact"] == "Mode Francine"


def test_a_real_contact_person_is_kept():
    """Adres defterine kisi adi girildiyse unvan onun yerini almaz."""
    address = _nl(company="Mode Francine", contact="Francine Dubois")

    assert address["contact"] == "Francine Dubois"


@pytest.mark.parametrize("field", ["name1", "name2", "name3", "street", "city"])
def test_no_field_exceeds_the_gls_limit(field):
    address = _nl(company="A" * 80, name2="B" * 80, name3="C" * 80,
                  street="D" * 80, city="E" * 80)

    assert len(address[field]) <= NL_MAX[field]


# -------------------------------------------------------------------- referans
def test_a_merged_reference_cannot_break_the_label():
    """Birlestirilmis sevkiyatta referans "fatura1+fatura2" olur ve 20'yi asabilir."""
    payload = build_nl_payload({}, {}, reference="8682500093774+8682500093775")

    assert len(payload["reference"]) <= NL_REFERENCE_MAX


# -------------------------------------------------------------------- Not1/Not2
def test_shipit_prints_notes_as_extra_reference_lines():
    """ShipIT `ShipmentReference` bir dizi — referans + Not1 + Not2 ayri satir."""
    payload = build_shipit_payload({}, {}, reference="SP26-4471",
                                   note1="Kirilir", note2="Ust uste koyma")

    assert payload["Shipment"]["ShipmentReference"] == [
        "SP26-4471", "Kirilir", "Ust uste koyma"]


def test_shipit_skips_empty_notes():
    """Bos Not satirlari etikete basilmaz."""
    payload = build_shipit_payload({}, {}, reference="SP26-4471", note1="", note2="Not2")

    assert payload["Shipment"]["ShipmentReference"] == ["SP26-4471", "Not2"]


def test_nl_merges_notes_into_the_single_reference_within_the_cap():
    """NL tek referans alani verir; referans + notlar 20 karaktere sigar."""
    payload = build_nl_payload({}, {}, reference="SP26", note1="Kirilir", note2="Dik")

    assert payload["reference"].startswith("SP26 Kirilir")
    assert len(payload["reference"]) <= NL_REFERENCE_MAX


# ------------------------------------------------------------- toplu akis eslemesi
def test_the_bulk_flow_prints_store_code_and_invoice():
    """name2 -> magaza kodu, reference -> fatura; ikisi de etiketin sol alt kosesinde."""
    shipment = Shipment(store_code="RPXSE1N", invoices=["5000173"],
                        boxes=[Box(rec_id="1", box_code="K1", store_code="RPXSE1N",
                                   invoice="5000173", weight_kg=2.0,
                                   customer_name=UZUN)],
                        address={"company": "Eski Unvan", "street": "Malmovagen",
                                 "postal_code": "27335", "city": "Tomelilla",
                                 "country": "SE"})

    delivery = _payload(shipment, {})["addresses"]["deliveryAddress"]

    assert delivery["name2"] == "RPXSE1N"
    assert _payload(shipment, {})["reference"] == "5000173"
    # Unvan ERP'den gelir ve adres defterindekini ezer — orasi bayatlayabiliyor.
    assert delivery["name1"] + " " + delivery["contact"] == UZUN


# ------------------------------------------------- ShipIT semasina uygunluk
# Alan adlari GLS'in resmi OpenAPI dosyasindan (shipit-farm.yaml, 2026-09-01).
# Bu builder CANLIDA HIC CALISMADI (ShipIT yapilandirilmamisti), bu yuzden
# sapmalar fark edilmemisti — yanlis adlar hata vermeden ATILIR.

def _adres():
    return {"name": "Test IE", "street": "Grafton St", "house_number": "22",
            "postal_code": "D02XY45", "city": "Dublin", "country": "IE",
            "email": "a@b.c", "phone": "123", "mobile": "456",
            "consignee_id": "X1", "contact": "Jane"}


def test_address_uses_the_exact_schema_field_names():
    from gls_api.label_payload import address_to_shipit
    a = address_to_shipit(_adres())

    assert a["Zipcode"] == "D02XY45"              # `ZIPCode` DEGIL
    assert a["Email"] == "a@b.c"                  # `eMail` DEGIL
    assert a["FixedLinePhonenumber"] == "123"     # `Phone` DEGIL
    assert a["MobilePhoneNumber"] == "456"        # `Mobile` DEGIL
    for gone in ("ZIPCode", "eMail", "Phone", "Mobile"):
        assert gone not in a


def test_consignee_id_is_not_part_of_the_address():
    """Semada `Consignee`nin `Address` ile KARDES alani."""
    from gls_api.label_payload import address_to_shipit, build_shipit_payload

    assert "ConsigneeID" not in address_to_shipit(_adres())
    p = build_shipit_payload({"name": "S"}, _adres(), "R1")
    assert p["Shipment"]["Consignee"]["ConsigneeID"] == "X1"


def test_the_shipper_carries_the_contact_id():
    """Hangi GLS hesabina basilacagini ContactID secer (GLS Ireland, 2026-09-01)."""
    from gls_api.label_payload import build_shipit_payload

    p = build_shipit_payload({"name": "S"}, _adres(), "R1", contact_id="372aaafNwL")
    assert p["Shipment"]["Shipper"]["ContactID"] == "372aaafNwL"


def test_printing_options_are_always_present():
    """`ShipmentRequestData`da ZORUNLU; hic gonderilmiyordu."""
    from gls_api.label_payload import build_shipit_payload

    p = build_shipit_payload({"name": "S"}, _adres(), "R1")
    assert p["PrintingOptions"]["ReturnLabels"]["LabelFormat"] == "PDF"


def test_services_use_the_schema_shape():
    from gls_api.label_payload import build_shipit_payload

    p = build_shipit_payload({"name": "S"}, _adres(), "R1", services=["FDS"])
    assert p["Shipment"]["Service"] == [{"Service": {"ServiceName": "FDS"}}]


def test_the_client_puts_printing_options_beside_shipment_not_inside():
    """Sema: `PrintingOptions` `ShipmentRequestData`nin alani, `Shipment`in DEGIL."""
    from gls_api.shipit_client import ShipITClient

    gonderilen = {}

    class C(ShipITClient):
        def _post(self, path, payload=None):
            gonderilen.update(yol=path, govde=payload)
            return {}

    c = C(base_url="http://x", username="u", password="p", contact_id="372aaafNwL")
    c.create_parcels({"Shipment": {"Product": "PARCEL", "Shipper": {"Address": {}}}})

    govde = gonderilen["govde"]
    assert "PrintingOptions" in govde                      # ust seviyede
    assert "PrintingOptions" not in govde["Shipment"]      # icinde DEGIL


def test_the_client_fills_the_contact_id_into_the_shipper():
    """Semada `ShipperAccount` diye bir alan YOK; ContactID Shipper'in alani."""
    from gls_api.shipit_client import ShipITClient

    gonderilen = {}

    class C(ShipITClient):
        def _post(self, path, payload=None):
            gonderilen.update(govde=payload)
            return {}

    c = C(base_url="http://x", username="u", password="p", contact_id="372aaafMF4")
    c.create_parcels({"Shipment": {"Product": "PARCEL", "Shipper": {"Address": {}}}})

    ship = gonderilen["govde"]["Shipment"]
    assert ship["Shipper"]["ContactID"] == "372aaafMF4"
    assert "ShipperAccount" not in ship


def test_an_explicit_contact_id_is_not_overwritten():
    """Cagiran hesabi acikca sectiyse (iki hesabimiz var) ezilmemeli."""
    from gls_api.shipit_client import ShipITClient

    gonderilen = {}

    class C(ShipITClient):
        def _post(self, path, payload=None):
            gonderilen.update(govde=payload)
            return {}

    c = C(base_url="http://x", username="u", password="p", contact_id="372aaafMF4")
    c.create_parcels({"Shipment": {"Shipper": {"ContactID": "372aaafNwL", "Address": {}}}})

    assert gonderilen["govde"]["Shipment"]["Shipper"]["ContactID"] == "372aaafNwL"
