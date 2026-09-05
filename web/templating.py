# -*- coding: utf-8 -*-
"""Tum router'larin paylastigi Jinja ortami.

Onceden her router kendi `Jinja2Templates(directory="web/templates")` ornegini
kuruyordu; sekiz ayri ortam olunca `t()` gibi bir genel islevi eklemek sekiz
yerde tekrar gerekirdi ve birinin unutulmasi o sayfayi cevrilmemis birakirdi.

`t` bir Jinja GENEL islevi: sablonlarda `{{ t('nav.pod') }}` yeter, dil
`i18n`in istek baglamindan gelir (bkz. `i18n/__init__.py`).
"""
from __future__ import annotations

from fastapi.templating import Jinja2Templates

import i18n
import timez
from auth import access

templates = Jinja2Templates(directory="web/templates")
templates.env.globals["t"] = i18n.t
templates.env.globals["t_or"] = i18n.t_or
templates.env.globals["langs"] = i18n.LANGS
templates.env.globals["lang"] = i18n.current
# `can(rol, yol)` — menuyu suzen kural ile 403 veren kural AYNI sozlukten
# (`auth/access.py`) gelsin diye. Iki liste tutuldugunda menude olmayan sayfa
# URL'den acilabilir kaliyor.
templates.env.globals["can"] = access.can
# `{{ deger|localtime }}` — depolanan UTC damgasini `19.08.2026 12:04` yapar.
# Ham ISO metni sablonda BASILMAZ: hem okunaksiz hem 3 saat geride gorunurdu.
# AYNI filtre `notify/digest.py`deki ayri Jinja ortamina da tanitilmistir.
templates.env.filters["localtime"] = timez.stamp
