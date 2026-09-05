# -*- coding: utf-8 -*-
import pytest
from notify.analytics_report import render_html, send_analytics_email, get_default_report_payload
from gls_api import config


def test_analytics_report_render():
    payload = get_default_report_payload()
    html = render_html(payload)
    assert "DOLCEZZA EUROPE LOGISTICS REPORT" in html
    assert "FA26 Sevkiyat &amp; Teslimat Süreleri Analizi" in html or "FA26 Sevkiyat & Teslimat" in html
    assert "863" in html
    assert "14.4 Gün" in html
    assert "3.2 Gün" in html
    assert "87 Kargo" in html
    assert "FR" in html
    assert "DE" in html
    assert "4th Truck Shipment" in html
    assert "BEGES1N" in html


def test_analytics_report_send_mock(monkeypatch):
    config.apply_overrides({"MAIL_SENDER": "analyzer@dolcezza.com.tr"})
    ok, msg = send_analytics_email(["kayhan.belek@dolcezza.com.tr", "zeynep.ersoz@dolcezza.com.tr"])
    assert ok is True
