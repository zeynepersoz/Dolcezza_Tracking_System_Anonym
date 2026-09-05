# -*- coding: utf-8 -*-
"""Iki dilli arayuz (TR/EN) — duz sozluk, derleme adimi yok.

`gettext` KULLANILMIYOR: `.po`/`.mo` derlemesi Docker imajina bir adim daha
eklerdi ve burada cevrilecek metin sabit bir kume (bkz. `tr.py` / `en.py`).
Sozluk Python dosyasi oldugu icin testler iki dilin anahtarlarinin birebir
ayni oldugunu dogrudan dogrulayabiliyor (`test_i18n.py`).

DIL NASIL SECILIR
-----------------
Istek basina bir `ContextVar` tutulur; `web/main.py` icindeki ara katman her
istekte `lang` cerezinden okuyup kurar. Boylece `t()` cagiran hicbir yere
(sablon, Excel uretici, mail sablonu) `request` gecirmek gerekmiyor — aksi
halde 40'tan fazla cagri yeri degisecekti.

`ContextVar` senkron uc noktalarda da dogru calisir: Starlette onlari
`anyio.to_thread.run_sync` ile calistirir ve o baglami is parcacigina kopyalar.

Arka plan islerinin (gunluk ozet) istegi yoktur; oralarda dil ACIKCA verilir:
`t(key, lang)` ya da `use(lang)`.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from i18n import en as _en
from i18n import tr as _tr

# Panelde ve mailde kullanilabilecek diller. Sira ust cubuktaki anahtarin sirasi.
LANGS: tuple[tuple[str, str], ...] = (("tr", "TR"), ("en", "EN"))
FALLBACK = "tr"

CATALOG: dict[str, dict[str, str]] = {"tr": _tr.STRINGS, "en": _en.STRINGS}

# Cerez adi — `web/routers/lang.py` yaziyor, ara katman okuyor.
COOKIE = "lang"

_current: ContextVar[str] = ContextVar("lang", default="")


def default_lang() -> str:
    """Panel ayarindaki dil (`UI_LANG`). Gecersizse TR.

    `config` BURADA import edilir, modul tepesinde degil: `gls_api.config`
    ayarlari `settings.store`dan okuyor ve o da bu paketi kullanabiliyor.
    """
    from gls_api import config

    lang = str(getattr(config, "UI_LANG", FALLBACK) or FALLBACK).lower()
    return lang if lang in CATALOG else FALLBACK


def current() -> str:
    """Bu istekte gecerli dil."""
    return _current.get() or default_lang()


def set_current(lang: str) -> None:
    """Ara katman her istekte cagirir."""
    _current.set(lang if lang in CATALOG else default_lang())


@contextmanager
def use(lang: str):
    """Arka plan isleri icin: `with i18n.use("en"): ...`"""
    token = _current.set(lang if lang in CATALOG else FALLBACK)
    try:
        yield
    finally:
        _current.reset(token)


def t(key: str, lang: str = "", **fields) -> str:
    """Anahtarin cevirisi. `{ad}` yer tutuculari `fields` ile doldurulur.

    Anahtar bulunamazsa once TR'ye, o da yoksa ANAHTARIN KENDISINE dusulur:
    eksik bir ceviri sayfayi 500'e dusurmemeli. Eksikligi gormek icin gercek
    koruma testtedir — iki sozlugun anahtar kumeleri birebir ayni olmali.
    """
    table = CATALOG.get(lang or current(), CATALOG[FALLBACK])
    text = table.get(key) or CATALOG[FALLBACK].get(key) or key
    return text.format(**fields) if fields else text


def t_or(key: str, default: str, lang: str = "") -> str:
    """Ceviri varsa onu, yoksa `default`u dondurur.

    `t()` bulamadigi anahtarin KENDISINI donduruyor; kullanicinin ekraninda
    `settings.field.MSSQL_PORT` gormesindense elde duran metni basmak yeglenir.
    """
    text = t(key, lang)
    return default if text == key else text
