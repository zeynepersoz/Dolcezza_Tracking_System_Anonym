# -*- coding: utf-8 -*-
"""/problems ekrani ve sorunlu kargo siniflandirmasi testleri."""
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracking.db import ShipmentsDB, event_stamp
from web import deps
from web.routers import problems

TRACK_NO = "35000001406746"


@pytest.fixture
def db(tmp_path, monkeypatch):
    database = ShipmentsDB(path=tmp_path / "problems.db")
    monkeypatch.setattr(deps, "_shipments_db", database)
    monkeypatch.setattr(deps, "OUTPUT_DIR", tmp_path)
    database.add_parcel(tracking_no=TRACK_NO, channel="NL", reference="NL-1",
                        country="Netherlands", consignee_name="NL Alici",
                        status="in_transit")
    return database


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(problems.router)
    return TestClient(app)


# ======================================================================
# /problems ekrani
# ======================================================================
def test_problems_page_groups_by_kind(client, db):
    db.add_parcel(tracking_no="21569761233", channel="IE", country="Ireland",
                  consignee_name="A", status="exception")
    # "POD eksik" YALNIZCA NL kanalinda ve teslim edileli >= stale_days GUN
    # gecmisse (IE'de cekilebilir POD yok, taze teslimatta POD henuz olusmamis
    # olabilir — bkz. problem_parcels, kullanici 2026-09-04).
    db.add_parcel(tracking_no="38120177761234", channel="NL", country="Netherlands",
                  consignee_name="B", status="delivered",
                  delivered_date="2020-01-01T10:00:00Z")   # POD yok, eski

    counts = db.problem_counts()
    assert counts["exception"] >= 1
    assert counts["pod_missing"] >= 1
    assert counts["total"] == sum(counts[k] for k in db.PROBLEM_KINDS)

    body = client.get("/problems").text
    assert "21569761233" in body
    assert "38120177761234" in body

    only_exception = client.get("/problems?kind=exception").text
    assert "21569761233" in only_exception
    assert "21569761234" not in only_exception


def test_explanation_carries_the_last_event_date(client, db):
    """Tarih ayri bir sutunda degil aciklamanin ICINDE — ERP ve Excel'le ayni metin."""
    db.update_status(TRACK_NO, "exception")
    db.update_tracking_info(TRACK_NO, issue_text="Consignee absent",
                            last_event_at="2026-08-14T19:48:42.724Z")
    # 19:48Z -> 22:48 Istanbul (bkz. timez).
    assert "Consignee absent · 14.08.2026 22:48" in client.get("/problems").text


@pytest.mark.parametrize("stamp, beklenen", [
    ("2026-08-14T19:48:42.724Z", "14.08.2026 22:48"),   # GLS'in damgasi
    ("2026-08-14T19:48:42", "14.08.2026 22:48"),        # bizim `_now()`
    ("2026-08-14", ""),                                 # saat yok
    ("", ""),
    (None, ""),
    ("bozuk", ""),
])
def test_event_stamp_accepts_both_formats_and_survives_junk(stamp, beklenen):
    """Damga Europe/Istanbul'a cevrilir — depolama UTC, gosterim yerel."""
    assert event_stamp(stamp) == beklenen


def test_gls_inquiry_lists_only_the_parcels_without_a_pod(client, db):
    """Mektup GLS'e gidiyor: dili panelden bagimsiz Ingilizce, icerigi POD'u eksik olanlar."""
    db.add_parcel(tracking_no="21569761234", channel="IE", country="Ireland",
                  consignee_name="B", store_code="NUNAT1N", invoice_number="5000202",
                  status="delivered", delivered_date="2020-01-01T10:00:00Z")
    db.add_parcel(tracking_no="21569761235", channel="IE", country="Ireland",
                  consignee_name="C", status="exception")   # baska bir sorun -> girmez

    body = client.get("/problems/gls-inquiry").text
    assert "Proof of Delivery" in body
    assert "21569761234" in body and "NUNAT1N-5000202" in body
    assert "21569761235" not in body


def test_gls_inquiry_waits_before_asking(client, db):
    """POD teslimattan saatler sonra olusabiliyor — bugun teslim edileni sormayiz."""
    db.add_parcel(tracking_no="21569761236", channel="IE", country="Ireland",
                  consignee_name="D", status="delivered",
                  delivered_date=datetime.now().isoformat(timespec="seconds"))
    assert "21569761236" not in client.get("/problems/gls-inquiry").text


def test_gls_inquiry_sends_nothing(client, db, monkeypatch):
    """Kullanicinin acik istegi: "su an yapicaz bunu sadece henuz otomatize olmayacak"."""
    from notify import mail

    def _patlasin(*args, **kwargs):
        raise AssertionError("panel GLS'e kendiliginden mail atmamali")

    monkeypatch.setattr(mail, "send", _patlasin)
    assert client.get("/problems/gls-inquiry").status_code == 200


def test_created_parcel_is_not_called_stale(db):
    """GLS koliyi henuz teslim almadiysa "hareketsiz" degildir — sadece bizdedir.

    Eski sorgu 'created'i de tariyordu ve canlida 240 satirin 235'i bu yuzden
    anlamsizdi; liste kullanilamaz hale gelmisti.
    """
    db.add_parcel(tracking_no="38120177000900", channel="NL", status="created",
                  shipment_date="2020-01-01")
    kinds = {r["tracking_no"]: r["problem_kind"] for r in db.problem_parcels()}
    assert kinds["38120177000900"] == "not_handed_over"
    assert db.problem_counts()["stale"] == 0


def test_a_damaged_parcel_on_the_move_gets_its_own_kind(db):
    """Hasarli koli yoluna devam ediyor: statu bozulmaz ama listeden dusmez.

    Canli ornek 38120177013009 — GLS'in kendi sitesi "bugun teslim" derken
    panelde ve ERP'de "EXCEPTION" yaziyordu.
    """
    db.add_parcel(tracking_no="38120177000907", channel="NL", status="out_for_delivery")
    db.update_tracking_info("38120177000907", issue_text="Inbound damaged (Turku FI2020)")
    kinds = {r["tracking_no"]: r["problem_kind"] for r in db.problem_parcels()}
    assert kinds["38120177000907"] == "damaged"


def test_an_exception_ages_out_of_the_problem_list(db):
    """Kullanici 2026-09-04: sinir otesi (FR/ES/BE) koliler GLS portalinde
    'delivered' gorunurken `api.gls.nl` beslemesi 'depoda' kaliyor ve
    exception sonsuza dek listede duruyordu. Son olaydan EXCEPTION_MAX_DAYS
    gun sonra 'Sorunlu Kargolar'dan duser (canli takip listesinde kalir)."""
    db.add_parcel(tracking_no="38120177000909", channel="NL", status="exception")
    db.update_tracking_info(
        "38120177000909", issue_text="The parcel is stored in the parcel center.",
        last_event_at=(datetime.now() - __import__("datetime").timedelta(days=30))
                      .isoformat(timespec="seconds"))
    assert db.problem_counts()["exception"] == 0
    # Kisa pencereyle (2 gun) yeni bir exception hala listede.
    db.add_parcel(tracking_no="38120177000910", channel="NL", status="exception")
    db.update_tracking_info("38120177000910", issue_text="Consignee absent",
                            last_event_at=datetime.now().isoformat(timespec="seconds"))
    kinds = {r["tracking_no"]: r["problem_kind"]
             for r in db.problem_parcels(exception_max_days=2)}
    assert kinds.get("38120177000909") is None
    assert kinds.get("38120177000910") == "exception"


def test_stale_is_measured_from_handover_not_from_the_last_event(db):
    """GLS takilan koliye de gurultu olayi yaziyor ("Action list item revised").

    Canli ornek 38120177007725: 23 Tem GLS'e verildi, hala teslim edilmedi, ama
    son olay BUGUNUN tarihli. Son olaya bakan bir kural bu koliyi hic gormez.
    """
    db.add_parcel(tracking_no="38120177000905", channel="NL", status="in_transit")
    db.update_tracking_info("38120177000905", handed_over_at="2020-01-01T09:00:00Z",
                            last_event_at=datetime.now().isoformat(timespec="seconds"))
    kinds = {r["tracking_no"]: r["problem_kind"] for r in db.problem_parcels()}
    assert kinds["38120177000905"] == "stale"


def test_handed_over_parcel_is_not_called_not_handed_over(db):
    """GLS 'state' alanini koli yola ciktiktan sonra da "Announced" birakabiliyor."""
    db.add_parcel(tracking_no="38120177000906", channel="NL", status="created",
                  shipment_date="2020-01-01")
    db.update_tracking_info("38120177000906", handed_over_at="2020-01-02T09:00:00Z")
    kinds = {r["tracking_no"]: r["problem_kind"] for r in db.problem_parcels()}
    assert kinds["38120177000906"] == "stale"


def test_issue_text_is_cleared_when_the_problem_goes_away(db):
    """Resepsiyon kapaliydi, ertesi gun teslim edildi — parca listede kalmamali."""
    db.add_parcel(tracking_no="38120177000907", channel="NL", status="exception")
    db.update_tracking_info("38120177000907", issue_text="reception was closed (Madrid ES)")
    assert db.get("38120177000907")["issue_text"]
    db.update_tracking_info("38120177000907", issue_text="")
    assert db.get("38120177000907")["issue_text"] == ""


def test_partial_delivery_flags_the_missing_box(db):
    """Ayni faturanin 2 kolisinden 1'i teslim -> digeri 'partial'."""
    for no, status in (("38120177000901", "delivered"), ("38120177000902", "in_transit")):
        db.add_parcel(tracking_no=no, channel="NL", status=status,
                      store_code="NUNAT1N", invoice_number="5000202")
    # Teslim edilen koliyi ESKI yap: yoksa "POD eksik" gun kapisina takilip
    # listeye hic girmez (bkz. problem_parcels, 2026-09-04).
    db.update_tracking_info("38120177000901", delivered_date="2020-01-01T10:00:00Z")
    kinds = {r["tracking_no"]: r["problem_kind"] for r in db.problem_parcels()}
    assert kinds["38120177000902"] == "partial"
    # Teslim edilen koli POD'u olmadigi icin listede, ama 'partial' olarak degil.
    assert kinds["38120177000901"] == "pod_missing"


def test_fully_delivered_invoice_is_not_partial(db):
    for no in ("38120177000903", "38120177000904"):
        db.add_parcel(tracking_no=no, channel="NL", status="delivered",
                      store_code="NUNAT1N", invoice_number="5000203")
    assert db.problem_counts()["partial"] == 0


def test_cancelled_parcel_leaves_the_tracking_queue(db):
    """Panelden `Label/Delete` gonderilen etiket geri gelmez; sormaya gerek yok."""
    db.add_parcel(tracking_no="38120177000905", channel="NL", status="created")
    db.update_status("38120177000905", "cancelled")
    assert "38120177000905" not in [p["tracking_no"] for p in db.active_parcels()]


def test_returned_parcel_leaves_tracking_and_appears_in_problems(db):
    """Gondericiye iade edilen koli takip kuyrugundan cikar, sorunlu listesinde 'returned' olur."""
    db.add_parcel(tracking_no="38120177000999", channel="NL", status="in_transit")
    db.update_status("38120177000999", "returned")
    assert "38120177000999" not in [p["tracking_no"] for p in db.active_parcels()]
    kinds = {r["tracking_no"]: r["problem_kind"] for r in db.problem_parcels()}
    assert kinds["38120177000999"] == "returned"
    assert db.problem_counts()["returned"] >= 1

