# -*- coding: utf-8 -*-
"""/dispatch uclari — onizleme, adres girisi, rol, cift is korumasi.

MSSQL yerine `erp.boxes` fonksiyonlari monkeypatch edilir: bu testler HTTP
katmanini dogrular, sorguyu `tests/test_erp_boxes.py` dogruluyor.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from address_book.db import AddressBook
from auth.dependencies import get_current_user
from tracking.db import ShipmentsDB
from web import deps
from web.routers import dispatch as dispatch_router

DAY = "2026-08-14"
ADRES = {"name": "Ahatler", "street": "Hauptplatz", "house_number": "1",
         "postal_code": "1010", "city": "Wien", "country": "AT"}


def _box(rec_id, store, invoice, weight=12.0):
    return {"rec_id": rec_id, "box_code": f"868250009{rec_id}", "store_code": store,
            "invoice": invoice, "weight_kg": weight, "dimensions": "60X42X46",
            "customer_name": store.title(), "country": "AT"}


ROWS = [
    _box("1", "NUNAT1N", "5000402"),
    _box("2", "WNPAT1N", "5000402"),          # ayni magaza, iki fatura
    _box("3", "WNPAT1N", "5000419"),
    _box("4", "SBEDE1N", "5000500"),          # adres defterinde yok
    _box("5", "TTTGB1P", "5000502"),          # Irlanda kodu — kapsam disi
]


@pytest.fixture
def book(tmp_path, monkeypatch):
    ab = AddressBook(db_path=tmp_path / "ab.db")
    for code in ("NUNAT1N", "WNPAT1N"):
        ab.add(name2=code, address_type="business", **ADRES)
    monkeypatch.setattr(deps, "_address_book", ab)
    return ab


@pytest.fixture
def db(tmp_path, monkeypatch):
    s = ShipmentsDB(path=tmp_path / "t.db")
    monkeypatch.setattr(deps, "_shipments_db", s)
    return s


@pytest.fixture
def client(book, db, monkeypatch):
    monkeypatch.setattr(dispatch_router, "_erp", lambda: object())
    monkeypatch.setattr(dispatch_router, "fetch_unlabeled_boxes",
                        lambda client, day: list(ROWS))
    return _client()


def _client(role: str = "operator") -> TestClient:
    app = FastAPI()
    app.include_router(dispatch_router.router)

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "test", "role": role}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app)


# ---------- sayfa ----------

def test_page_renders_sentez_dates_dropdown_when_available(client, monkeypatch):
    """Sentez'den donen etiketlenmemis koli paketleme tarihleri select menusu olarak sunulur."""
    monkeypatch.setattr(dispatch_router, "available_dates",
                        lambda client, days=30: [{"date": DAY, "boxes": 5, "stores": 3}])
    html = client.get("/dispatch", params={"day": DAY}).text

    assert '<select id="day"' in html
    assert 'name="day"' in html
    assert DAY in html
    assert "5 koli" in html


def test_page_falls_back_to_date_input_when_no_sentez_dates(client, monkeypatch):
    """Sentez'de bekleyen tarih yoksa serbest tarih secici sunulur."""
    monkeypatch.setattr(dispatch_router, "available_dates", lambda client, days=30: [])
    html = client.get("/dispatch").text

    assert '<input type="date"' in html
    assert 'name="day"' in html


# ---------- onizleme ----------

def test_preview_separates_ready_blocked_and_out_of_scope(client):
    html = client.get("/dispatch/rows", params={"day": DAY}).text

    assert "NUNAT1N" in html and "WNPAT1N" in html      # hazir
    assert "SBEDE1N" in html and "Adres yok" in html    # eksik
    assert "TTTGB1P" in html                            # kapsam disi


def test_two_invoices_of_one_store_are_merged_and_flagged(client):
    """Kullanicinin "sistem sorsun" kurali: birlestirme gorunur olmali."""
    html = client.get("/dispatch/rows", params={"day": DAY}).text

    assert "5000402+5000419" in html
    assert "fatura tek adreste birleştirildi" in html


def test_split_turns_the_merged_store_into_two_shipments(client):
    html = client.get("/dispatch/rows", params={"day": DAY, "split": "WNPAT1N"}).text

    assert "5000402+5000419" not in html
    assert html.count('name="stores" value="WNPAT1N"') == 2


def test_a_bad_date_is_rejected_before_it_reaches_sql(client):
    assert client.get("/dispatch/rows", params={"day": "2026-13-99"}).status_code == 400


# ---------- adres girisi ----------

def test_saving_a_missing_address_moves_the_store_to_ready(client, book):
    html = client.post("/dispatch/address", data={
        "day": DAY, "store_code": "SBEDE1N", "name": "Ford Moden",
        "street": "Hauptstr", "house_number": "5", "postal_code": "10115",
        "city": "Berlin", "country": "DE",
    }).text

    assert book.find_by_store_code("SBEDE1N")           # deftere yazildi
    assert "Adres yok" not in html                      # satir artik hazir


# ---------- is korumasi ----------

def test_a_second_job_is_refused_while_one_is_running(client):
    client.app.state.bulk_job = {"done": False}

    r = client.post("/dispatch/create", data={"day": DAY, "stores": ["NUNAT1N"]})

    assert r.status_code == 409


def test_creating_without_a_selection_is_refused(client):
    assert client.post("/dispatch/create", data={"day": DAY}).status_code == 400


# ---------- onizlemede adres duzenleme ----------

def _ship(store, reference, **addr):
    from types import SimpleNamespace
    base = {"street": "Hauptplatz", "house_number": "1", "city": "Wien",
            "region": "AT-9", "addition": "2.OG"}
    base.update(addr)
    return SimpleNamespace(store_code=store, reference=reference, address=base)


def test_apply_address_overrides_merges_only_the_edited_fields():
    """Kullanici 2026-09-03: 'adreste yapilan duzenleme etiket olustur
    butonunda aktive olacak'. Onizlemede degistirilen alanlar sevkiyat
    adresini ezer; digerleri (region/addition) korunur."""
    s = _ship("NUNAT1N", "5000402")
    dispatch_router._apply_address_overrides(
        [s], {"NUNAT1N::5000402": {"house_number": "299", "street": "Musterstrasse"}})
    assert s.address["house_number"] == "299"
    assert s.address["street"] == "Musterstrasse"
    assert s.address["region"] == "AT-9"        # dokunulmadi
    assert s.address["addition"] == "2.OG"

    # Eski (magaza bazli) anahtar da geriye uyum icin calisir.
    s2 = _ship("NUNAT1N", "5000402")
    dispatch_router._apply_address_overrides([s2], {"NUNAT1N": {"city": "Graz"}})
    assert s2.address["city"] == "Graz"

    # Eslesmeyen kod / adressiz sevkiyat sessizce atlanir.
    from types import SimpleNamespace
    dispatch_router._apply_address_overrides(
        [SimpleNamespace(store_code="X", reference="1", address=None)],
        {"X::1": {"street": "z"}})


def test_apply_address_overrides_edits_each_split_invoice_independently():
    """Kullanici 2026-09-03: fatura bazinda ayrilinca 'bir faturanin adresi
    farkliysa' yalnizca o sevkiyat degismeli."""
    a = _ship("WQSCZ1N", "5000937", street="Vzorova")
    b = _ship("WQSCZ1N", "5000938", street="Vzorova")
    dispatch_router._apply_address_overrides([a, b], {
        "WQSCZ1N::5000938": {"street": "Nadrazni", "house_number": "77"},
    })
    assert a.address["street"] == "Vzorova"          # dokunulmadi
    assert b.address["street"] == "Nadrazni"
    assert b.address["house_number"] == "77"


def test_create_accepts_a_malformed_overrides_blob_without_crashing(client):
    r = client.post("/dispatch/create", data={
        "day": DAY, "stores": ["NUNAT1N"], "overrides": "{not json"})
    assert r.status_code == 200          # yok sayilir, is yine baslar


# ---------- rol ----------

def test_viewers_cannot_reach_the_bulk_screen():
    assert _client(role="viewer").get("/dispatch").status_code == 403


# ------------------------------------------------------------------ arama
# Kullanici istegi (2026-09-01): "bir de search butonu istiyorum" — 30+
# magazalik bir gunde test edilecek birkac magazayi elle aramak zordu.

def test_the_ready_list_has_a_search_box(client):
    html = client.get("/dispatch/rows", params={"day": DAY}).text

    assert 'x-model="q"' in html
    assert "Mağaza kodu, unvan, şehir" in html


def test_every_ready_row_carries_a_searchable_key(client):
    """Suzme TARAYICIDA olur: her satir kendi aranabilir metnini tasir."""
    html = client.get("/dispatch/rows", params={"day": DAY}).text

    assert 'data-search="' in html
    assert 'x-show="hit($el)"' in html
    # Anahtar magaza kodunu KUCUK HARFLE tasimali (arama kucuk harfe cevrilir).
    assert "nunat1n" in html


def test_the_search_box_does_not_change_the_selection(client):
    """Suzgec yalnizca GORUNUMU daraltir; gizlenen magaza secili kalir ve
    etikete girer — sayac bu yuzden hep TUM secimi gosterir."""
    html = client.get("/dispatch/rows", params={"day": DAY}).text

    assert "selected.length + '/' + stores.length" in html
    # `q` secim dizisine HIC dokunmamali.
    assert "q: \"\"" in html


# ------------------------------------------------------- gonderici adresi
# Canli olcum (2026-09-01): varsayilan gonderici HIC ayarlanmamisti; uc
# sevkiyat da GLS'te "V009 ... The Name1/Street/City/ZipCode field is required"
# ile patladi ve kullanici yalnizca "gecersiz veri iceriyor" gordu — sorun
# ALICI adresinde sanildi. Artik GLS'e hic gidilmez.

def test_missing_shipper_fields_reports_exactly_what_gls_requires():
    from gls_api.label_payload import missing_shipper_fields

    assert missing_shipper_fields({}) == ["name1", "street", "city", "zipCode"]
    assert missing_shipper_fields(None) == ["name1", "street", "city", "zipCode"]


def test_a_complete_shipper_has_nothing_missing():
    from gls_api.label_payload import missing_shipper_fields

    assert missing_shipper_fields({
        "name": "Dolcezza", "street": "Nijverheidsweg-Noord", "house_number": "75",
        "postal_code": "3812PK", "city": "AMERSFOORT", "country": "NL",
    }) == []


def test_a_partial_shipper_names_only_the_gaps():
    from gls_api.label_payload import missing_shipper_fields

    assert missing_shipper_fields({"name": "Dolcezza", "city": "AMERSFOORT"}) == ["street", "zipCode"]


# ------------------------------------------------- birlesik PDF (tek yazdirma)
# Kullanici istegi (2026-09-01): "download all as PDF butonu da eklensin,
# tek seferde yazdirmak icin tum pdfler birlesmis olsun".

def _one_page_pdf() -> bytes:
    from fpdf import FPDF
    doc = FPDF()
    doc.add_page()
    return bytes(doc.output())


def test_all_labels_merge_into_a_single_pdf(client, tmp_path, monkeypatch):
    from dispatch import engine
    from pypdf import PdfReader
    import io

    day = "2026-09-04"
    directory = engine.label_dir(day, base=tmp_path)
    directory.mkdir(parents=True)
    for name in ("A-1.pdf", "B-2.pdf", "C-3.pdf"):
        (directory / name).write_bytes(_one_page_pdf())
    monkeypatch.setattr(engine, "label_dir", lambda d, base=None: directory)

    resp = client.post("/dispatch/download-pdf", data={"day": day})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert len(PdfReader(io.BytesIO(resp.content)).pages) == 3


def test_a_corrupt_label_is_skipped_instead_of_losing_the_whole_batch(client, tmp_path, monkeypatch):
    """30 sevkiyatlik bir yazdirmanin tek bozuk dosya yuzunden hic olmamasi
    daha kotudur — bozuk atlanir, kalani basilir."""
    from dispatch import engine
    from pypdf import PdfReader
    import io

    day = "2026-09-04"
    directory = engine.label_dir(day, base=tmp_path)
    directory.mkdir(parents=True)
    (directory / "A-1.pdf").write_bytes(_one_page_pdf())
    (directory / "B-bozuk.pdf").write_bytes(b"bu bir pdf degil")
    monkeypatch.setattr(engine, "label_dir", lambda d, base=None: directory)

    resp = client.post("/dispatch/download-pdf", data={"day": day})

    assert resp.status_code == 200
    assert len(PdfReader(io.BytesIO(resp.content)).pages) == 1


def test_merging_with_no_labels_is_a_404(client, tmp_path, monkeypatch):
    from dispatch import engine

    directory = engine.label_dir("2026-09-04", base=tmp_path)
    directory.mkdir(parents=True)
    monkeypatch.setattr(engine, "label_dir", lambda d, base=None: directory)

    assert client.post("/dispatch/download-pdf", data={"day": "2026-09-04"}).status_code == 404


def test_the_merged_pdf_keeps_the_same_order_as_the_zip(client, tmp_path, monkeypatch):
    """Iki indirme ayni gunu AYNI duzende versin; yoksa yazdirilan deste ile
    arsivdeki dosyalar karsilastirilamaz."""
    from dispatch import engine

    day = "2026-09-04"
    directory = engine.label_dir(day, base=tmp_path)
    directory.mkdir(parents=True)
    for name in ("FGLFR1N-5000887.pdf", "ULQFR1N-5000886.pdf", "YNOFR1N-5000885.pdf"):
        (directory / name).write_bytes(_one_page_pdf())
    monkeypatch.setattr(engine, "label_dir", lambda d, base=None: directory)

    import zipfile, io
    zip_names = zipfile.ZipFile(io.BytesIO(
        client.post("/dispatch/download-zip", data={"day": day}).content)).namelist()

    assert zip_names == sorted(zip_names)   # ikisi de `sorted` kullanir


# ------------------------------------------- "bu parti" vs "gunun tamami"
# Kullanici bildirdi (2026-09-01): tek dugme gunun TAMAMINI veriyordu.
# Uğur'un ifadesiyle: "4 Eylul'deki hepsini mi, simdi yazdirilan 2 tane mi".

def test_the_batch_pdf_contains_only_the_given_parcels(client, db, tmp_path, monkeypatch):
    from dispatch import engine
    from pypdf import PdfReader
    import io

    day = "2026-09-04"
    directory = engine.label_dir(day, base=tmp_path)
    directory.mkdir(parents=True)
    # Gunun klasorunde UC etiket var ama biz yalnizca IKISINI istiyoruz.
    for name in ("A.pdf", "B.pdf", "C.pdf"):
        (directory / name).write_bytes(_one_page_pdf())
    monkeypatch.setattr(engine, "label_dir", lambda d, base=None: directory)

    for no, fname in (("38120177000101", "A.pdf"), ("38120177000102", "B.pdf")):
        db.add_parcel(no, "NL", store_code="S1", consignee_name="c",
                      country="FR", shipment_date=day)
        db.set_label_path(no, str(directory / fname))

    resp = client.post("/dispatch/download-pdf",
                       data={"day": day,
                             "tracking_nos": ["38120177000101", "38120177000102"]})

    assert resp.status_code == 200
    assert len(PdfReader(io.BytesIO(resp.content)).pages) == 2      # 3 degil


def test_without_tracking_numbers_the_whole_day_is_returned(client, db, tmp_path, monkeypatch):
    """Eski davranis korunur: parametre yoksa gunun tamami iner."""
    from dispatch import engine
    from pypdf import PdfReader
    import io

    day = "2026-09-04"
    directory = engine.label_dir(day, base=tmp_path)
    directory.mkdir(parents=True)
    for name in ("A.pdf", "B.pdf", "C.pdf"):
        (directory / name).write_bytes(_one_page_pdf())
    monkeypatch.setattr(engine, "label_dir", lambda d, base=None: directory)

    resp = client.post("/dispatch/download-pdf", data={"day": day})

    assert len(PdfReader(io.BytesIO(resp.content)).pages) == 3


def test_a_forged_path_cannot_be_downloaded(client, db, tmp_path, monkeypatch):
    """Yol istemciden GELMEZ: takip no dogrulanip DB'den okunur. Uydurma bir
    numara hicbir dosya getirmemeli."""
    from dispatch import engine

    day = "2026-09-04"
    directory = engine.label_dir(day, base=tmp_path)
    directory.mkdir(parents=True)
    monkeypatch.setattr(engine, "label_dir", lambda d, base=None: directory)

    resp = client.post("/dispatch/download-pdf",
                       data={"day": day, "tracking_nos": ["../../etc/passwd", "99999999999999"]})

    assert resp.status_code == 404
