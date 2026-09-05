# -*- coding: utf-8 -*-
"""Yanlis basilan etiketi kapatma ucu.

API ile uretilen sevkiyatlar GLS'in Print&Ship ekraninda hic gorunmez, bu yuzden
elle silinemezler (GLS Servicedesk, kayit M2608 0415) — bu uc TEK yoldur ve
GERI ALINAMAZ. O yuzden burada asil sinanan sey "iptal calisiyor mu" degil,
YANLISLIKLA calismamasi.

Calistirma: pytest -q
"""
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from tracking.db import ShipmentsDB
from web import deps
from web.routers import tracking as tracking_router

NO = "38120177011043"


class FakeNL:
    """Cagirilan takip numaralarini biriktirir — GLS'e gidildi mi, gorelim."""

    def __init__(self):
        self.deleted = []

    def delete_label(self, unit_no):
        self.deleted.append(unit_no)
        return {"error": False}


@pytest.fixture
def gls(monkeypatch):
    client = FakeNL()
    monkeypatch.setattr(tracking_router.providers, "strict_provider_for",
                        lambda channel: ("nl", client))
    return client


@pytest.fixture
def db(tmp_path, monkeypatch):
    s = ShipmentsDB(path=tmp_path / "t.db")
    monkeypatch.setattr(deps, "_shipments_db", s)
    return s


def _parcel(db, tracking_no=NO, *, status="created", channel="NL"):
    db.add_parcel(tracking_no=tracking_no, channel=channel, reference="5000173",
                  country="SE", consignee_name="Eckerlunds Klader AB",
                  status=status, store_code="RPXSE1N", invoice_number="5000173")


def _client(role: str = "operator") -> TestClient:
    app = FastAPI()
    # `web/main.py` ile ayni: router'in TAMAMI oturum arkasinda. Detay sablonu
    # iptal dugmesini `request.state.user.role`'e gore gosterir, o da bu
    # bagimlilikta kurulur.
    app.include_router(tracking_router.router, dependencies=[Depends(get_current_user)])

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "test", "role": role}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app)


@pytest.fixture
def client():
    return _client()


# ------------------------------------------------------------------ mutlu yol
def test_cancelling_closes_the_label_at_gls(client, db, gls):
    _parcel(db)

    client.post(f"/tracking/cancel/{NO}")

    assert gls.deleted == [NO]


def test_the_parcel_is_removed_from_our_database(client, db, gls):
    """Kullanici karari (2026-09-01): silinen etiket bizde `cancelled` olarak
    DURMAZ, kayit tamamen kalkar — "GLS'te kalabilir ama bizim db'de hic
    olmamasi lazim". Koli GLS takibinde gorunmeye devam eder."""
    _parcel(db)

    client.post(f"/tracking/cancel/{NO}")

    assert db.get(NO) is None


def test_the_deletion_is_written_to_the_log_not_the_parcel(caplog, client, db, gls):
    """Kayit gittigi icin gecmis satiri tutulamaz; kim sildi bilgisi LOG'a
    yazilir — aylar sonra "bu koli neden yok" sorusunun tek izi odur."""
    import logging
    _parcel(db)

    with caplog.at_level(logging.INFO, logger="web.routers.tracking"):
        client.post(f"/tracking/cancel/{NO}")

    assert any(NO in r.getMessage() and "test" in r.getMessage()
               for r in caplog.records)


# ----------------------------------------------------------------- korumalar
def test_a_parcel_on_its_way_is_not_cancelled(client, db, gls):
    """GLS zaten reddeder ama istek hic gitmemeli: operator "iptal ettim" sanmasin."""
    _parcel(db, status="in_transit")

    response = client.post(f"/tracking/cancel/{NO}")

    assert response.status_code == 400
    assert gls.deleted == []
    assert db.get(NO)["status"] == "in_transit"


def test_an_irish_parcel_is_not_sent_to_the_dutch_endpoint(client, db, gls):
    _parcel(db, tracking_no="21561234567", channel="IE")

    response = client.post("/tracking/cancel/21561234567")

    assert response.status_code == 400
    assert gls.deleted == []


def test_an_unknown_tracking_number_is_a_404(client, db, gls):
    response = client.post(f"/tracking/cancel/{NO}")

    assert response.status_code == 404
    assert gls.deleted == []


def test_a_viewer_cannot_cancel(db, gls):
    """Etiket basma yetkisi olmayan iptal de edemez."""
    _parcel(db)

    response = _client(role="viewer").post(f"/tracking/cancel/{NO}")

    assert response.status_code == 403
    assert gls.deleted == []


def test_a_failed_gls_call_leaves_the_label_valid(client, db, monkeypatch):
    """GLS patlarsa panelde "iptal edildi" yazip gecerli etiket birakmayalim."""
    _parcel(db)

    def boom(unit_no):
        raise RuntimeError("GLS 500")

    monkeypatch.setattr(tracking_router.providers, "strict_provider_for",
                        lambda channel: ("nl", type("C", (), {"delete_label": staticmethod(boom)})()))

    with pytest.raises(RuntimeError):
        client.post(f"/tracking/cancel/{NO}")

    assert db.get(NO)["status"] == "created"


# -------------------------------------------------------------------- ekran
def test_the_button_needs_two_steps_before_it_fires(client, db, gls):
    """Tek tik yetmez: once uyari kutusu acilir, sonra tarayici onayi sorulur."""
    _parcel(db)

    html = client.get(f"/tracking/detail/{NO}").text

    assert "confirming = true" in html          # 1. adim: kutuyu ac
    assert "hx-confirm" in html                 # 2. adim: tarayici onayi
    assert f"/tracking/cancel/{NO}" in html


def test_a_delivered_parcel_has_no_cancel_button(client, db, gls):
    _parcel(db, status="delivered")

    html = client.get(f"/tracking/detail/{NO}").text

    assert f"/tracking/cancel/{NO}" not in html


# --------------------------------------------------------- kayit tamamen silinir
# Kullanici karari (2026-09-01): "biz labeli silersek bizden komple silinsin,
# GLS'te kalabilir ama bizim db'de hic olmamasi lazim, o yuzden sorgular
# koliden olmali".

def test_the_status_history_is_removed_too(db):
    """Olaylar oksuz satir olarak kalmamali (SQLite'ta CASCADE kapali)."""
    pid = db.add_parcel(tracking_no="38120177000901", channel="NL",
                        consignee_name="c", country="FR", status="created")
    db.update_status("38120177000901", "in_transit", note="yolda")

    assert db.delete_parcel("38120177000901") is True
    left = db.conn.execute("SELECT COUNT(*) FROM status_events WHERE parcel_id = ?",
                           (pid,)).fetchone()[0]
    assert left == 0


def test_deleting_frees_the_box_for_a_new_label(db):
    """`box_rec_id` de gittigi icin koli toplu etikette YENIDEN gorunur —
    etiketi silinmis koli yeniden etiketlenebilmelidir."""
    db.add_parcel(tracking_no="38120177000902", channel="NL", consignee_name="c",
                  country="FR", status="created", shipment_date="2026-09-04")
    db.set_label_path("38120177000902", "/tmp/y.pdf", box_rec_id="143998")
    assert "143998" in db.labeled_box_ids("2026-09-04")

    db.delete_parcel("38120177000902")

    assert "143998" not in db.labeled_box_ids("2026-09-04")


def test_deleting_an_unknown_parcel_is_a_no_op(db):
    assert db.delete_parcel("99999999999999") is False


# ------------------------------------------------- ERP'deki takip no da silinir
# Kullanici kurali (2026-09-01): "delete label kullanilirsa ERP'den silinmesi
# lazim takip numarasinin" — aksi halde koli ERP'de etiketli gorunmeye devam
# eder ve toplu etiket listesine bir daha HIC dusmez.

def test_deleting_a_label_clears_the_tracking_number_in_erp(client, db, gls, monkeypatch):
    from web.routers import tracking as tracking_router

    calls = []
    monkeypatch.setattr(tracking_router.writeback, "clear_tracking_number",
                        lambda box, no: calls.append((box, no)) or True)

    _parcel(db)
    db.set_label_path(NO, "/tmp/x.pdf", box_rec_id="143729")

    client.post(f"/tracking/cancel/{NO}")

    assert calls == [("143729", NO)]


def test_erp_is_not_touched_when_the_parcel_has_no_box_record(client, db, gls, monkeypatch):
    """Elle girilmis / ERP kolisi olmayan kayitta bosuna MSSQL'e gidilmez."""
    from web.routers import tracking as tracking_router

    monkeypatch.setattr(tracking_router.writeback, "clear_tracking_number",
                        lambda box, no: pytest.fail("koli kaydi yokken ERP'ye gidilmemeliydi"))

    _parcel(db)          # box_rec_id yok

    assert client.post(f"/tracking/cancel/{NO}").status_code == 200
    assert db.get(NO) is None


def test_the_erp_clear_runs_before_the_local_row_disappears(client, db, gls, monkeypatch):
    """`box_rec_id` yerel kayitta durur; once silinirse ERP'yi temizlemek icin
    gereken kimlik kaybolurdu."""
    from web.routers import tracking as tracking_router

    seen = {}
    def fake_clear(box, no):
        seen["parcel_still_there"] = db.get(no) is not None
        return True
    monkeypatch.setattr(tracking_router.writeback, "clear_tracking_number", fake_clear)

    _parcel(db)
    db.set_label_path(NO, "/tmp/x.pdf", box_rec_id="143729")

    client.post(f"/tracking/cancel/{NO}")

    assert seen["parcel_still_there"] is True
