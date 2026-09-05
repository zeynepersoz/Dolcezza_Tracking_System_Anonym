# -*- coding: utf-8 -*-
"""Kısmi teslimat WhatsApp bildirim testleri."""
from unittest.mock import patch
import pytest

from notify.partial_delivery import (
    find_partial_deliveries,
    format_partial_delivery_message,
    run_partial_deliveries_daily,
)
from tracking.db import ShipmentsDB


@pytest.fixture
def db(tmp_path):
    d = ShipmentsDB(path=tmp_path / "shipments.db")
    # Fatura 5000495 (TOPESCN): 2 teslim edildi, 1 bekliyor -> Kısmi teslimat!
    d.add_parcel("38120177020694", channel="NL", store_code="TOPESCN", invoice_number="5000495",
                 status="delivered", consignee_name="TOP TRES FASHION", country="CI",
                 delivered_date="2026-08-21T07:39:00Z", shipment_date="2026-08-15")
    d.add_parcel("38120177020755", channel="NL", store_code="TOPESCN", invoice_number="5000495",
                 status="delivered", consignee_name="TOP TRES FASHION", country="CI",
                 delivered_date="2026-08-21T07:39:00Z")
    d.add_parcel("38120177020748", channel="NL", store_code="TOPESCN", invoice_number="5000495",
                 status="out_for_delivery", consignee_name="TOP TRES FASHION", country="CI")
    d.update_tracking_info("38120177020748", last_event_text="The parcel is expected to be delivered during the day.")

    # Fatura 5000999: 2 koli var, 2'si de teslim edilmiş -> Kısmi DEĞİL (Tamamlandı)
    d.add_parcel("38120177000001", channel="NL", store_code="ALLOK", invoice_number="5000999",
                 status="delivered", consignee_name="All Done", country="DE")
    d.add_parcel("38120177000002", channel="NL", store_code="ALLOK", invoice_number="5000999",
                 status="delivered", consignee_name="All Done", country="DE")
    return d


def test_find_partial_deliveries(db):
    partials = find_partial_deliveries(db)
    assert len(partials) == 1
    p = partials[0]
    assert p["invoice_number"] == "5000495"
    assert p["store_code"] == "TOPESCN"
    assert p["total_count"] == 3
    assert p["delivered_count"] == 2
    assert p["pending_count"] == 1


def test_format_partial_delivery_message(db):
    partials = find_partial_deliveries(db)
    msg = format_partial_delivery_message(partials[0])
    assert "Kısmi Teslimat Bildirimi" in msg
    assert "TOPESCN" in msg
    assert "5000495" in msg
    assert "Toplam 3 koliden *2* tanesi teslim edildi, *1* koli teslim bekliyor." in msg
    assert "38120177020694" in msg
    assert "38120177020748" in msg
    assert "The parcel is expected to be delivered during the day." in msg
    # Sevk tarihi de mesajda (kullanici karari 2026-09-02: "gönderi tarihini
    # de ekleyelim").
    assert "Sevk Tarihi:* 15.08.2026" in msg


def test_run_partial_deliveries_daily(db, monkeypatch):
    from gls_api import config
    from notify import whatsapp
    monkeypatch.setattr(config, "OPENWA_ENABLED", "1")
    monkeypatch.setattr(config, "OPENWA_CHAT_ID", "100000000000001@g.us")
    monkeypatch.setattr(config, "MODE", "mock")
    monkeypatch.setattr(whatsapp, "_is_weekend", lambda: False)

    with patch("notify.whatsapp.send_text", return_value=(True, "mock_sent")) as mock_send:
        sent, errs = run_partial_deliveries_daily(db)
        assert sent == 1
        assert len(errs) == 0
        assert mock_send.called


def test_run_partial_deliveries_daily_is_skipped_on_the_weekend(db, monkeypatch):
    """Canli olcum (2026-08-29/30, kullanici bildirdi): ayni icerikli kismi
    teslimat mesaji hem Cumartesi hem Pazar 18:00'de gitmisti — bu is gunluk
    cron uzerinden gittigi icin `handle_tracker_event`in hafta sonu kuralinin
    disinda kalmisti."""
    from gls_api import config
    from notify import whatsapp
    monkeypatch.setattr(config, "OPENWA_ENABLED", "1")
    monkeypatch.setattr(config, "OPENWA_CHAT_ID", "100000000000001@g.us")
    monkeypatch.setattr(config, "MODE", "mock")
    monkeypatch.setattr(whatsapp, "_is_weekend", lambda: True)

    with patch("notify.whatsapp.send_text", return_value=(True, "mock_sent")) as mock_send:
        sent, errs = run_partial_deliveries_daily(db)
        assert sent == 0
        assert errs == []
        assert not mock_send.called
