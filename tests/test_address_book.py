# -*- coding: utf-8 -*-
"""Adres defteri CRUD + importer testleri."""
import csv
import json

import pytest

from address_book.db import AddressBook
from address_book.importers import (import_generic, import_json,
                                    import_sentez_odbc, map_headers)


@pytest.fixture
def tmp_book(tmp_path):
    db_path = tmp_path / "test.db"
    with AddressBook(db_path) as book:
        yield book


def test_add_and_get(tmp_book):
    aid = tmp_book.add(name="Test Co", city="Amsterdam", country="NL",
                       email="a@b.c")
    row = tmp_book.get(aid)
    assert row["name"] == "Test Co"
    assert row["city"] == "Amsterdam"


def test_update_and_delete(tmp_book):
    aid = tmp_book.add(name="X")
    tmp_book.update(aid, city="Rotterdam")
    assert tmp_book.get(aid)["city"] == "Rotterdam"
    tmp_book.delete(aid)
    assert tmp_book.get(aid) is None


def test_search(tmp_book):
    tmp_book.add(name="Alpha", city="Ankara")
    tmp_book.add(name="Beta", city="Berlin")
    tmp_book.add(name="Gamma", company="Alpha Group")
    hits = tmp_book.list(search="alpha")
    assert len(hits) == 2


def test_bulk_add_dedupes(tmp_book):
    rows = [{"name": "One", "city": "X"}, {"name": "One", "city": "X"},
            {"name": "Two"}]
    n = tmp_book.bulk_add(rows)
    assert n == 2
    assert tmp_book.count() == 2


def test_map_headers_turkish():
    m = map_headers(["Cari Adı", "Vergi No", "Telefon", "Ülke",
                     "Bilinmeyen Sütun"])
    assert m["Cari Adı"] == "name"
    assert m["Vergi No"] == "tax_no"
    assert m["Telefon"] == "phone"
    assert m["Ülke"] == "country"
    assert "Bilinmeyen Sütun" not in m


def test_generic_csv_import(tmp_path):
    p = tmp_path / "in.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Cari Adı", "Şehir", "Ülke", "E-posta"])
        w.writerow(["ACME", "Istanbul", "TR", "info@acme.com"])
        w.writerow(["BETA", "Ankara", "TR", "hi@beta.tr"])
    rows = import_generic(str(p))
    assert len(rows) == 2
    assert rows[0]["name"] == "ACME"
    assert rows[0]["email"] == "info@acme.com"


def test_sentez_odbc_stub_raises():
    with pytest.raises(NotImplementedError):
        import_sentez_odbc("SentezDSN")


# ---------- GLS portal disa aktarimi (toplu etiketin adres kaynagi) ----------

def _export(tmp_path, *records) -> str:
    p = tmp_path / "addresses-export.json"
    p.write_text(json.dumps(list(records)), encoding="utf-8")
    return str(p)


def test_gls_export_company_field_is_really_the_store_code(tmp_path):
    """Olculdu: 360 kaydin 297'sinde `company` firma adi degil magaza kodu.

    Gercek etikette de bu kod `name2`de basiliyor (RPXSE1N.pdf).
    """
    rows = import_json(_export(tmp_path, {
        "name": "A CAUSE DES GARCONS", "company": "PNHFR1N", "type": 1,
        "street": "14, Rue de l'Ancien Courrier", "houseNumber": "14",
        "postalCode": "34000", "city": "MONTPELLIER", "country": "FR",
    }))

    assert rows[0]["name2"] == "PNHFR1N"
    assert rows[0]["company"] == ""


def test_real_company_name_is_not_moved_to_name2(tmp_path):
    rows = import_json(_export(tmp_path, {"name": "Ali", "company": "Dolcezza BV"}))

    assert rows[0]["company"] == "Dolcezza BV"
    assert not rows[0].get("name2")


def test_house_number_equal_to_postal_code_is_dropped(tmp_path):
    """38 kayitta portalde yanlis girilmis; kapi numarasi olarak basilirsa adres bozulur."""
    rows = import_json(_export(tmp_path, {
        "name": "X", "street": "Hauptplatz 1", "houseNumber": "1010",
        "postalCode": "1010", "city": "Wien", "country": "AT",
    }))

    assert rows[0]["house_number"] == ""
    assert rows[0]["postal_code"] == "1010"


def test_find_by_store_code_is_exact_not_fuzzy(tmp_book):
    """Benzer isimle eslemek yanlis adrese etiket bastirir — yalnizca tam kod."""
    tmp_book.add(name="AHA Austria", name2="NUNAT1N", city="Wien")
    tmp_book.add(name="AHA Germany", name2="QXQDE1N", city="Berlin")

    assert [a["name"] for a in tmp_book.find_by_store_code("nunat1n")] == ["AHA Austria"]
    assert tmp_book.find_by_store_code("AHAAT") == []
    assert tmp_book.find_by_store_code("") == []


def test_two_addresses_for_one_code_are_both_returned(tmp_book):
    """Belirsizligi gizlemek yerine gostermek gerek — operator secer."""
    tmp_book.add(name="Grace", name2="TENES1N", city="Pineda")
    tmp_book.add(name="Grace", name2="TENES1N", city="Barcelona")

    assert len(tmp_book.find_by_store_code("TENES1N")) == 2


def test_bulk_add_keeps_distinct_store_codes_with_the_same_name_and_city(tmp_book):
    """Isim+sehir tek basina yetmiyor: GLS disa aktariminda kodsuz ikizler
    ANQDE1N ve WNQHU1N'i sessizce yutuyordu."""
    rows = [{"name": "Boutique Nadja", "city": "Aue"},
            {"name": "Boutique Nadja", "city": "Aue", "name2": "ANQDE1N"}]

    assert tmp_book.bulk_add(rows) == 2
    assert len(tmp_book.find_by_store_code("ANQDE1N")) == 1


def test_bulk_add_of_the_same_export_twice_adds_nothing(tmp_book):
    rows = [{"name": "A", "city": "Wien", "name2": "NNNAT1N"}]

    assert tmp_book.bulk_add(rows) == 1
    assert tmp_book.bulk_add(rows) == 0
