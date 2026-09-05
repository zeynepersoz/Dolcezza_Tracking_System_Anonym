"""Toplu etiket motoru — GLS cagrisi, PDF yazimi, DB kaydi."""
import base64
from pathlib import Path
import io

import pytest
from pypdf import PdfReader

from dispatch.engine import box_label_dir, label_dir, run
from dispatch.plan import build_plan
from gls_api.mock_server import _mini_label_pdf
from tracking.db import ShipmentsDB

ADRES = {"name": "AHA Austria", "street": "Hauptplatz", "house_number": "1",
         "postal_code": "1010", "city": "Wien", "country": "AT"}


def _box(rec_id="1", store="NUNAT1N", invoice="5000402", weight=12.0, season=""):
    return {"rec_id": rec_id, "box_code": f"868250009{rec_id}", "store_code": store,
            "invoice": invoice, "weight_kg": weight, "dimensions": "60X42X46",
            "customer_name": "AHA Austria", "country": "AT", "season": season}


class FakeGLS:
    """Gercek GLS gibi: her koli KENDI tek sayfalik PDF'ini doner (birlesik yok)."""

    def __init__(self, fail_on=()):
        self.payloads = []
        self.fail_on = set(fail_on)
        self._next = 35000000000000
        self.confirmed = []

    def create_label(self, payload: dict) -> dict:
        self.payloads.append(payload)
        reference = payload["reference"]
        if reference in self.fail_on:
            raise RuntimeError("GLS: zipCode is invalid")
        units = []
        for unit in payload["units"]:
            self._next += 1
            unit_no = str(self._next)
            units.append({
                "unitId": unit.get("unitId"),
                "unitNo": unit_no,
                "label": base64.b64encode(_mini_label_pdf(unit_no)).decode(),
            })
        return {"error": False, "units": units}

    def confirm_label(self, unit_no, shipping_date=None):
        self.confirmed.append(str(unit_no))
        return {"error": False, "status": "200"}


@pytest.fixture
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "t.db")


def _run(rows, db, tmp_path, book=None, **kw):
    plan = build_plan(rows, lambda c: list((book or {"NUNAT1N": [ADRES]}).get(c, [])))
    return run(plan.sendable, client=FakeGLS(**kw), db=db,
               day="2026-08-14", base_dir=tmp_path / "labels")


# ---------- cikti dosyasi ----------

def test_one_shipment_makes_one_pdf_with_a_page_per_box(db, tmp_path):
    """Gercekte de boyle: portalden inen TSOEL1N.pdf 2 koli icin 2 sayfa."""
    out = _run([_box("1"), _box("2")], db, tmp_path)

    pdf = PdfReader(io.BytesIO(open(out.created[0].pdf_path, "rb").read()))
    assert len(pdf.pages) == 2


def test_pdf_is_named_after_the_store_code(db, tmp_path):
    out = _run([_box()], db, tmp_path)

    assert out.created[0].pdf_path.endswith("/NUNAT1N-5000402.pdf")


def test_folder_name_carries_the_packing_date():
    assert label_dir("2026-08-14").name == "2026-08-14 NL label"


# ---------- GLS govdesi ----------

def test_store_code_goes_to_name2_and_invoice_to_reference(db, tmp_path):
    """Etikette basilan iki alan — RPXSE1N.pdf ile dogrulandi."""
    client = FakeGLS()
    plan = build_plan([_box()], lambda c: [ADRES])
    run(plan.sendable, client=client, db=db, day="2026-08-14", base_dir=tmp_path / "l")

    body = client.payloads[0]
    assert body["addresses"]["deliveryAddress"]["name2"] == "NUNAT1N"
    assert body["reference"] == "5000402"


def test_each_box_is_sent_with_its_own_weight_and_barcode(db, tmp_path):
    """Tek agirligi koli sayisina bolmek yanlis navlun demek."""
    client = FakeGLS()
    plan = build_plan([_box("1", weight=17.0), _box("2", weight=14.0)],
                      lambda c: [ADRES])
    run(plan.sendable, client=client, db=db, day="2026-08-14", base_dir=tmp_path / "l")

    assert client.payloads[0]["units"] == [
        {"unitId": "8682500091", "weight": 17.0},
        {"unitId": "8682500092", "weight": 14.0},
    ]


def test_merged_invoices_are_written_to_the_reference(db, tmp_path):
    client = FakeGLS()
    plan = build_plan([_box("1", invoice="5000402"), _box("2", invoice="5000419")],
                      lambda c: [ADRES])
    run(plan.sendable, client=client, db=db, day="2026-08-14", base_dir=tmp_path / "l")

    assert client.payloads[0]["reference"] == "5000402+5000419"


# ---------- yerel kayit ----------

def test_every_box_becomes_a_tracked_parcel(db, tmp_path):
    out = _run([_box("1"), _box("2")], db, tmp_path)

    assert len(out.created[0].tracking_numbers) == 2
    parcel = db.get(out.created[0].tracking_numbers[0])
    assert parcel["store_code"] == "NUNAT1N"
    assert parcel["status"] == "created"
    # Kayda KOLININ KENDI etiketi baglanir (kullanici istegi, 2026-09-01):
    # arsivde her satir kendi belgesini acsin, sevkiyatin ortak cok sayfali
    # PDF'ini degil.
    assert parcel["label_pdf_path"].endswith("NUNAT1N-5000402 1st box.pdf")


def test_the_boxs_season_is_written_immediately(db, tmp_path):
    """Kullanici bildirdi (2026-09-02): "bunların sezonu niye yok" — sonraki
    ERP senkronu paketleme tarihi GECMISTE olan kolileri kapsar (bkz.
    erp/sync.py), ileri tarihli etiketler gunlerce sezonsuz kalirdi. Sezon
    artik etiket olusurken DOGRUDAN ERP satirindan yazilir."""
    out = _run([_box("1", season="FA26")], db, tmp_path)
    parcel = db.get(out.created[0].tracking_numbers[0])
    assert parcel["season"] == "FA26"


def test_a_box_without_a_season_leaves_it_blank_not_invented(db, tmp_path):
    out = _run([_box("1")], db, tmp_path)
    parcel = db.get(out.created[0].tracking_numbers[0])
    assert parcel["season"] == ""


def test_every_created_tracking_number_is_confirmed_with_gls(db, tmp_path):
    """Confirm cagrilmazsa etiket GLS'in kendi Shipments ekraninda gorunmuyor."""
    client = FakeGLS()
    plan = build_plan([_box("1"), _box("2")], lambda c: [ADRES])
    out = run(plan.sendable, client=client, db=db, day="2026-08-14", base_dir=tmp_path / "l")

    assert sorted(client.confirmed) == sorted(out.created[0].tracking_numbers)


def test_confirm_failure_does_not_fail_an_otherwise_created_shipment(db, tmp_path):
    """Onay ikincildir — etiket zaten olusturuldugu icin sevkiyat patlamaz.

    Ama SESSIZ de kalmaz: onaysiz koli GLS'in Shipments ekraninda gorunmez,
    operator "etiket olusmamis" sanip ikinci kez basabilir.
    """
    class ConfirmFailingGLS(FakeGLS):
        def confirm_label(self, unit_no, shipping_date=None):
            raise RuntimeError("GLS: confirm endpoint timed out")

    plan = build_plan([_box("1")], lambda c: [ADRES])
    out = run(plan.sendable, client=ConfirmFailingGLS(), db=db,
              day="2026-08-14", base_dir=tmp_path / "l")

    assert out.failed == []
    assert len(out.created[0].tracking_numbers) == 1
    assert out.created[0].unconfirmed == out.created[0].tracking_numbers


def test_labeled_boxes_are_remembered_so_the_second_run_skips_them(db, tmp_path):
    """Etiket geri alinamaz — ayni gun ikinci kez calistirmak cift etiket basmamali."""
    rows = [_box("1"), _box("2")]
    _run(rows, db, tmp_path)

    assert db.labeled_box_ids("2026-08-14") == {"1", "2"}
    plan = build_plan(rows, lambda c: [ADRES],
                      already_labeled=db.labeled_box_ids("2026-08-14"))
    assert plan.shipments == []


# ---------- hata dayanikliligi ----------

def test_one_failing_shipment_does_not_stop_the_others(db, tmp_path):
    rows = [_box("1", store="QQQDE1N", invoice="9999999"), _box("2", store="NUNAT1N")]
    plan = build_plan(rows, lambda c: [ADRES])
    out = run(plan.sendable, client=FakeGLS(fail_on=["9999999"]), db=db,
              day="2026-08-14", base_dir=tmp_path / "l")

    assert [r.store_code for r in out.created] == ["NUNAT1N"]
    assert out.failed[0].store_code == "QQQDE1N"
    assert "zipCode" in out.failed[0].error


def test_created_tracking_numbers_reach_the_server_log(db, tmp_path, caplog):
    """Etiket geri alinamaz; tarayici penceresi kapaninca numaralar kaybolmamali."""
    with caplog.at_level("INFO", logger="dispatch"):
        out = _run([_box("1")], db, tmp_path)

    assert out.created[0].tracking_numbers[0] in caplog.text


def test_a_failed_shipment_is_logged_with_its_reason(db, tmp_path, caplog):
    with caplog.at_level("ERROR", logger="dispatch"):
        _run([_box("1", invoice="9999999")], db, tmp_path, fail_on=["9999999"])

    assert "zipCode" in caplog.text


def test_progress_is_reported_for_every_shipment(db, tmp_path):
    seen = []
    plan = build_plan([_box("1", store="QQQDE1N"), _box("2", store="NUNAT1N")],
                      lambda c: [ADRES])
    run(plan.sendable, client=FakeGLS(), db=db, day="2026-08-14",
        base_dir=tmp_path / "l", progress=lambda i, n, s: seen.append((i, n, s)))

    assert seen == [(1, 2, "NUNAT1N"), (2, 2, "QQQDE1N")]


# ------------------------------------------- koli basina etiket arsivi
# Kullanici istegi (2026-09-01): "her bir kolinin labeli pdf olarak dursun,
# nunat1n-5000000 1st box diye adlandirilsin, klasorlu bir sekilde tut:
# labels/FA26/04.09.2026/nunat1n".

def test_the_archive_tree_is_season_date_store(tmp_path, monkeypatch):
    from gls_api import config
    monkeypatch.setattr(config, "season_label", lambda: "FA26")

    d = box_label_dir("2026-09-04", "NUNAT1N", base=tmp_path)

    assert d == tmp_path / "labels" / "FA26" / "04.09.2026" / "NUNAT1N"


def test_each_box_gets_its_own_numbered_pdf(db, tmp_path, monkeypatch):
    from gls_api import config
    monkeypatch.setattr(config, "season_label", lambda: "FA26")

    out = _run([_box("1"), _box("2"), _box("3")], db, tmp_path)

    # `_run` taban olarak `tmp_path/labels` verir; yolu fonksiyondan turetiyoruz.
    store_dir = box_label_dir("2026-08-14", "NUNAT1N", base=tmp_path / "labels")
    names = sorted(p.name for p in store_dir.glob("*.pdf"))
    assert names == ["NUNAT1N-5000402 1st box.pdf",
                     "NUNAT1N-5000402 2nd box.pdf",
                     "NUNAT1N-5000402 3rd box.pdf"]
    # Her dosya TEK kolinin etiketi olmali, sevkiyatin tamami degil.
    from pypdf import PdfReader
    assert all(len(PdfReader(str(p)).pages) == 1 for p in store_dir.glob("*.pdf"))
    assert len(out.created[0].tracking_numbers) == 3


def test_each_parcel_points_at_its_own_label(db, tmp_path):
    """Arsivde ayni belge tekrar tekrar gorunmemeli — her satirin yolu FARKLI."""
    _run([_box("1"), _box("2")], db, tmp_path)

    paths = [db.get(t)["label_pdf_path"]
             for t in db.conn.execute(
                 "SELECT tracking_no FROM parcels ORDER BY tracking_no").fetchall()
             for t in (t[0],)]
    assert len(set(paths)) == len(paths)


def test_the_combined_shipment_pdf_is_still_written(db, tmp_path):
    """ZIP ve tek-PDF birlestirme O klasorden calisiyor — bozulmamali."""
    out = _run([_box("1"), _box("2")], db, tmp_path)

    combined = Path(out.created[0].pdf_path)
    assert combined.exists()
    assert combined.parent == label_dir("2026-08-14", tmp_path / "labels")
    from pypdf import PdfReader
    assert len(PdfReader(str(combined)).pages) == 2


def test_box_pdfs_never_escape_the_given_base(db, tmp_path):
    """Testler (ve farkli yapilandirmalar) gercek cikti klasorune yazmamali."""
    _run([_box("1")], db, tmp_path)

    for t in db.conn.execute("SELECT label_pdf_path FROM parcels").fetchall():
        assert str(tmp_path) in t[0]
