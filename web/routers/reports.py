# -*- coding: utf-8 -*-
"""Sevkiyat & teslimat sureleri analiz raporu (İş biriminin mailindeki
bicimin panel karsiligi — kullanici istegi, 2026-08-31).

Once TEK sayfaydi; kullanici bunu IKI ayri, kendi menu ogesi olan sayfaya
bolmemizi istedi (2026-08-31): "reports kutusuna bastığında altta parcelshop
deliveries ve delivery times adlı iki ayrı alan açıp oradan da seçmesi
lazım". `/reports` eskisi gibi calismaya devam eder — ilkine yonlendirir,
eski yer imleri kirilmasin.
"""
import i18n
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from tracking.completion_report import build_completion_report, completion_export_table
from tracking.delivery_report import (REPORT_CHANNEL_LABELS, breakdown_export_table,
                                      build_parcel_shop_report, build_report,
                                      normalize_report_channel, overall_export_table,
                                      parcel_shop_export_table)
from web import deps, exports
from web.templating import templates

router = APIRouter()


def _carrier(request: Request) -> str:
    """`?carrier=` -> NL/IE/FEDEX (gecersizse ilki). Iki rapor da tasima
    firmasina gore hazirlanir (kullanici istegi 2026-09-03)."""
    return normalize_report_channel(request.query_params.get("carrier", ""))


def _slug(channel: str) -> str:
    return REPORT_CHANNEL_LABELS[channel].lower()


@router.get("/reports")
async def reports_page():
    """Eski tek-sayfali /reports icin geriye donuk uyumluluk — yer imleri kirilmasin."""
    return RedirectResponse("/reports/delivery-times")


@router.get("/reports/delivery-times")
async def delivery_times_page(request: Request):
    db = deps.get_shipments_db()
    ctx = {
        "request": request,
        "report": build_report(db, _carrier(request)),
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "reports_delivery_times.html", ctx)


@router.get("/reports/delivery-times/export.xlsx")
async def delivery_times_xlsx(request: Request):
    ch = _carrier(request)
    r = build_report(deps.get_shipments_db(), ch)
    o_headers, o_rows = overall_export_table(r["overall"], r["has_items"])
    c_headers, c_rows = breakdown_export_table(r["by_country"], "reports.col.country")
    m_headers, m_rows = breakdown_export_table(r["by_method"], "reports.col.method")
    data = exports.to_xlsx_multi([
        (i18n.t("reports.card.total_title"), o_headers, o_rows),
        (i18n.t("reports.country_table_title"), c_headers, c_rows),
        (i18n.t("reports.method_table_title"), m_headers, m_rows),
    ])
    return Response(
        data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="teslimat-sureleri-{_slug(ch)}.xlsx"'})


@router.get("/reports/delivery-times/export.pdf")
async def delivery_times_pdf(request: Request):
    ch = _carrier(request)
    r = build_report(deps.get_shipments_db(), ch)
    o_headers, o_rows = overall_export_table(r["overall"], r["has_items"])
    c_headers, c_rows = breakdown_export_table(r["by_country"], "reports.col.country")
    m_headers, m_rows = breakdown_export_table(r["by_method"], "reports.col.method")
    data = exports.to_pdf_multi(i18n.t("reports.title"), i18n.t("reports.subtitle"), [
        (i18n.t("reports.card.total_title"), o_headers, o_rows),
        (i18n.t("reports.country_table_title"), c_headers, c_rows),
        (i18n.t("reports.method_table_title"), m_headers, m_rows),
    ])
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="teslimat-sureleri-{_slug(ch)}.pdf"'})


@router.get("/reports/parcel-shop")
async def parcel_shop_page(request: Request):
    db = deps.get_shipments_db()
    ctx = {
        "request": request,
        "ps": build_parcel_shop_report(db, _carrier(request)),
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "reports_parcel_shop.html", ctx)


@router.get("/reports/parcel-shop/export.xlsx")
async def parcel_shop_xlsx(request: Request):
    ch = _carrier(request)
    ps = build_parcel_shop_report(deps.get_shipments_db(), ch)
    headers, rows = parcel_shop_export_table(ps)
    data = exports.to_xlsx(headers, rows, i18n.t("reports.parcel_shop_title"))
    return Response(
        data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="parcelshop-teslimatlari-{_slug(ch)}.xlsx"'})


@router.get("/reports/parcel-shop/export.pdf")
async def parcel_shop_pdf(request: Request):
    ch = _carrier(request)
    ps = build_parcel_shop_report(deps.get_shipments_db(), ch)
    headers, rows = parcel_shop_export_table(ps)
    data = exports.to_pdf(headers, rows, i18n.t("reports.parcel_shop_title"))
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="parcelshop-teslimatlari-{_slug(ch)}.pdf"'})


@router.get("/reports/completion")
async def completion_page(request: Request):
    db = deps.get_shipments_db()
    ctx = {
        "request": request,
        "report": build_completion_report(db),
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "reports_completion.html", ctx)


@router.get("/reports/completion/export.xlsx")
async def completion_xlsx():
    report = build_completion_report(deps.get_shipments_db())
    headers, rows = completion_export_table(report)
    data = exports.to_xlsx(headers, rows, i18n.t("completion.title"))
    return Response(
        data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="siparis-tamamlanma.xlsx"'})


@router.get("/reports/completion/export.pdf")
async def completion_pdf():
    report = build_completion_report(deps.get_shipments_db())
    headers, rows = completion_export_table(report)
    subtitle = i18n.t("completion.subtitle", season=report["season"] or "—")
    data = exports.to_pdf(headers, rows, i18n.t("completion.title"), subtitle)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="siparis-tamamlanma.pdf"'})


@router.get("/reports/mail-preview")
async def reports_mail_preview():
    """Analiz e-postasının tarayıcıdaki canlı HTML önizlemesi."""
    from fastapi.responses import HTMLResponse
    from notify.analytics_report import render_html
    return HTMLResponse(render_html())
