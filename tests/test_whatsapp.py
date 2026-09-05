"""OpenWA WhatsApp bildirim testleri."""
from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest

import timez
from gls_api import config
from notify.whatsapp import (
    handle_tracker_event,
    is_eligible_date,
    notify_delivery,
    notify_problem,
    notify_returned,
    send_template,
    send_text,
)
from tracking.db import ShipmentsDB

# 2026-08-22 Cumartesi / 2026-08-24 Pazartesi, ikisi de Istanbul saatiyle.
_SATURDAY = datetime(2026, 8, 22, 12, 0, tzinfo=timez.TZ)


@pytest.fixture
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "shipments.db")


def test_send_template_payload_format():
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.text = '{"success": true}'

        with patch.object(config, "OPENWA_FORCE_LIVE", True):
            ok, text = send_template(
                template_name="delivery-confirmation",
                vars={"customerCode": "AHATEST", "seasonCode": "FA26", "trackingNumber": "8778676856765"},
                chat_id="100000000000001@g.us",
                url="http://192.0.2.10:2785",
                session_id="f246ef55-65db-4b23-b9b6-9ddad850ff32",
                api_key="owa_k1_test",
            )

        assert ok is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://192.0.2.10:2785/api/sessions/f246ef55-65db-4b23-b9b6-9ddad850ff32/messages/send-template"
        assert kwargs["headers"]["X-API-Key"] == "owa_k1_test"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs["json"] == {
            "chatId": "100000000000001@g.us",
            "templateName": "delivery-confirmation",
            "vars": {
                "customerCode": "AHATEST",
                "seasonCode": "FA26",
                "trackingNumber": "8778676856765",
            },
        }


def test_send_text_payload_format():
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.text = '{"success": true}'

        with patch.object(config, "OPENWA_FORCE_LIVE", True):
            ok, text = send_text(
                text="Test return message",
                chat_id="100000000000001@g.us",
                url="http://192.0.2.10:2785",
                session_id="f246ef55-65db-4b23-b9b6-9ddad850ff32",
                api_key="owa_k1_test",
            )

        assert ok is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://192.0.2.10:2785/api/sessions/f246ef55-65db-4b23-b9b6-9ddad850ff32/messages/send-text"
        assert kwargs["json"] == {
            "chatId": "100000000000001@g.us",
            "text": "Test return message",
            # Onizleme karti varsayilan olarak ACIK kalir; yalnizca FedEx
            # durum mesajlari kapatir (bkz. tests/test_notify.py).
            "linkPreview": True,
        }


def test_notify_delivery_extracts_correct_fields():
    """Artik OpenWA sablonu degil, serbest metin — SEVK tarihi mesajda olmali.

    Kullanici istegi (2026-08-27): once 'kayit tarihi' (created_at) denendi,
    ama bu bizim veritabanimiza NE ZAMAN eklendigimizi tutuyor, ERP'nin
    Sevk Tarihi'yle gunler farkli cikabiliyordu (canli ornek 38120177017908:
    created_at 18.08, shipment_date 14.08) — kullanici fark edip duzelttirdi.
    """
    parcel = {
        "tracking_no": "38120177012231",
        "store_code": "NUNAT1N",
        "season": "FA26",
        "consignee_name": "AHA Austria",
        "shipment_date": "2026-08-14",
        "created_at": "2026-08-18T07:51:20",
    }
    with patch("notify.whatsapp.send_text") as mock_send:
        mock_send.return_value = (True, "ok")
        ok, detail = notify_delivery(parcel)

        assert ok is True
        mock_send.assert_called_once()
        text = mock_send.call_args.args[0]
        assert "14.08.2026" in text     # sevk tarihi, kayit tarihi (18.08) DEGIL
        assert "18.08.2026" not in text
        assert "NUNAT1N" in text
        assert "FA26" in text
        assert "38120177012231" in text


def test_notify_delivery_handles_a_missing_shipment_date():
    parcel = {"tracking_no": "38120177012231", "store_code": "NUNAT1N", "season": "FA26"}
    with patch("notify.whatsapp.send_text") as mock_send:
        mock_send.return_value = (True, "ok")
        notify_delivery(parcel)
        assert "—" in mock_send.call_args.args[0]


def test_notify_problem_extracts_correct_fields():
    parcel = {
        "tracking_no": "38120177012231",
        "store_code": "NUNAT1N",
        "consignee_name": "AHA Austria",
        "explain": "The parcel could not be delivered as the reception was closed.",
    }
    with patch("notify.whatsapp.send_text") as mock_send:
        mock_send.return_value = (True, "ok")
        ok, detail = notify_problem(parcel)

        assert ok is True
        mock_send.assert_called_once()
        text_arg = mock_send.call_args[0][0]
        assert "NUNAT1N" in text_arg
        assert "38120177012231" in text_arg
        assert "Delivery Problem" in text_arg
        assert "reception was closed" in text_arg


def test_notify_returned_sends_distinct_message():
    parcel = {
        "tracking_no": "38120177012231",
        "store_code": "NUNAT1N",
        "season": "FA26",
        "consignee_name": "AHA Austria",
    }
    with patch("notify.whatsapp.send_text") as mock_send:
        mock_send.return_value = (True, "ok")
        ok, detail = notify_returned(parcel)

        assert ok is True
        mock_send.assert_called_once()
        text_arg = mock_send.call_args[0][0]
        assert "İade Edildi" in text_arg
        assert "NUNAT1N" in text_arg
        assert "38120177012231" in text_arg


def test_old_dates_are_not_eligible_for_notification():
    """Kullanici kurali: bugunden oncekiler icin mesaj gelmemeli."""
    old_parcel = {
        "delivered_date": "2026-08-18",
        "last_event_at": "2026-08-18 14:00",
    }
    fresh_parcel = {
        "delivered_date": "2026-08-21",
        "last_event_at": "2026-08-21 10:00",
    }

    assert is_eligible_date(old_parcel, kind="delivery", start_date="2026-08-21") is False
    assert is_eligible_date(fresh_parcel, kind="delivery", start_date="2026-08-21") is True
    assert is_eligible_date(old_parcel, kind="problem", start_date="2026-08-21") is False
    assert is_eligible_date(fresh_parcel, kind="problem", start_date="2026-08-21") is True
    assert is_eligible_date(old_parcel, kind="returned", start_date="2026-08-21") is False
    assert is_eligible_date(fresh_parcel, kind="returned", start_date="2026-08-21") is True


def test_handle_tracker_event_delivery_marks_wa_delivered(db):
    db.add_parcel(
        tracking_no="38120177012231",
        channel="NL",
        store_code="NUNAT1N",
        season="FA26",
        consignee_name="AHA Austria",
        status="in_transit",
    )
    db.update_tracking_info("38120177012231", delivered_date="2026-08-21")

    with patch("notify.whatsapp.notify_delivery") as mock_delivery:
        mock_delivery.return_value = (True, "sent")
        handle_tracker_event(db, {
            "tracking_no": "38120177012231",
            "old_status": "in_transit",
            "new_status": "delivered",
        })

        mock_delivery.assert_called_once()
        parcel = db.get("38120177012231")
        assert parcel["wa_delivered_at"] is not None

        # Tekrar durum olayi geldiginde ikinci kez GONDERILMEZ
        mock_delivery.reset_mock()
        handle_tracker_event(db, {
            "tracking_no": "38120177012231",
            "old_status": "delivered",
            "new_status": "delivered",
        })
        mock_delivery.assert_not_called()


def test_handle_tracker_event_problem_marks_wa_problem(db):
    db.add_parcel(
        tracking_no="38120177012231",
        channel="NL",
        store_code="NUNAT1N",
        consignee_name="AHA Austria",
        status="in_transit",
    )
    db.update_tracking_info("38120177012231", last_event_at="2026-08-21 11:00")

    with patch("notify.whatsapp.notify_problem") as mock_problem:
        mock_problem.return_value = (True, "sent")
        handle_tracker_event(db, {
            "tracking_no": "38120177012231",
            "old_status": "in_transit",
            "new_status": "exception",
        })

        mock_problem.assert_called_once()
        parcel = db.get("38120177012231")
        assert parcel["wa_problem_at"] is not None

        # Tekrar durum olayi geldiginde ikinci kez GONDERILMEZ
        mock_problem.reset_mock()
        handle_tracker_event(db, {
            "tracking_no": "38120177012231",
            "old_status": "exception",
            "new_status": "exception",
        })
        mock_problem.assert_not_called()


def test_handle_tracker_event_returned_marks_wa_returned(db):
    db.add_parcel(
        tracking_no="38120177012231",
        channel="NL",
        store_code="NUNAT1N",
        consignee_name="AHA Austria",
        status="in_transit",
    )
    db.update_tracking_info("38120177012231", last_event_at="2026-08-21 11:00")

    with patch("notify.whatsapp.notify_returned") as mock_returned:
        mock_returned.return_value = (True, "sent")
        handle_tracker_event(db, {
            "tracking_no": "38120177012231",
            "old_status": "in_transit",
            "new_status": "returned",
        })

        mock_returned.assert_called_once()
        parcel = db.get("38120177012231")
        assert parcel["wa_returned_at"] is not None

        # Tekrar durum olayi geldiginde ikinci kez GONDERILMEZ
        mock_returned.reset_mock()
        handle_tracker_event(db, {
            "tracking_no": "38120177012231",
            "old_status": "returned",
            "new_status": "returned",
        })
        mock_returned.assert_not_called()


def test_handle_tracker_event_weekend_skips_problem_and_returned(db):
    db.add_parcel(
        tracking_no="38120177012231",
        channel="NL",
        store_code="NUNAT1N",
        consignee_name="AHA Austria",
        status="in_transit",
    )
    db.update_tracking_info("38120177012231", last_event_at="2026-08-21 11:00")

    with patch("notify.whatsapp.timez.now", return_value=_SATURDAY), \
         patch("notify.whatsapp.notify_problem") as mock_problem, \
         patch("notify.whatsapp.notify_returned") as mock_returned:
        handle_tracker_event(db, {
            "tracking_no": "38120177012231",
            "old_status": "in_transit",
            "new_status": "exception",
        })
        mock_problem.assert_not_called()
        assert db.get("38120177012231")["wa_problem_at"] is None

        handle_tracker_event(db, {
            "tracking_no": "38120177012231",
            "old_status": "exception",
            "new_status": "returned",
        })
        mock_returned.assert_not_called()
        assert db.get("38120177012231")["wa_returned_at"] is None


def test_handle_tracker_event_weekend_still_sends_delivery(db):
    db.add_parcel(
        tracking_no="38120177012231",
        channel="NL",
        store_code="NUNAT1N",
        consignee_name="AHA Austria",
        status="in_transit",
    )
    db.update_tracking_info("38120177012231", last_event_at="2026-08-21 11:00")

    with patch("notify.whatsapp.timez.now", return_value=_SATURDAY), \
         patch("notify.whatsapp.notify_delivery") as mock_delivery:
        mock_delivery.return_value = (True, "sent")
        handle_tracker_event(db, {
            "tracking_no": "38120177012231",
            "old_status": "in_transit",
            "new_status": "delivered",
        })
        mock_delivery.assert_called_once()
        assert db.get("38120177012231")["wa_delivered_at"] is not None


# ======================================================================
# FedEx de eski-kayit korumasindan GECER (2026-09-03)
# ======================================================================
# 2026-09-02'de FedEx dali `handle_tracker_event` icinde erken `return`
# ediyordu ve `is_eligible_date` kontrolune HIC ulasmiyordu. Sonuc: 2025
# tarihli 285 koli ilk taramada "delivered" olunca tek saatte 207 mesaj gitti.
def test_old_fedex_delivery_does_not_notify(monkeypatch, tmp_path):
    from tracking.db import ShipmentsDB
    from notify import whatsapp as wa

    db = ShipmentsDB(path=tmp_path / "wa.db")
    db.add_parcel(tracking_no="886685651125", channel="FEDEX", country="RE",
                  consignee_name="c", status="delivered",
                  delivered_date="2025-11-28T10:00:00Z")

    monkeypatch.setattr(wa.config, "OPENWA_ENABLED", "1")
    monkeypatch.setattr(wa.config, "OPENWA_FEDEX_CHAT_ID", "grup@g.us")
    monkeypatch.setattr(wa.config, "OPENWA_START_DATE", "2026-08-21")
    monkeypatch.setattr(wa, "notify_fedex_status",
                        lambda *a, **kw: pytest.fail("eski kayit icin mesaj gitmemeli"))

    wa.handle_tracker_event(db, {"tracking_no": "886685651125", "channel": "FEDEX",
                                 "new_status": "delivered"})


def test_fresh_fedex_delivery_still_notifies(monkeypatch, tmp_path):
    from tracking.db import ShipmentsDB
    from notify import whatsapp as wa

    db = ShipmentsDB(path=tmp_path / "wa2.db")
    db.add_parcel(tracking_no="876634105532", channel="FEDEX", country="CA",
                  consignee_name="c", status="delivered",
                  delivered_date="2026-09-01T10:00:00Z")

    gonderildi = []
    monkeypatch.setattr(wa.config, "OPENWA_ENABLED", "1")
    monkeypatch.setattr(wa.config, "OPENWA_FEDEX_CHAT_ID", "grup@g.us")
    monkeypatch.setattr(wa.config, "OPENWA_START_DATE", "2026-08-21")
    monkeypatch.setattr(wa, "notify_fedex_status",
                        lambda p, **kw: gonderildi.append(p["tracking_no"]) or (True, ""))

    wa.handle_tracker_event(db, {"tracking_no": "876634105532", "channel": "FEDEX",
                                 "new_status": "delivered"})
    assert gonderildi == ["876634105532"]
