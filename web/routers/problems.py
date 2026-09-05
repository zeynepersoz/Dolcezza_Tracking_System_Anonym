"""Sorunlu kargolar: exception, hareketsiz (stale) ve POD'u eksik teslimatlar."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from web.templating import templates
from web import deps
from gls_api import config
from notify import pod_chase

router = APIRouter(prefix="/problems", tags=["problems"])

# Metinler `i18n` sozlugunde; burada yalnizca SIRA tutuluyor — kartlarin ve
# filtrenin sirasi onem sirasidir, alfabetik degil.
KINDS = ("exception", "damaged", "partial", "pod_missing", "stale", "not_handed_over", "returned")

@router.get("", response_class=HTMLResponse)
async def problems_page(request: Request, kind: str = "", stale_days: int = 7):
    db = deps.get_shipments_db()
    stale_days = max(1, min(stale_days, 365))
    rows = db.problem_parcels(stale_days=stale_days, limit=1000)
    if kind in KINDS:
        rows = [r for r in rows if r["problem_kind"] == kind]
    ctx = {
        "request": request,
        "parcels": rows,
        "counts": db.problem_counts(stale_days=stale_days),
        "kinds": KINDS,
        "filter_kind": kind,
        "stale_days": stale_days,
        "truncated": len(rows) >= 1000,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "problems.html", ctx)


@router.get("/gls-inquiry", response_class=HTMLResponse)
async def gls_inquiry(request: Request, stale_days: int = 7):
    """POD'u gelmeyen teslimatlar icin hazir GLS mektubu — GONDERMEZ, gosterir.

    Otomatik gonderimle AYNI kaynak ve AYNI bekleme suresini kullanir
    (`notify/pod_chase.py`), boylece ekranda gorulen mektup ile GLS'e giden
    mektup birbirinden ayrisamaz. Tek fark: burada daha once sorulmus parcalar
    da listelenir — cevap gelmediyse kullanici elle yeniden sorabilmeli.
    """
    db = deps.get_shipments_db()
    rows = db.pod_missing_since(wait_days=config.GLS_INQUIRY_WAIT_DAYS,
                                unchased_only=False, limit=1000)
    subject, body, html_body = pod_chase.draft(rows)
    ctx = {
        "request": request,
        "parcels": rows,
        "subject": subject,
        "body": body,
        "html_body": html_body,
        "stale_days": stale_days,
        "wait_days": config.GLS_INQUIRY_WAIT_DAYS,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "problems_inquiry.html", ctx)
