# -*- coding: utf-8 -*-
"""Toplu etiket — paketleme tarihinden GLS NL etiketine.

Akis: tarih sec -> ONIZLE -> eksik adresleri gir -> ONAYLA -> etiketler uretilir.
Onizleme adimi zorunlu: etiket geri alinamaz, 30 sevkiyatlik bir hatanin bedeli
30 yanlis etikettir.

Katmanlar: `erp/boxes.py` (okuma) -> `dispatch/plan.py` (saf) -> `dispatch/engine.py`
(yan etkiler). Bu dosya yalnizca HTTP.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import zipfile

from pypdf import PdfWriter
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

import i18n
from auth import access
from auth.dependencies import require_role
import erp.boxes as erp_boxes
from dispatch import engine
from dispatch.plan import build_plan
from erp.boxes import available_dates, fetch_unlabeled_boxes
from erp.client import ERPClient, ERPError
from gls_api import config, providers
from gls_api.label_payload import missing_shipper_fields
from gls_api.pipeline import safe_name_part
# Takip no kalibi TEK yerde tanimli (bkz. web/routers/tracking.py) —
# ikinci bir kopya zamanla ayrisir.
from web.routers.tracking import TRACKING_NO_PATTERN
from web import deps
from web.templating import templates

# Toplu etiket admin + operator: gunluk isi operatorler yapiyor, admin'i beklemek
# 85 koliyi bekletir. Onizleme + onay adimi zaten koruma sagliyor.
router = APIRouter(prefix="/dispatch", tags=["dispatch"],
                   dependencies=[Depends(require_role(*access.WRITERS))])

log = logging.getLogger("dispatch")


def _valid_day(day: str) -> str:
    """Tarih hem SQL parametresine hem KLASOR ADINA giriyor — bicimi zorunlu."""
    try:
        return date.fromisoformat((day or "").strip()).isoformat()
    except ValueError:
        raise HTTPException(400, i18n.t("dispatch.err_bad_date"))


def _erp() -> ERPClient:
    # `ERP_BOXES_MOCK_FIXTURE=1` (yerel gelistirme, bkz. erp/boxes.py):
    # `fetch_unlabeled_boxes`/`available_dates` gercek MSSQL'e hic gitmeden
    # sahte koliler doner, bu yuzden burada MSSQL sarti aranmaz.
    if not erp_boxes._MOCK_FIXTURE_ENABLED and not config.mssql_configured():
        raise HTTPException(503, i18n.t("err.mssql_not_configured"))
    return ERPClient()


def _dates() -> list[dict]:
    try:
        return available_dates(_erp(), days=30)
    except (ERPError, HTTPException, Exception):
        return []


def _plan(day: str, *, split_stores=()):
    """Koli listesini MSSQL'den TAZE ceker ve plani kurar.

    Tarayicidan gelen agirlik/fatura degerlerine GUVENILMEZ — disaridan yalnizca
    "hangi magaza kodlari" ve "birlestirme onayi" alinir.
    """
    try:
        rows = fetch_unlabeled_boxes(_erp(), day)
    except ERPError as exc:
        raise HTTPException(502, str(exc))
    return build_plan(
        rows,
        deps.get_address_source().find_by_store_code,
        split_stores=split_stores,
        already_labeled=deps.get_shipments_db().labeled_box_ids(day),
    )


@router.get("", response_class=HTMLResponse)
async def dispatch_page(request: Request, day: str = ""):
    dates = await asyncio.to_thread(_dates)
    if day:
        selected = _valid_day(day)
    elif dates:
        selected = dates[0]["date"]
    else:
        selected = date.today().isoformat()
    return templates.TemplateResponse(request, "dispatch.html", {
        "request": request,
        "dates": dates,
        "day": selected,
        **deps.template_context(),
    })


@router.get("/rows", response_class=HTMLResponse)
async def dispatch_rows(request: Request, day: str, split: str = ""):
    """Onizleme tablosu (HTMX). `split` = birlestirmesi kapatilan magaza kodlari."""
    selected = _valid_day(day)
    split_stores = [s for s in split.split(",") if s]
    plan = await asyncio.to_thread(_plan, selected, split_stores=split_stores)
    return templates.TemplateResponse(request, "_partials/dispatch_rows.html", {
        "request": request,
        "plan": plan,
        "day": selected,
        "split": split,
        **deps.template_context(),
    })


@router.post("/address", response_class=HTMLResponse)
async def save_address(
    request: Request,
    day: str = Form(...),
    split: str = Form(""),
    store_code: str = Form(...),
    name: str = Form(...),
    street: str = Form(""),
    house_number: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    country: str = Form(""),
    contact: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address_id: str = Form(""),
):
    """Adresi adres defterine yazar — BIR KEZ girilir, sonraki gunler eslesir.

    `address_id` verilirse (sendable satirdan duzenleme) mevcut kayit GUNCELLENIR;
    yeni kayit eklenirse magaza iki adaya dusup 'belirsiz adres'e kayardi. Bos ise
    (eksik adres girisi) yeni kayit eklenir.
    """
    code = safe_name_part(store_code, 20).upper()
    if not code:
        raise HTTPException(400, i18n.t("dispatch.err_bad_store"))
    book = deps.get_address_book()
    fields = dict(
        name=name, street=street, house_number=house_number,
        postal_code=postal_code, city=city, country=country,
        contact=contact, phone=phone, email=email,
    )
    if address_id.strip().isdigit():
        book.update(int(address_id), **fields)
    else:
        book.add(name2=code, address_type="business", **fields)
    # Kaynak onbellegini dusur: /create adresi yeniden cozecek, degisiklik gorulsun.
    deps.get_address_source().invalidate()
    return await dispatch_rows(request, day=_valid_day(day), split=split)


# Adres duzenleme formundan (dispatch_rows.html) gelen, ETIKETE gecirilecek
# alanlar. `_plan` adresi ERP'den TAZE cozer; operator onizlemede duzenlerse
# o degerler "Adresi kaydet"e basmadan da bu tur icin gecerli olmali
# (kullanici 2026-09-03: "adreste yapilan duzenleme tabii ki etiket olustur
# butonunda aktive olacak"). Kalici degisiklik icin "Adresi kaydet" ayrica var.
_OVERRIDE_FIELDS = ("name", "contact", "street", "house_number",
                    "postal_code", "city", "country", "phone", "email")


def _apply_address_overrides(shipments: list, overrides: dict[str, dict]) -> None:
    """Onizlemede elle duzenlenen adres alanlarini sevkiyatlara isler.

    Anahtar SEVKIYAT bazinda: "MAGAZAKODU::referans". Fatura bazinda ayrilmis
    ("Ayir") her sevkiyat kendi adresini alir — biri farkliysa yalnizca o
    degisir. Eski (magaza bazli) anahtar da geriye uyum icin denenir.
    """
    if not overrides:
        return
    for s in shipments:
        code = (s.store_code or "").upper()
        ov = overrides.get(f"{code}::{s.reference}") or overrides.get(code)
        if not ov or not s.address:
            continue
        merged = dict(s.address)
        for key in _OVERRIDE_FIELDS:
            if key in ov and ov[key] is not None:
                merged[key] = str(ov[key]).strip()
        s.address = merged


def _run_job(app, day: str, stores: set[str], split_stores: list[str],
             overrides: dict[str, dict] | None = None) -> None:
    """Arka plan isi: 30 sevkiyat x ~1.5 s senkron istegi zaman asimina ugratirdi."""
    job = app.state.bulk_job
    try:
        # Sessiz yedekleme YOK: NL yapilandirilmamissa is bastan hata versin,
        # 30 Hollanda gonderisi icin Irlanda etiketi basilmasin.
        _, client = providers.strict_provider_for("NL")
        chosen = [s for s in _plan(day, split_stores=split_stores).sendable
                  if s.store_code in stores]
        _apply_address_overrides(chosen, overrides or {})
        job["total"] = len(chosen)
        if not chosen:
            job.update(done=True, error=i18n.t("dispatch.err_nothing"))
            return
        # Gonderici adresi eksikse GLS'e HIC gidilmez: aksi halde her sevkiyat
        # tek tek "V009 ... field is required" ile patliyor ve ekranda yalnizca
        # "gecersiz veri iceriyor" gorunuyordu — sorunun ALICI adresinde
        # sanilmasina yol aciyordu (kullanici bildirdi, 2026-09-01).
        shipper = deps.get_address_book().get_default_shipper() or {}
        missing = missing_shipper_fields(shipper)
        if missing:
            job.update(done=True,
                       error=i18n.t("dispatch.err_no_shipper", fields=", ".join(missing)))
            return
        result = engine.run(
            chosen,
            client=client,
            db=deps.get_shipments_db(),
            day=day,
            shipper=shipper,
            progress=lambda i, n, s: job.update(current=i, total=n, store=s),
        )
        job.update(done=True, result=result)
    except Exception as exc:                # noqa: BLE001 — is durumu ekranda gorunsun
        job.update(done=True, error=str(exc) or exc.__class__.__name__)
        # Ekrandaki hata tarayici kapaninca kayboluyordu; is HIC baslamadiysa
        # (yapilandirma, MSSQL, adres) tek iz sunucu gunlugudur.
        log.exception("Toplu etiket isi basarisiz (%s)", day)


@router.post("/create", response_class=HTMLResponse)
async def create_labels(
    request: Request,
    day: str = Form(...),
    stores: list[str] = Form([]),
    split: str = Form(""),
    # Onizlemedeki adres duzenleme alanlari: {MAGAZAKODU: {street, house_number,
    # ...}} JSON'i (dispatch_rows.html'deki Alpine `edits` nesnesi). Bos ise
    # adres ERP'den cozuldugu gibi kullanilir.
    overrides: str = Form(""),
):
    """ONAYDAN SONRA isi baslatir. Ayni anda ikinci is calisamaz (409)."""
    selected = _valid_day(day)
    job = getattr(request.app.state, "bulk_job", None)
    if job and not job.get("done"):
        raise HTTPException(409, i18n.t("dispatch.err_busy"))
    codes = {s.strip().upper() for s in stores if s.strip()}
    if not codes:
        raise HTTPException(400, i18n.t("dispatch.err_nothing"))

    parsed_overrides: dict[str, dict] = {}
    if overrides.strip():
        try:
            raw = json.loads(overrides)
            if isinstance(raw, dict):
                parsed_overrides = {str(k).strip().upper(): v
                                    for k, v in raw.items() if isinstance(v, dict)}
        except (ValueError, TypeError):
            log.warning("dispatch/create: overrides JSON cozulemedi, yok sayildi")

    request.app.state.bulk_job = {
        "day": selected, "current": 0, "total": len(codes),
        "store": "", "done": False, "error": "", "result": None,
    }
    split_stores = [s for s in split.split(",") if s]
    asyncio.create_task(
        asyncio.to_thread(_run_job, request.app, selected, codes, split_stores,
                          parsed_overrides))
    return await dispatch_progress(request)


@router.get("/progress", response_class=HTMLResponse)
async def dispatch_progress(request: Request):
    return templates.TemplateResponse(request, "_partials/dispatch_progress.html", {
        "request": request,
        "job": getattr(request.app.state, "bulk_job", None),
        **deps.template_context(),
    })


@router.get("/label/{store_code}")
async def download_label(store_code: str, day: str):
    """Yol ic mantiktan uretilir; disaridan gelen ad dosya adina temizlenerek girer.

    Dosya adi artik `{magaza}-{fatura}.pdf` (bkz. dispatch/engine.py
    `_write_pdf`) — fatura numarasi burada bilinmedigi icin onekle aranir.
    Eski bicimle (yalnizca magaza kodu) olusturulmus dosyalar icin geriye
    donuk uyumluluk korunur.
    """
    code = safe_name_part(store_code, 20).upper()
    if not code:
        raise HTTPException(400, i18n.t("dispatch.err_bad_store"))
    directory = engine.label_dir(_valid_day(day))
    matches = sorted(directory.glob(f"{code}-*.pdf")) if directory.is_dir() else []
    if not matches:
        legacy = directory / f"{code}.pdf"
        if legacy.is_file():
            matches = [legacy]
    if not matches:
        raise HTTPException(404, i18n.t("dispatch.err_no_label", store=code))
    path = matches[0]
    return Response(path.read_bytes(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


@router.post("/download-zip")
async def download_zip(day: str = Form(...)):
    """Gunun tum etiketleri tek dosyada — yazdirma/arsiv icin."""
    selected = _valid_day(day)
    directory: Path = engine.label_dir(selected)
    pdfs = sorted(directory.glob("*.pdf")) if directory.is_dir() else []
    if not pdfs:
        raise HTTPException(404, i18n.t("dispatch.err_no_labels", day=selected))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for pdf in pdfs:
            zf.write(pdf, arcname=pdf.name)
    name = f"{selected}-{engine.LABEL_DIR_SUFFIX.replace(' ', '-')}.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/download-pdf")
async def download_pdf(day: str = Form(...), tracking_nos: list[str] = Form(default=[])):
    """Gunun tum etiketleri TEK PDF'te — tek seferde yazdirmak icin.

    ZIP'in kardesi ama farkli isi gorur: ZIP arsivlemek/tek tek acmak icindir,
    kullanici 30 dosyayi tek tek acip yazdirmak istemiyor (istek, 2026-09-01:
    "tek seferde yazdirmak icin tum pdfler birlesmis olsun").

    Sira ZIP ile AYNI (`sorted`): iki indirme ayni gunu ayni duzende versin,
    yoksa yazdirilan deste ile arsivdeki dosyalar karsilastirilamaz.

    Bozuk/okunamayan tek bir etiket TUM desteyi dusurmez — atlanir ve loglanir;
    30 sevkiyatlik bir yazdirmanin tek dosya yuzunden hic olmamasi daha kotudur.
    """
    selected = _valid_day(day)
    if tracking_nos:
        # YALNIZCA bu yazdirmadakiler (kullanici istegi, 2026-09-01: "4 Eylul'de
        # ki hepsini mi, simdi yazdirilan 2 tane mi"). Koli BASINA uretilen tek
        # sayfalik PDF kullanilir — sevkiyatin ortak cok sayfali dosyasi
        # secilseydi ayni gunun BASKA kolileri de desteye karisirdi.
        #
        # Yol istemciden GELMEZ: takip numarasi dogrulanip veritabanindan
        # okunur, yoksa disaridan gelen bir yol sunucudaki herhangi bir
        # dosyayi indirtebilirdi.
        db = deps.get_shipments_db()
        pdfs = []
        for no in tracking_nos:
            if not re.fullmatch(TRACKING_NO_PATTERN, (no or "").strip()):
                continue
            parcel = db.get(no.strip())
            path = Path(parcel["label_pdf_path"]) if parcel and parcel.get("label_pdf_path") else None
            if path and path.is_file():
                pdfs.append(path)
        name_hint = f"{selected}-{len(pdfs)}-etiket"
    else:
        directory: Path = engine.label_dir(selected)
        pdfs = sorted(directory.glob("*.pdf")) if directory.is_dir() else []
        name_hint = f"{selected}-{engine.LABEL_DIR_SUFFIX.replace(' ', '-')}"
    if not pdfs:
        raise HTTPException(404, i18n.t("dispatch.err_no_labels", day=selected))

    merger = PdfWriter()
    merged = 0
    for pdf in pdfs:
        try:
            merger.append(str(pdf))
            merged += 1
        except Exception:                   # noqa: BLE001 — bozuk dosya desteyi dusurmesin
            log.exception("Etiket birlestirilemedi, atlandi: %s", pdf.name)
    if not merged:
        raise HTTPException(500, i18n.t("dispatch.err_merge_failed"))

    buf = io.BytesIO()
    merger.write(buf)
    merger.close()
    name = f"{name_hint}.pdf"
    log.info("Birlesik etiket PDF'i: %s (%s/%s dosya)", name, merged, len(pdfs))
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
