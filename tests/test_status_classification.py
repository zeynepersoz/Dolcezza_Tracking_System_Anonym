# -*- coding: utf-8 -*-
"""`classify_event_text` — GLS olay metninden statu tespiti.

Calistirma: pytest -q tests/test_status_classification.py
"""
from tracking.db import classify_event_text


def test_a_routine_address_correction_is_not_an_exception():
    """Cıplak "address" kelimesi EXCEPTION_PHRASES'te YOKTU olmamali.

    Canli olcum (2026-08-26): "Change completed for Delivery address" —
    GLS'in rutin bir kayit-kapatma olayi, sorun degil — yalnizca "address"
    gectigi icin yanlislikla "exception" cikiyordu. Bu olay dogru siniftaki
    "change completed" ifadesiyle IN_TRANSIT_PHRASES'e girmeli.
    """
    assert classify_event_text("Change completed for Delivery address") == "in_transit"


def test_a_genuine_address_problem_is_still_an_exception():
    """Duzeltme asiri gitmemeli: gercek adres sorunlari hala yakalanmali."""
    assert classify_event_text(
        "The parcel is stored in the final parcel center. It could not be "
        "delivered as further address information is needed."
    ) == "exception"
    assert classify_event_text("Delivery failed: incorrect address") == "exception"


def test_reached_the_parcel_center_is_in_transit():
    """Sorun geçmişte, ama SON olay masumsa statu de masum olmali."""
    assert classify_event_text("The parcel has reached the parcel center.") == "in_transit"


def test_fedex_on_vehicle_for_delivery_counts_as_out_for_delivery():
    """Canli olcum (2026-08-31, gercek FedEx production API, 876365567966):
    FedEx'in kendi `latestStatusDetail`i "Out for delivery" diyordu ama tarama
    metni GLS'in "out for delivery" ifadesini hic kullanmiyor — bu ifade
    olmadan kargo yanlislikla "in_transit"e dusuyordu."""
    assert classify_event_text("On FedEx vehicle for delivery") == "out_for_delivery"


# ------------------------------------------- etiket basildi ama GLS ALMADI
# Kullanici bildirdi (2026-09-01): "bugun olusturulan toplu etiketteki koliler
# neden in transit gorunuyor" + "GLS koliyi 4'unde almayacak, etiketi olusturduk,
# GLS alinca okutulma gorunur". O gun basilan 72 etiketin hepsi yanlisti.

def test_a_label_that_gls_has_not_collected_is_still_created():
    """GLS'in metni: koli gonderici tarafindan ALINMAK UZERE hazirlandi."""
    assert classify_event_text(
        "The parcel was provided by the sender for collection by GLS.") == "created"


def test_not_yet_handed_over_is_not_in_transit():
    """"not yet handed over" ifadesi IN_TRANSIT'teki "handed over" alt dizesine
    takiliyor ve OLUMSUZLAMA yok sayiliyordu."""
    assert classify_event_text(
        "The parcel data was entered into the GLS IT system; "
        "the parcel was not yet handed over to GLS.") == "created"


def test_a_real_handover_is_still_in_transit():
    """Guvenlik agi: GERCEK devralma hala yolda sayilmali."""
    assert classify_event_text("The parcel was handed over to GLS.") == "in_transit"
    assert classify_event_text("The parcel has reached the parcel center.") == "in_transit"


def test_fedex_information_sent_is_not_in_transit():
    """FedEx karsiligi: bilgi gonderildi, koli HENUZ ALINMADI (canli olcum,
    2026-09-01: 876572877080 panelde "Yolda" gorunuyordu)."""
    assert classify_event_text("Shipment information sent to FedEx") == "created"
