# -*- coding: utf-8 -*-
"""/archive — diskte belgesi olan parcalari listeler ve indirtir.

Neden var: POD ve etiket PDF'leri her zaman diske yaziliyordu ama onlara
ulasmanin tek yolu dogru gunu/magazayi tahmin etmekti. Buradaki testler
sayfanin YALNIZCA dosyasi olani gosterdigini ve indirme baglantisinin
gercekten o dosyayi verdigini kanitlar.
"""
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from gls_api import config
from tracking.db import ShipmentsDB
from web import deps
from web.routers import archive as archive_router


SHEET = "Vision_FA26_Boxed_EUROPE"


@pytest.fixture
def db(tmp_path, monkeypatch):
    s = ShipmentsDB(path=tmp_path / "arsiv.db")
    monkeypatch.setattr(deps, "_shipments_db", s)

    s.add_parcel("38120177000001", "NL", store_code="NUNAT1N", invoice_number="5000402",
                 country="AT", source_sheet=SHEET)
    s.set_pod_path("38120177000001", str(tmp_path / "pod.png"), "png")

    s.add_parcel("38120177000002", "NL", store_code="WNPAT1N", invoice_number="5000419",
                 country="AT", source_sheet=SHEET, shipment_date="2026-08-14")
    s.set_label_path("38120177000002", str(tmp_path / "WNPAT1N-5000419.pdf"))

    s.add_parcel("38120177000003", "NL", store_code="SBEDE1N", invoice_number="5000500",
                 country="DE", source_sheet=SHEET, shipment_date="2026-08-15")
    s.set_pod_path("38120177000003", str(tmp_path / "pod3.png"), "png")
    s.set_label_path("38120177000003", str(tmp_path / "SBEDE1N-5000500.pdf"))

    # Belgesiz parca — arsivde HIC gorunmemeli.
    s.add_parcel("38120177000004", "NL", store_code="BOSXX1N", invoice_number="5000600",
                 country="DE", source_sheet=SHEET)
    return s


def leaf(country: str, store: str, **extra) -> dict:
    """POD dalinin en dibi — dosya listesi ancak sezon+ulke+magaza secilince cikar."""
    return {"kind": "pod", "season": "FA26", "country": country, "store": store, **extra}


def label_leaf(day: str, store: str, **extra) -> dict:
    """Etiket dalinin en dibi. Diskteki duzenin ayni (kullanici istegi,
    2026-09-01): `labels/{SEZON}/{GG.AA.YYYY}/{MAGAZA}/`."""
    return {"kind": "label", "season": "FA26", "day": day, "store": store, **extra}


@pytest.fixture
def client(db):
    app = FastAPI()
    # Router korumasi `web/main.py`deki gibi verilir: `request.state.user`
    # ancak bu bagimlilik calisinca dolar. Etiket dali role gore suzuldugu icin
    # (bkz. `_allowed_kinds`) rolsuz bir istek POD daliyla sinirli kalirdi.
    app.include_router(archive_router.router, dependencies=[Depends(get_current_user)])

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "test", "role": "operator"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app)


# ---------------------------------------------------------------- klasorler

def test_the_archive_opens_on_the_two_document_types(client):
    """POD ve etiket ayni klasorde gorunmemeli (kullanici istegi): kok artik
    sezon degil BELGE TURU seviyesidir."""
    html = client.get("/archive").text

    assert "/archive?kind=pod" in html and "/archive?kind=label" in html
    assert "FA26" not in html          # sezon bir alt seviyede
    assert "38120177000001" not in html


def test_the_pod_branch_opens_season_country_store(client):
    """POD dali: Tur > Sezon > Ulke > Magaza > dosyalar."""
    seasons = client.get("/archive", params={"kind": "pod"}).text
    countries = client.get("/archive", params={"kind": "pod", "season": "FA26"}).text
    stores = client.get("/archive", params={"kind": "pod", "season": "FA26",
                                            "country": "AT"}).text
    files = client.get("/archive", params=leaf("AT", "NUNAT1N")).text

    assert "kind=pod&amp;season=FA26" in seasons
    assert "season=FA26&amp;country=AT" in countries
    assert "country=AT&amp;store=NUNAT1N" in stores
    assert "WNPAT1N" not in files and "38120177000001" in files


def test_the_label_branch_mirrors_the_folders_on_disk(client):
    """Etiket dali diskteki duzenin ayni (kullanici istegi, 2026-09-01):
    sezon -> gun -> magaza. Onceden yalnizca gun vardi, magaza klasoru yoktu."""
    seasons = client.get("/archive", params={"kind": "label"}).text
    days = client.get("/archive", params={"kind": "label", "season": "FA26"}).text
    stores = client.get("/archive",
                        params={"kind": "label", "season": "FA26", "day": "2026-08-14"}).text
    files = client.get("/archive", params=label_leaf("2026-08-14", "WNPAT1N")).text

    assert "FA26" in seasons
    assert "2026-08-14" in days and "2026-08-15" in days
    assert "WNPAT1N" in stores
    assert "38120177000002" in files
    assert "38120177000003" not in files  # baska gunun/magazanin etiketi


def test_a_folder_counts_only_parcels_that_have_a_document(db):
    """Kart '3 belge' deyip icinde 2 satir cikarsa sayfaya guven biter —
    sayim ile liste ayni suzgecten gecmeli."""
    counts = {f["name"]: f["n"] for f in db.archive_folders("country")}

    assert counts == {"AT": 2, "DE": 1}  # DE'nin belgesiz parcasi sayilmaz


def test_the_kind_reaches_the_folders_too(db):
    """Tur yalnizca dosya listesine uygulansaydi, etiket dalindaki klasore
    girildiginde bos liste cikardi."""
    counts = {f["name"]: f["n"] for f in db.archive_folders("store", kind="label")}

    assert counts == {"SBEDE1N": 1, "WNPAT1N": 1}  # NUNAT1N yalnizca POD


def test_the_day_folders_count_only_labels(db):
    """POD'u olan ama etiketi olmayan parca gun klasorunu sismanlatmamali."""
    counts = {f["name"]: f["n"] for f in db.archive_folders("day", kind="label")}

    assert counts == {"2026-08-14": 1, "2026-08-15": 1}


def test_an_unknown_level_cannot_reach_the_query(db):
    """Klasor adi istekten geliyor; kolon adi ASLA gelmemeli."""
    with pytest.raises(KeyError):
        db.archive_folders("pod_path")


# ---------------------------------------------------------------- liste

def test_only_parcels_with_a_document_are_listed(client):
    """Belgesiz parca listede olsaydi sayfa 'arsiv' degil ikinci bir takip
    listesi olurdu — tiklanacak hicbir seyi olmayan satirlar."""
    stores = client.get("/archive", params={"kind": "pod", "season": "FA26",
                                            "country": "DE"}).text

    assert "SBEDE1N" in stores
    assert "BOSXX1N" not in stores  # belgesiz parcanin magazasi klasor bile acmaz


def test_a_branch_only_shows_its_own_document(client):
    """Ikisi de olan parca her iki dalda da cikar ama YALNIZCA o dalin indirme
    dugmesiyle — POD ve etiket yan yana gorunmemeli."""
    pod = client.get("/archive/rows", params=leaf("DE", "SBEDE1N")).text
    label = client.get("/archive/rows",
                       params=label_leaf("2026-08-15", "SBEDE1N")).text

    assert "/pod/download/38120177000003" in pod
    assert "/archive/label/38120177000003" not in pod
    assert "/archive/label/38120177000003" in label
    assert "/pod/download/38120177000003" not in label


def test_search_skips_the_folders_entirely(client):
    """Kullanici takip numarasini degil magaza kodunu biliyor — ve hangi
    klasorde oldugunu bilmiyor; arama klasorleri atlayip dosyayi vermeli."""
    html = client.get("/archive/rows", params={"q": "WNPAT1N"}).text

    assert "38120177000002" in html
    assert "38120177000001" not in html


def test_each_row_links_to_the_document_it_actually_has(client):
    """POD'u olmayan satirda POD baglantisi gorunmemeli — 404'e goturur."""
    html = client.get("/archive/rows", params={"q": "38120177000002"}).text

    assert "/archive/label/38120177000002" in html
    assert "/pod/download/38120177000002" not in html


# ---------------------------------------------------------------- indirme

def test_the_label_link_serves_the_stored_file(client, db, tmp_path, monkeypatch):
    """Etiket PARCADAN indirilir: ayni magazanin ayni gun iki faturasi olabilir,
    magaza+gun ile arayan `/dispatch/label` ilkine duser."""
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    (tmp_path / "WNPAT1N-5000419.pdf").write_bytes(b"%PDF-1.4 etiket")

    response = client.get("/archive/label/38120177000002")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 etiket"
    assert "WNPAT1N-5000419.pdf" in response.headers["content-disposition"]


def test_a_missing_label_file_is_a_404_not_a_crash(client, tmp_path, monkeypatch):
    """Kayitta yol var ama dosya silinmis — arsiv sayfasi cokmemeli."""
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)

    assert client.get("/archive/label/38120177000002").status_code == 404


def test_a_path_outside_the_output_dir_is_refused(client, db, tmp_path, monkeypatch):
    """Yol kendi kaydimizdan gelse de dosya sunan uc sinirini kontrol etmeli."""
    outside = tmp_path / "disarida.pdf"
    outside.write_bytes(b"%PDF-1.4 gizli")
    db.set_label_path("38120177000002", str(outside))
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "cikti")

    assert client.get("/archive/label/38120177000002").status_code == 404


def test_each_label_row_links_to_its_own_box_pdf(client, db, tmp_path):
    """Ayni sevkiyatin iki kolisi ARSIVDE ayri belge acmali — onceden ikisi de
    sevkiyatin ortak cok sayfali PDF'ine gidiyordu ve "duplike" gorunuyordu
    (kullanici bulgusu, 2026-09-01)."""
    db.add_parcel("38120177000010", "NL", store_code="YNOFR1N", invoice_number="5000885",
                  country="FR", source_sheet=SHEET, shipment_date="2026-09-04")
    db.add_parcel("38120177000011", "NL", store_code="YNOFR1N", invoice_number="5000885",
                  country="FR", source_sheet=SHEET, shipment_date="2026-09-04")
    db.set_label_path("38120177000010", str(tmp_path / "YNOFR1N-5000885 1st box.pdf"))
    db.set_label_path("38120177000011", str(tmp_path / "YNOFR1N-5000885 2nd box.pdf"))

    html = client.get("/archive/rows", params=label_leaf("2026-09-04", "YNOFR1N")).text

    assert "/archive/label/38120177000010" in html
    assert "/archive/label/38120177000011" in html


def test_the_store_folder_appears_under_the_day(client, db, tmp_path):
    """Kullanicinin bildirdigi eksik: "store kod ve invoice olan klasor
    gozukmuyor"."""
    db.add_parcel("38120177000012", "NL", store_code="FGLFR1N", invoice_number="5000887",
                  country="FR", source_sheet=SHEET, shipment_date="2026-09-04")
    db.set_label_path("38120177000012", str(tmp_path / "FGLFR1N-5000887 1st box.pdf"))

    stores = client.get("/archive",
                        params={"kind": "label", "season": "FA26", "day": "2026-09-04"}).text

    assert "FGLFR1N" in stores
