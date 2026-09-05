# -*- coding: utf-8 -*-
"""Dil anahtari — ust cubuktaki TR | EN.

Secim SUNUCUDA degil tarayicida saklanir (`lang` cerezi): ayni panele iki kisi
farkli dille bakabilsin diye. Ayarlar'daki `UI_LANG` yalnizca VARSAYILANDIR ve
cerezi olmayanlar (yeni tarayici, gunluk ozet maili) icin gecerlidir.

Bu router korumasizdir: giris sayfasinin da dil anahtari var.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

import i18n

router = APIRouter(prefix="/lang", tags=["lang"])

# Cerez bir yil yasar: dil tercihi oturumdan uzun omurlu.
MAX_AGE = 365 * 24 * 3600


def _safe_next(raw: str) -> str:
    """Yalnizca kendi sitemize donen yollar. `//host` bir BASKA siteye gider."""
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


@router.get("/{code}")
async def switch(request: Request, code: str):
    target = _safe_next(request.query_params.get("next", "") or "/")
    response = RedirectResponse(target, status_code=303)
    if code in i18n.CATALOG:
        response.set_cookie(i18n.COOKIE, code, max_age=MAX_AGE,
                            httponly=False, samesite="lax")
    return response
