# -*- coding: utf-8 -*-
"""Sozlukler icin koruma.

`t()` eksik anahtarda sayfayi 500'e dusurmez, anahtarin kendisini basar — yani
eksik bir ceviri gozle GORULMEZ, panelde "nav.pod" yazar gecer. Gercek koruma
burasi: iki sozlugun anahtar kumeleri ve yer tutuculari birebir ayni olmali.
"""
from __future__ import annotations

import re

import i18n

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_both_catalogs_have_the_same_keys():
    assert set(i18n.CATALOG["tr"]) == set(i18n.CATALOG["en"])


def test_placeholders_match():
    """Ceviride eksik bir `{count}` calisma aninda KeyError atardi.

    KUME karsilastirilir, sira degil: cumle kurulusu dile gore degisiyor
    ("480 gonderiden 173 tanesi" / "173 of 480 shipments").
    """
    for key, turkish in i18n.CATALOG["tr"].items():
        assert set(_PLACEHOLDER.findall(turkish)) == set(_PLACEHOLDER.findall(
            i18n.CATALOG["en"][key])), key


def test_no_empty_strings():
    for lang, table in i18n.CATALOG.items():
        for key, text in table.items():
            assert text.strip(), f"{lang}/{key} bos"


def test_unknown_key_falls_back_to_itself():
    assert i18n.t("yok.boyle.bir.anahtar") == "yok.boyle.bir.anahtar"


def test_fields_are_formatted():
    assert i18n.t("problems.hint.stale", "tr", days=7) == \
        "GLS koliyi 7+ gün önce aldı, hâlâ teslim edilmedi"
    assert i18n.t("problems.hint.stale", "en", days=7) == \
        "GLS took the parcel over 7+ days ago, still not delivered"


def test_use_restores_the_previous_language():
    """Arka plan isleri baglami kirletmemeli — zamanlayici surekli calisiyor."""
    before = i18n.current()
    with i18n.use("en"):
        assert i18n.current() == "en"
    assert i18n.current() == before


def test_unknown_language_falls_back():
    assert i18n.t("nav.pod", "de") == i18n.t("nav.pod", "tr")
