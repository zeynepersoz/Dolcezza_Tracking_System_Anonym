"""Canli takip sayfasi + HTMX tabanli tablo yenilemesi."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import i18n
import timez
from auth import access
from auth.dependencies import require_role
from erp import contents, writeback
from erp.client import ERPClient
from erp.sync import sync as erp_sync
from gls_api import config, providers
from tracking.db import status_label
from web.templating import templates
from web import box_image_document, deps, exports, paging
from web.uploads import read_limited
from web.importers.tracking_list import import_all_sheets, import_tracking_list, list_sheets

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tracking", tags=["tracking"])

# Sayfanin KENDISI `viewer`a da acik (salt okuma), ama asagidaki dort uc GLS'e
# ya da veritabanina yazar/tetikler — onlar yalnizca `access.WRITERS`a.
WRITE = [Depends(require_role(*access.WRITERS))]

TRACKING_NO_PATTERN = r"^[A-Za-z0-9]{1,20}$"

# Sol filtre panelindeki her alan icin: query-string adi (f_<key>) -> ShipmentsDB
# kolon anahtari. Yeni bir filtrelenebilir alan eklemek icin sadece buraya (ve
# ShipmentsDB._LIKE_FILTERS'a, zaten ayni anahtarlari kullanir) eklemek yeterli.
FILTER_FIELDS = [
    "tracking_no", "reference", "store_code", "invoice_number", "emc_invoices",
    "consignee_name", "sales_rep", "country", "zip_code",
    "shipment_date", "delivered_date",
]


def _read_filters(request: Request) -> dict:
    """Query-string'den 'f_<alan>' parametrelerini okur -> {alan: deger}.

    Ayni alan iki kez gelebilir (sol panel + sutun basligindaki huni menusu);
    dolu olan kazanir, ikisi de bossa bos string.
    """
    out = {}
    for field in FILTER_FIELDS:
        values = [v for v in request.query_params.getlist(f"f_{field}") if v]
        out[field] = values[0] if values else ""
    return out


def _read_multi(request: Request, name: str) -> list[str]:
    """Sutun basligindaki huni menusu ayni adi birden cok kez yollar.

    Sol paneldeki tek secimli `?status=delivered` baglantilari da calisir —
    tek elemanli liste olur.
    """
    return [v for v in request.query_params.getlist(name) if v]


# Ekranda tek sayfa. Once tum liste (1000 satir) tek seferde ciziliyordu ve
# tablo kendini 30 saniyede bir yeniliyordu: her tur 1000 satirlik HTML uretip
# DOM'a yikmak demek. Kullanici bunu "memoryi sisirir, yuklenmesi cok uzun
# surer" diye bildirdi.
PAGE_SIZE = 100


def _query(request: Request, limit: int = PAGE_SIZE,
           paged: bool = True) -> tuple[list[dict], dict]:
    """Uc uctan da ayni sorgu: sayfa, HTMX satirlari ve filtre baglami.

    Disa aktarmada `paged=False` ve `limit` buyuktur: dosya EKSIK OLMAMALI —
    kullanici onu Excel'de suzecek ve orada eksik satir sessizce yanlis toplam
    demek. Ekranda ise gorunen sayfa iner.
    """
    filters = _read_filters(request)
    status = _read_multi(request, "status")
    channel = _read_multi(request, "channel")
    season = _read_multi(request, "season")
    shipment_method = _read_multi(request, "shipment_method")
    if not shipment_method and request.query_params.get("f_shipment_method"):
        shipment_method = [request.query_params.get("f_shipment_method")]
    search = request.query_params.get("search", "")
    # Panelin "Bugun teslim edilen" kartindan gelir; `f_` alani DEGIL — LIKE
    # degil tam gun eslesmesi ister (bkz. ShipmentsDB.delivered_on).
    delivered_day = request.query_params.get("delivered_day", "")

    criteria = dict(status=status, channel=channel, season=season,
                    shipment_method=shipment_method,
                    search=search, delivered_day=delivered_day, **filters)
    db = deps.get_shipments_db()
    total = db.count_parcels(**criteria)
    # `?page=abc`, eksi sayfa ve sondan tasan sayfa korumalari `web/paging.py`de
    # — giris kaydi sayfalanirken ayni mantik ikinci kez yazilmasin diye.
    win = paging.window(request, total, limit)
    page = win["page"] if paged else 1
    parcels = db.list_parcels(limit=limit,
                              offset=win["offset"] if paged else 0,
                              **criteria)
    ctx = {
        "filter_status": status,
        "filter_channel": channel,
        "filter_season": season,
        "filter_shipment_method": shipment_method,
        "filter_search": search,
        "filter_delivered_day": delivered_day,
        # Huni menusu sayaclari BU sozlukten uretilir (bkz. `_menu_options`):
        # sayaclar listeyle ayni suzgeclerden gecmezse "kanal=FedEx (289)"
        # secikken statu menusunde "delivered 1000+" yaziyordu.
        "criteria": criteria,
        "f": filters,
        "active_filter_count": sum(1 for v in (*status, *channel, *season, *shipment_method, search,
                                               delivered_day, *filters.values()) if v),
        "page": page,
        "pages": win["pages"],
        "total": total,
        "page_from": (page - 1) * limit + 1 if parcels else 0,
        "page_to": (page - 1) * limit + len(parcels),
    }
    return parcels, ctx


STATUS_ORDER = ["created", "in_transit", "out_for_delivery", "delivered", "exception", "returned", "cancelled"]


def season_options(db, selected=()) -> list[tuple]:
    """Sezon suzgecinin secenekleri: (kod, etiket, adet, secili mi).

    Panel ve takip sayfasi AYNI listeyi kullanir; secenekler ERP'den gelen
    gercek sezonlardir, elle tutulan bir liste yeni sezon acildiginda unutulur.
    En yeni sezon ustte: gecis doneminde iki sezon birden acik oluyor ve
    kullanicinin aradigi hemen her zaman yenisi.
    """
    counts = db.season_counts()
    return [(code, code, counts[code], code in selected)
            for code in sorted(counts, key=config.season_sort_key, reverse=True)]


def shipment_method_options(db, selected=()) -> list[tuple]:
    """Gonderim sekli suzgecinin secenekleri: (kod, etiket, adet, secili mi)."""
    counts = db.shipment_method_counts()
    return [(code, code, counts[code], code in selected)
            for code in sorted(counts.keys())]


def _menu_options(db, filter_ctx: dict) -> dict:
    """Sutun basligindaki huni menusu: (kod, etiket, adet, secili mi).

    Sayaclar DINAMIKTIR: her suzgecin secenekleri sayilirken DIGER suzgecler
    uygulanir, kendi suzgeci uygulanmaz. Kendi suzgecini de uygulasaydik
    secili olmayan her secenek 0 gorunur ve menu kullanilamaz hale gelirdi.

    Eskiden sayaclar tum tablodan geliyordu; kullanici "kanal=FedEx (289)"
    secikken statu menusunde "delivered 1000+" goruyordu (bildirildi
    2026-09-03).
    """
    criteria = filter_ctx.get("criteria", {})

    def counts(column: str, own_key: str) -> dict:
        return db.facet_counts(column, **{k: v for k, v in criteria.items()
                                          if k != own_key})

    status_counts = counts("status", "status")
    channel_counts = counts("channel", "channel")
    season_counts = counts("season", "season")
    method_counts = counts("shipment_method", "shipment_method")

    return {
        "season_options": [
            (code, code, season_counts.get(code, 0), code in filter_ctx["filter_season"])
            for code in sorted(season_counts, key=config.season_sort_key, reverse=True)
        ],
        "shipment_method_options": [
            (code, code, method_counts.get(code, 0),
             code in filter_ctx.get("filter_shipment_method", ()))
            for code in sorted(method_counts)
        ],
        "status_options": [
            (code, status_label(code), status_counts.get(code, 0),
             code in filter_ctx["filter_status"])
            for code in STATUS_ORDER
        ],
        "channel_options": [
            (code, i18n.t(f"tracking.channel_{code.lower()}"),
             channel_counts.get(code, 0),
             code in filter_ctx["filter_channel"])
            for code in ("NL", "IE", "FEDEX")
        ],
    }


@router.get("", response_class=HTMLResponse)
def tracking_page(request: Request):
    db = deps.get_shipments_db()
    parcels, filter_ctx = _query(request)
    ctx = {
        "request": request,
        "parcels": parcels,
        # Secim siniri sablonda SABIT YAZILMAZ: ucta 400 doner, ekranda
        # uyari cikar — iki sayi ayri yerde tutulursa biri degisip digeri kalir.
        "bulk_limit": CONTENTS_BULK_LIMIT,
        **_menu_options(db, filter_ctx),
        **filter_ctx,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "tracking.html", ctx)


@router.get("/rows", response_class=HTMLResponse)
def tracking_rows(request: Request):
    """HTMX kismi yenileme - sadece <tr>'ler."""
    parcels, filter_ctx = _query(request)
    ctx = {
        "request": request,
        "parcels": parcels,
        **filter_ctx,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "_partials/tracking_rows.html", ctx)


# Ekranda tek SAYFA var; dosyada tum suzgec sonucu olmali.
EXPORT_LIMIT = 20000

# Disa aktarilan kolonlar: (baslik anahtari, parca alani). Ekrandaki tabloyla
# AYNI sira, ama ulke/posta kodu ve iki tarih kendi kolonlarinda — ekranda yer
# olmadigi icin alt satira sikistirilmislardi, Excel'de suzulemezlerdi.
EXPORT_COLUMNS = [
    ("field.channel", "channel"),
    ("field.tracking_no", "tracking_no"),
    ("field.store_code", "store_code"),
    ("field.invoice_number", "invoice_number"),
    ("field.consignee", "consignee_name"),
    ("field.country", "country"),
    ("field.zip_code", "zip_code"),
    ("field.season", "season"),
    ("field.status", "status"),
    ("field.shipment_date", "shipment_date"),
    ("field.delivered_date", "delivered_date"),
    ("field.shipment_method", "shipment_method"),
]


def _list_table(parcels: list[dict]) -> tuple[list[str], list[list]]:
    """Suzulmus listeyi duz bir tabloya cevirir.

    Statu ve teslim damgasi EKRANDAKI haliyle yazilir: dosyayi acan kisi
    `out_for_delivery` ya da UTC damga degil, panelde gordugu metni bekler.
    """
    headers = [i18n.t(key) for key, _ in EXPORT_COLUMNS]
    rows = []
    for p in parcels:
        row = []
        for _, field in EXPORT_COLUMNS:
            value = p.get(field) or ""
            if field == "status":
                value = status_label(value)
            elif field == "delivered_date" and value:
                value = timez.stamp(value) or value
            row.append(value)
        rows.append(row)
    return headers, rows


@router.get("/export.xlsx")
async def tracking_xlsx(request: Request):
    """Ekrandaki suzgeclerin AYNISIYLA bir Excel dosyasi."""
    parcels, _ = _query(request, limit=EXPORT_LIMIT, paged=False)
    headers, rows = _list_table(parcels)
    data = exports.to_xlsx(headers, rows, i18n.t("tracking.list_title"))
    name = exports.stamped_name("takip-listesi", "xlsx")
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/export.pdf")
async def tracking_pdf(request: Request):
    parcels, _ = _query(request, limit=EXPORT_LIMIT, paged=False)
    headers, rows = _list_table(parcels)
    subtitle = i18n.t("export.subtitle", count=len(parcels),
                      at=timez.now().strftime(timez.STAMP_FMT))
    data = exports.to_pdf(headers, rows, i18n.t("tracking.list_title"), subtitle)
    name = exports.stamped_name("takip-listesi", "pdf")
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# Panelin arama kutusu tek satirlik bir cevap bekler; 1000 satir orada
# okunamaz zaten, kullanici listeyi gormek isterse /tracking'e gider.
QUICK_LIMIT = 50


@router.get("/quick", response_class=HTMLResponse)
def tracking_quick(request: Request, search: str = "", season: str = ""):
    """Paneldeki hizli arama (HTMX) — ayni satir sablonu, ayni detay paneli.

    `/tracking/rows`tan tek farki HICBIR suzgec yokken bos donmesi: orada bos
    arama "suzgec yok" demek ve tum liste gelir, panelde ise kullanici daha
    hicbir sey secmeden ekrana 1000 satir yikilirdi.

    Sezon TEK BASINA da arama baslatir: kutuya bir sey yazmadan sezon secmek
    "bana bu sezonun kolilerini goster" demektir, sessiz bos tablo degil.
    """
    query = search.strip()
    asked = bool(query or season)
    parcels = (deps.get_shipments_db().list_parcels(
                   search=query, season=season, limit=QUICK_LIMIT)
               if asked else [])
    ctx = {
        "request": request,
        "parcels": parcels,
        "truncated": len(parcels) >= QUICK_LIMIT,
        "searched": asked,
        # Panelde takip no POD'un yaninda; /tracking'te bastadir (bkz. sablon).
        "quick": True,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "_partials/tracking_rows.html", ctx)


def _group_consecutive_events(events: list[dict]) -> list[dict]:
    """Art arda gelen ayni statu+metin olaylarini tek satira toplar.

    `events` en yeniden en eskiye siralidir (bkz. `ShipmentsDB.events_for`).
    GLS bazi kolilerde ayni "depoda bekliyor" olayini gunlerce tekrarlar
    (canli ornek: 11 gun ust uste "The parcel is stored in the parcel
    center."); hepsini ayri satir olarak listelemek zaman cizelgesini
    okunmaz hale getirir. Grup, en yeni olayin tarihini tasir; tum tekil
    zaman damgalari `timestamps` altinda saklanir (panelde acilir-kapanir).
    """
    grouped: list[dict] = []
    for event in events:
        if grouped and grouped[-1]["status"] == event["status"] and grouped[-1]["note"] == event["note"]:
            grouped[-1]["timestamps"].append(event["at"])
        else:
            grouped.append({**event, "timestamps": [event["at"]]})
    for group in grouped:
        group["count"] = len(group["timestamps"])
    return grouped


@router.get("/detail/{tracking_no}", response_class=HTMLResponse)
async def parcel_detail(
    request: Request,
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
):
    """Sag panelde tek kargo detayi (HTMX)."""
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found"))

    # Canli GLS takibinden son olaylari sorgulayip tum gecmisi senkronize et (en fazla 6sn bekle)
    channel = parcel.get("channel") or "NL"
    provider = providers.tracking_provider_for(channel)
    if provider:
        try:
            name, client = provider
            if name == "nl_tt":
                from tracking.scheduler import map_nl_tt_parcel, _live_delivery_scan

                def _refresh_one():
                    """Toplu ucun HAM cevabi + gerekirse teslim taramasi.

                    Toplu uc sinir otesi (FR/ES/BE) son teslim adimini geri
                    yazmiyor ("exception"/"in_transit"te takili kalabiliyor —
                    bkz. Tracker._nl_results). Bu detay ekrani DOGRUDAN toplu
                    ucu okuyup DB'ye yazinca, tur'un `deliveryScanInfo` ile
                    zaten duzelttigi "delivered" durumunu GERI DUSURUYORDU:
                    liste "delivered" gosterirken satira tiklaninca acilan
                    panel "in_transit" yaziyor VE kaydi oyle EZIYORDU
                    (kullanici 2026-09-04: "ilk gordugumuz statu delivered'ti
                    ama yanda acilan modalda intransit"). Tur ile AYNI
                    kontrolu burada da yapariz.
                    """
                    data = client.parcel_details([tracking_no])
                    p_data = data.get(tracking_no)
                    if not p_data:
                        return None
                    info = map_nl_tt_parcel(p_data)
                    if info["status"] in ("out_for_delivery", "exception", "in_transit"):
                        live = _live_delivery_scan(client, tracking_no, p_data)
                        if live is not None:
                            return live
                    return info

                info = await asyncio.wait_for(asyncio.to_thread(_refresh_one), timeout=6.0)
                if info:
                    db.update_tracking_info(
                        tracking_no,
                        last_event_at=info["event_at"],
                        last_event_text=info["note"],
                        delivered_date=info["delivered_date"],
                        explain=info.get("raw_note") or info["note"],
                        handed_over_at=info.get("handed_over_at", ""),
                        issue_text=info.get("issue_text", ""),
                        tracking_url=info.get("tracking_url", ""),
                    )
                    synced_events = False
                    if info.get("raw_events"):
                        db.sync_events(tracking_no, info["raw_events"])
                        synced_events = True
                    # `sync_events` GLS'in TAM gecmisini zaten yazdiysa
                    # `update_status` AYRICA sentetik bir olay yazmamali —
                    # bkz. ShipmentsDB.update_status.
                    db.update_status(tracking_no, info["status"], note=info["note"] or "tracker",
                                      log_event=not synced_events)
                    parcel = db.get(tracking_no)
        except Exception:
            pass

    events = _group_consecutive_events(db.events_for(tracking_no))
    ctx = {
        "request": request,
        "p": parcel,
        "events": events,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "_partials/tracking_detail.html", ctx)


@router.get("/contents/{tracking_no}", response_class=HTMLResponse)
async def box_contents(
    request: Request,
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
):
    """Kolinin icindeki barkodlar/adetler — beden matrisi (HTMX, tembel yuklenir).

    HER ZAMAN 200 doner. HTMX 4xx/5xx yanitlarda hedefin govdesini DEGISTIRMEZ:
    hata durumunda kullanici bos bir kutu gorur ve neden bos oldugunu bilemezdi.
    Durum `state` alaninda tasinir: ok | off | empty | error.

    Sorgu SQLite'a degil dogrudan MSSQL'e gider (kullanici karari) ve surucu
    senkron — olay dongusu bloklanmasin diye ayri isliğe alinir.
    """
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found"))

    state, matrix = await _load_contents(tracking_no, parcel.get("season", ""))
    ctx = {"request": request, "p": parcel, "state": state, "matrix": matrix,
           **deps.template_context()}
    return templates.TemplateResponse(request, "_partials/box_contents.html", ctx)


async def _load_contents(tracking_no: str, season: str) -> tuple[str, dict | None]:
    """(durum, matris). Ekran da disa aktarma da AYNI yoldan gecer."""
    if not config.mssql_configured():
        return "off", None

    def read():
        # Iki sorgu TEK islikte: icerik sezon gorunumunden, kolinin kendi
        # barkodu taban tablodan gelir (gorunumlerde o alan yok).
        client = ERPClient(timeout=20)
        return (contents.fetch_contents(client, tracking_no, season),
                contents.fetch_box_code(client, tracking_no))

    try:
        rows, box_code = await asyncio.to_thread(read)
    except Exception as exc:
        # Ayrinti log'a, kullaniciya kisa mesaj: MSSQL hatalari sunucu adi
        # ve kullanici adi icerebiliyor.
        log.warning("koli icerigi alinamadi (%s): %s", tracking_no, exc)
        return "error", None

    matrix = contents.build_matrix(rows)
    matrix["box_code"] = box_code
    return ("ok" if rows else "empty"), matrix


def _contents_table(matrix: dict, lang: str = "") -> tuple[list[str], list[list]]:
    """Beden matrisini duz bir tabloya acar — Excel'de suzulebilsin diye model ve
    renk AYRI kolonlar (ekranda tek hucrede, orada yer yok).

    Son satir TOPLAM: her beden sutununun toplami + genel adet. Eskiden yoktu ve
    indirilen dosya kendini dogrulamiyordu — kullanici ekrandaki sayilarla
    dosyadakileri elle toplayip karsilastirmak zorunda kaliyordu.

    `lang`: bos ise ISTEGIN dili (panel), sabit verilirse ONA gore basar —
    FedEx koli gorseli PDF'i her zaman "en" verir (kullanici karari,
    2026-09-02: "pdflerin de ingilizce olması lazım daha panelden
    oluştururkende"), operatorun panel dili Turkce olsa bile.
    """
    sizes = matrix["sizes"]
    headers = ([i18n.t("export.style", lang), i18n.t("export.color", lang)]
               + [s or "—" for s in sizes]
               + [i18n.t("export.pieces", lang)])
    rows = [
        [g["style"], g["color"]]
        + [g["qty"].get(size, "") for size in sizes]
        + [g["total"]]
        for g in matrix["groups"]
    ]
    rows.append(_total_row(matrix, sizes, lang))
    return headers, rows


def _total_row(matrix: dict, sizes: list[str], lang: str = "") -> list:
    """`TOPLAM | 5 model | <beden toplamlari…> | 34`."""
    columns = [sum(g["qty"].get(size, 0) for g in matrix["groups"]) or ""
               for size in sizes]
    return ([i18n.t("export.total_row", lang),
             i18n.t("tracking.detail.contents_summary", lang, models=matrix["models"])]
            + columns + [matrix["total"]])


async def _contents_export(tracking_no: str) -> tuple[dict, list[str], list[list]]:
    """Disa aktarilacak tablo. Icerik yoksa dosya URETILMEZ — bos bir Excel
    indirmek kullaniciya "koli bos" der, oysa sorun ERP tarafinda olabilir."""
    parcel = deps.get_shipments_db().get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found"))

    state, matrix = await _load_contents(tracking_no, parcel.get("season", ""))
    if state != "ok":
        raise HTTPException(404, i18n.t(f"tracking.detail.contents_{state}"))

    headers, rows = _contents_table(matrix)
    return parcel, headers, rows


@router.get("/contents/{tracking_no}/export.xlsx")
async def box_contents_xlsx(
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
):
    _, headers, rows = await _contents_export(tracking_no)
    data = exports.to_xlsx(headers, rows, i18n.t("tracking.detail.contents"))
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="koli-{tracking_no}.xlsx"'})


@router.get("/contents/{tracking_no}/export.pdf")
async def box_contents_pdf(
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
):
    parcel, headers, rows = await _contents_export(tracking_no)
    subtitle = " · ".join(filter(None, [
        tracking_no, parcel.get("store_code") or parcel.get("consignee_name"),
        parcel.get("invoice_number")]))
    data = exports.to_pdf(headers, rows, i18n.t("tracking.detail.contents"), subtitle)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="koli-{tracking_no}.pdf"'})


# ---------------------------------------------------------------------------
# Koli Gorseli — FedEx'te resmi POD YOK (bkz. gls_api/providers.py:
# pod_provider_for), operator bunun yerine koli fotografi yukluyor. Her
# yukleme MEVCUT fotograflarin uzerine degil YANINA eklenir; PDF her
# seferinde klasordeki TUM fotograflardan yeniden uretilir (kullanici istegi
# 2026-08-31: "yüklenen resimler pdfe gömülecek").
# ---------------------------------------------------------------------------

BOX_IMAGE_DIR = deps.OUTPUT_DIR / "BOX_IMAGES"
MAX_BOX_IMAGE_BYTES = 20 * 1024 * 1024   # POD yuklemesiyle ayni sinir (bkz. web/pod.py)
MAX_BOX_IMAGES_TOTAL = 40                # PDF'in makul kalmasi icin ust sinir


def _box_image_folder(tracking_no: str) -> Path:
    folder = BOX_IMAGE_DIR / tracking_no
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def box_image_filename(tracking_no: str) -> str:
    """Belgenin gorunen adi: "Shipment Details <takip no>.pdf".

    Kullanici karari (2026-09-02): once "Shipment Details", sonra takip
    numarasi. TEK yer — hem diske yazilan dosya, hem indirme adi, hem
    WhatsApp ekinin adi buradan gelir; ayrisirlarsa ayni belge uc farkli
    isimle dolasirdi.
    """
    return f"Shipment Details {tracking_no}.pdf"


def _sniff_image_ext(content: bytes, filename: str) -> str | None:
    """Sihirli baslikla resim turunu tespit eder; resim degilse None
    (bkz. web/routers/pod.py:upload_parcel_pod, ayni desen)."""
    if content.startswith(b"\x89PNG"):
        return "png"
    if content.startswith(b"\xff\xd8"):
        return "jpg"
    if content.startswith(b"BM"):
        return "bmp"
    if content.startswith(b"RIFF") and b"WEBP" in content[:16]:
        return "webp"
    suffix = Path((filename or "").lower()).suffix.lstrip(".")
    if suffix in ("png", "jpg", "jpeg", "bmp", "webp", "gif"):
        return "jpg" if suffix == "jpeg" else suffix
    return None


@router.post("/box-image/upload/{tracking_no}")
async def upload_box_images(
    request: Request,
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
    files: list[UploadFile] = File(...),
):
    """FedEx kolileri icin elle yuklenen koli fotograflari — bkz. modul basligi."""
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found"))

    # Belge VARKEN uzerine ekleme YOK (kullanici karari 2026-09-02: "tekrar
    # uzerine ekleme yapamasin, eski olusturdugunu kaldirmasi gereksin").
    # Arayuz yukleme alanini zaten gizliyor; bu sunucu tarafi kilit, dogrudan
    # istek atilirsa da belgenin sessizce buyumesini engeller.
    if parcel.get("box_image_path"):
        raise HTTPException(409, i18n.t("err.box_image_exists"))

    folder = _box_image_folder(tracking_no)
    next_index = len(list(folder.glob("raw_*.*"))) + 1

    saved = 0
    for i, file in enumerate(files):
        content = await read_limited(file, MAX_BOX_IMAGE_BYTES)
        if not content:
            continue
        ext = _sniff_image_ext(content, file.filename or "")
        if not ext:
            continue
        (folder / f"raw_{next_index + saved:03d}.{ext}").write_bytes(content)
        saved += 1

    if not saved:
        raise HTTPException(400, i18n.t("err.box_image_none_valid"))

    raw_files = sorted(folder.glob("raw_*.*"))[:MAX_BOX_IMAGES_TOTAL]
    images = [p.read_bytes() for p in raw_files]

    state, matrix = await _load_contents(tracking_no, parcel.get("season", ""))
    # PDF her zaman Ingilizce (kullanici karari 2026-09-02) — panel dili
    # Turkce olsa da tablo basliklari "en" ile sabitlenir.
    headers, rows = (_contents_table(matrix, lang="en") if state == "ok" else (None, None))

    # Pillow/fpdf yoksa ya da fotograf bozuksa kullaniciya ANLASILIR mesaj
    # dusmeli; ham bir 500 "yukleme neden olmadi"yi cevapsiz birakir.
    try:
        pdf_bytes = box_image_document.build(parcel, images, headers, rows)
    except box_image_document.BoxImageDocumentUnavailable:
        log.exception("Koli gorseli PDF'i uretilemedi: %s", tracking_no)
        raise HTTPException(500, i18n.t("err.box_image_pdf_failed"))

    pdf_path = folder / box_image_filename(tracking_no)
    pdf_path.write_bytes(pdf_bytes)
    db.set_box_image_path(tracking_no, str(pdf_path))

    # Fotograf yuklenip PDF olustugu ANDA dogrudan FedEx WhatsApp grubuna PDF ekli olarak gider
    channel = (parcel.get("channel") or "").strip().upper()
    is_fedex = channel == "FEDEX" or (len(tracking_no) == 12 and not tracking_no.startswith("38120177") and not tracking_no.startswith("2156"))
    if is_fedex:
        if channel != "FEDEX":
            db.conn.execute("UPDATE parcels SET channel = 'FEDEX' WHERE tracking_no = ?", (tracking_no,))
            db.conn.commit()
        try:
            from notify import whatsapp as wa
            ok, detail = wa.notify_fedex_created(db.get(tracking_no) or parcel,
                                                 pdf_bytes=pdf_bytes,
                                                 filename=box_image_filename(tracking_no))
            log.info("FedEx WhatsApp bildirimi %s: %s (detay: %s)",
                     "gonderildi" if ok else "GONDERILEMEDI", tracking_no, detail)
        except Exception:                                  # noqa: BLE001
            log.exception("FedEx WhatsApp bildirimi hata verdi: %s", tracking_no)

    events = _group_consecutive_events(db.events_for(tracking_no))
    updated_parcel = db.get(tracking_no)
    ctx = {"request": request, "p": updated_parcel, "events": events,
           **deps.template_context()}
    response = templates.TemplateResponse(request, "_partials/tracking_detail.html", ctx)
    response.headers["HX-Trigger"] = "refresh"
    return response


@router.get("/box-image/download/{tracking_no}")
async def download_box_images(
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
):
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    path = Path(parcel["box_image_path"]) if parcel and parcel.get("box_image_path") else None
    if not path or not path.exists():
        raise HTTPException(404, i18n.t("err.box_image_not_found"))
    return Response(path.read_bytes(), media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="{box_image_filename(tracking_no)}"'})


@router.post("/box-image/delete/{tracking_no}", dependencies=WRITE)
async def delete_box_images(
    request: Request,
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
):
    """Yuklenen fotograflari + uretilen PDF'i siler, yeniden yuklemeyi acar.

    Kullanici karari (2026-09-02): belge olustuktan sonra uzerine ekleme
    yapilamaz; yanlis fotograf yuklendiyse once bu kaldirilir, sonra bastan
    yuklenir. Ham fotograflar da silinir — kalirlarsa bir sonraki yukleme
    onlari yeni PDF'e yeniden gomerdi.
    """
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found"))

    folder = BOX_IMAGE_DIR / tracking_no
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    db.set_box_image_path(tracking_no, "")

    events = _group_consecutive_events(db.events_for(tracking_no))
    ctx = {"request": request, "p": db.get(tracking_no), "events": events,
           **deps.template_context()}
    response = templates.TemplateResponse(request, "_partials/tracking_detail.html", ctx)
    response.headers["HX-Trigger"] = "refresh"
    return response


# ---------------------------------------------------------------------------
# Musteri bazli toplu koli icerigi (secilen kolilerin hepsi TEK dosyada)
# ---------------------------------------------------------------------------

# 200 koli ~6 MSSQL sorgusuyla biter (2 gorunum + Erp_Box, hepsi parcali).
# Sinir keyfi degil: ustu ERP'yi salt okunur bir rapor icin uzun sure mesgul
# eder ve tarayici indirmesi zaman asimina ugrar.
CONTENTS_BULK_LIMIT = 200

# Ozet sayfasi: koli basina bir satir. `field.*` anahtarlari liste disa
# aktarmasiyla AYNI — iki dosyayi yan yana koyan kisi ayni basliklari gormeli.
BULK_SUMMARY_COLUMNS = [
    ("field.tracking_no", "tracking_no"),
    ("field.store_code", "store_code"),
    ("field.invoice_number", "invoice_number"),
    ("field.consignee", "consignee_name"),
    ("field.country", "country"),
    ("field.season", "season"),
    ("field.status", "status"),
    ("field.shipment_date", "shipment_date"),
    ("field.delivered_date", "delivered_date"),
]


def _picked_parcels(tracking_nos: list[str]) -> list[dict]:
    """Secimi DOGRULAR: bicimi tutan ve veritabaninda GERCEKTEN olan koliler.

    Numara istekten geliyor; suzulmezse uydurulmus bir deger dogrudan MSSQL
    sorgusuna girerdi. Sira kullanicinin gonderdigi sira degil, listenin kendi
    sirasi olsun diye `list_parcels` sonucu degil `db.get` ile tek tek okunur —
    secim zaten en fazla 200 satir.
    """
    db = deps.get_shipments_db()
    seen, parcels = set(), []
    for no in tracking_nos:
        no = (no or "").strip()
        if not no or no in seen or not re.fullmatch(TRACKING_NO_PATTERN, no):
            continue
        seen.add(no)
        parcel = db.get(no)
        if parcel:
            parcels.append(parcel)
    return parcels


def _bulk_sheets(parcels: list[dict], found: dict[str, list[dict]],
                 box_codes: dict[str, str]) -> list[tuple]:
    """Uc sayfa: Ozet · Icerik · Beden Matrisi."""
    summary_headers = ([i18n.t(key) for key, _ in BULK_SUMMARY_COLUMNS]
                       + [i18n.t("tracking.detail.box_code"),
                          i18n.t("tracking.detail.box_no"),
                          i18n.t("export.style"), i18n.t("export.barcode"),
                          i18n.t("export.pieces"), i18n.t("export.note_column")])
    line_headers = [i18n.t("field.tracking_no"), i18n.t("field.store_code"),
                    i18n.t("field.invoice_number"),
                    i18n.t("tracking.detail.box_code"), i18n.t("export.style"),
                    i18n.t("export.color"), i18n.t("export.size"),
                    i18n.t("export.barcode"), i18n.t("export.pieces")]

    summary_rows, line_rows, all_rows = [], [], []
    for parcel in parcels:
        no = parcel["tracking_no"]
        rows = found.get(no, [])
        box_code = box_codes.get(no, "")
        matrix = contents.build_matrix(rows)
        all_rows.extend(rows)

        head = []
        for _, field in BULK_SUMMARY_COLUMNS:
            value = parcel.get(field) or ""
            if field == "status":
                value = status_label(value)
            elif field == "delivered_date" and value:
                value = timez.stamp(value) or value
            head.append(value)
        # Icerigi cikmayan koli de listede KALIR ("kayit yok"): sessizce
        # atlanirsa kullanici o koliyi bos saniyor. Canlida 986 parcanin 71'i
        # boyle (sezon gorunumlerinde yoklar).
        summary_rows.append(head + [box_code, matrix["box_no"], matrix["models"],
                                    matrix["lines"], matrix["total"],
                                    "" if rows else i18n.t("export.no_records")])

        for row in rows:
            line_rows.append([no, parcel.get("store_code") or "",
                              parcel.get("invoice_number") or "", box_code,
                              row["style"], row["color"], row["size"],
                              row["barcode"], row["qty"]])

    total = contents.build_matrix(all_rows)
    summary_rows.append(
        [i18n.t("export.total_row")] + [""] * (len(BULK_SUMMARY_COLUMNS) + 1)
        + [total["models"], total["lines"], total["total"], ""])

    matrix_headers, matrix_rows = _contents_table(total)
    return [
        (i18n.t("export.sheet_summary"), summary_headers, summary_rows),
        (i18n.t("export.sheet_lines"), line_headers, line_rows),
        (i18n.t("export.sheet_matrix"), matrix_headers, matrix_rows),
    ]


@router.post("/contents/export.xlsx")
async def bulk_contents_xlsx(tracking_no: list[str] = Form(default=[])):
    """Secili kolilerin icerigi — TEK Excel, uc sayfa.

    GET degil POST: 200 takip numarasi bir adres satirina sigmaz. Bu bir dosya
    indirmesi (HTMX degil), bu yuzden gercek HTTP hata kodlari kullanilabilir —
    `/contents/{no}`nun "daima 200 don" kurali buraya islemez.
    """
    parcels = _picked_parcels(tracking_no)
    if not parcels:
        raise HTTPException(400, i18n.t("export.none_selected"))
    if len(parcels) > CONTENTS_BULK_LIMIT:
        raise HTTPException(400, i18n.t("export.too_many",
                                        limit=CONTENTS_BULK_LIMIT,
                                        count=len(parcels)))
    if not config.mssql_configured():
        raise HTTPException(503, i18n.t("tracking.detail.contents_off"))

    numbers = [p["tracking_no"] for p in parcels]

    def read():
        # Koli basina ucer sorgu yerine TOPLU okuma (bkz. erp/contents.py).
        client = ERPClient(timeout=60)
        return (contents.fetch_many(client, numbers),
                contents.fetch_box_codes(client, numbers))

    try:
        found, box_codes = await asyncio.to_thread(read)
    except Exception as exc:
        log.warning("toplu koli icerigi alinamadi (%d koli): %s", len(numbers), exc)
        raise HTTPException(502, i18n.t("tracking.detail.contents_error"))

    data = exports.to_xlsx_multi(_bulk_sheets(parcels, found, box_codes))
    # Secimin tamami ayni magazaysa dosya adi onu tasir: kullanicinin derdi
    # zaten "NUNAT1N'in kolileri", indirilenler klasorunde ayirt edilebilmeli.
    stores = {p.get("store_code") or "" for p in parcels}
    prefix = "koli-icerik"
    if len(stores) == 1 and (store := stores.pop()):
        prefix = f"koli-icerik-{exports.safe_filename(store)}"
    name = exports.stamped_name(prefix, "xlsx")
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


# Yalnizca HENUZ YOLA CIKMAMIS koli iptal edilebilir. GLS zaten tasimaya baslamis
# bir koliyi iptal etmez (hata doner) ama istegi hic gondermemek daha iyi: operator
# "iptal ettim" sanip koliyi rafta birakmasin.
CANCELLABLE_STATUSES = {"created"}


@router.post("/cancel/{tracking_no}", response_class=HTMLResponse,
             dependencies=WRITE)
async def cancel_label(
    request: Request,
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
):
    """Yanlis basilan etiketi GLS tarafinda kapatir — GERI ALINAMAZ.

    API ile uretilen sevkiyatlar GLS'in kendi Print&Ship ekraninda HIC gorunmez,
    bu yuzden elle silinemezler; tek yol budur (GLS Servicedesk, kayit M2608 0415).

    Koli Track & Trace'te GORUNMEYE DEVAM EDER, yalnizca surecleri durur.
    GLS tarafinda kalmasi sorun degil; BIZDEN tamamen silinir (kullanici
    karari, 2026-09-01: "biz labeli silersek bizden komple silinsin, GLS'te
    kalabilir ama bizim db'de hic olmamasi lazim, o yuzden sorgular koliden
    olmali"). Eskiden kayit `cancelled` statusuyle duruyordu.

    Silinen koli toplu etikette YENIDEN gorunur — dogrusu budur: etiketi
    silinmis koli yeniden etiketlenebilmelidir (bkz. delete_parcel).
    """
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found"))
    if parcel["channel"] != providers.CHANNEL_NL:
        raise HTTPException(400, i18n.t("tracking.cancel.err_channel"))
    if parcel["status"] not in CANCELLABLE_STATUSES:
        raise HTTPException(400, i18n.t("tracking.cancel.err_status",
                                        status=status_label(parcel["status"])))

    _, client = providers.strict_provider_for(providers.CHANNEL_NL)
    client.delete_label(tracking_no)
    # GLS'e istek GITTIKTEN sonra silinir: cagri patlarsa kaydi ucurup aslinda
    # gecerli kalan bir etiketi gozden kaybetmeyelim.
    log.info("Etiket silindi ve kayit dusuruldu: %s (kullanici %s)",
             tracking_no, request.state.user.get("username", ""))
    # ERP'deki koliye yazdigimiz takip numarasi da BOSALTILIR: koli yeniden
    # etiketlenebilmeli (kullanici kurali "sorgular koliden olmali"). Yalnizca
    # BIZIM yazdigimiz numara silinir; baskasinin yazdigi deger korunur.
    if parcel.get("box_rec_id"):
        writeback.clear_tracking_number(parcel["box_rec_id"], tracking_no)
    db.delete_parcel(tracking_no)
    # Kayit artik YOK; detay paneli 404 verirdi. Panel bosaltilir ve liste
    # yenilenir (HX-Trigger, POD yuklemesindeki desenin aynisi).
    response = HTMLResponse("")
    response.headers["HX-Trigger"] = "refresh"
    return response


@router.post("/refresh", dependencies=WRITE)
async def force_refresh(request: Request):
    """Manuel tarama tetikle.

    `tick()` bir turda yuzlerce GLS istegi + ayristirma yapar. DOGRUDAN
    cagrilirsa (eski hali) tum asyncio event loop'u tur boyunca (~40-60 sn)
    kilitlenir — panel herkes icin donar (kullanici 2026-09-03: "yine
    kilitleniyor"). Zamanli tur gibi ayri bir is parcaciginda calistirilir.
    """
    tracker = getattr(request.app.state, "tracker", None)
    if tracker:
        await asyncio.to_thread(tracker.tick)
    return {"ok": True, "last_run": getattr(tracker, "last_run_at", None)}


@router.post("/sync-mssql", response_class=HTMLResponse, dependencies=WRITE)
async def sync_mssql(request: Request):
    """Takip listesini Sentez/BlueCherry MSSQL'inden tazeler (SALT OKUMA).

    Yalnizca ekler/gunceller — paneli SIFIRLAMAZ. Sifirlama tek tikla veri
    kaybina yol acmasin diye sadece CLI'da: `python -m erp.sync --reset`.
    """
    if not config.mssql_configured():
        raise HTTPException(503, i18n.t("err.mssql_not_configured"))

    result = await asyncio.to_thread(erp_sync, deps.get_shipments_db())
    ctx = {
        "request": request,
        "result": result,
        "filename": f"MSSQL · {result['source']}",
        "sheets": [],
        "used_sheet": "",
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "_partials/upload_result.html", ctx)


ALLOWED_UPLOAD_EXT = {".xlsx", ".xls", ".xlsm"}
# Takip listesi Excel'leri binlerce satir tasiyabilir; POD gorseline gore
# daha genis bir tavan (bkz. web/uploads.py).
MAX_TRACKING_LIST_BYTES = 50 * 1024 * 1024


@router.post("/upload", response_class=HTMLResponse, dependencies=WRITE)
async def upload_tracking_list(
    request: Request,
    file: UploadFile = File(...),
    sheet: str = Form(""),
    all_sheets: str = Form(""),
):
    """EMC formatinda bir tracking list Excel dosyasini toplu olarak ice aktar.

    `all_sheets` isaretliyse dosyadaki tum sayfalar aktarilir ("TESLIMAT KANITI
    ALINACAKLAR", "Problematic Packages", "Return to Sender" dahil).
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise HTTPException(400, i18n.t("err.excel_only", exts=", ".join(ALLOWED_UPLOAD_EXT)))

    contents = await read_limited(file, MAX_TRACKING_LIST_BYTES)
    if not contents:
        raise HTTPException(400, i18n.t("err.file_empty"))

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        sheets = list_sheets(tmp_path)
        db = deps.get_shipments_db()
        if all_sheets:
            result = import_all_sheets(tmp_path, db)
            used_sheet = f"tum sayfalar ({len(sheets)})"
        else:
            target_sheet = sheet if sheet in sheets else 0
            result = import_tracking_list(tmp_path, db, sheet=target_sheet)
            used_sheet = result["sheet"]
    finally:
        tmp_path.unlink(missing_ok=True)

    ctx = {
        "request": request,
        "result": result,
        "filename": file.filename,
        "sheets": sheets,
        "used_sheet": used_sheet,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "_partials/upload_result.html", ctx)


@router.get("/{tracking_no}", response_class=HTMLResponse)
async def tracking_direct(
    request: Request,
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
):
    """Dogrudan /tracking/{takip_no} adresine gelindiginde arama yapilmis olarak ana sayfaya yonlendirir."""
    return RedirectResponse(f"/tracking?q={tracking_no}", status_code=302)


@router.post("/note/{tracking_no}")
async def update_parcel_note(
    request: Request,
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
    note: str = Form(default=""),
):
    """Kargo ozel teslimat notu / PIN kodu aciklamasi kaydeder ve resmi POD PDF'ini arsivler."""
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found"))

    clean_note = note.strip()
    db.update_tracking_info(tracking_no, issue_text=clean_note)
    if parcel.get("status") != "delivered":
        db.update_status(tracking_no, "delivered")

    updated_parcel = db.get(tracking_no)

    # Not girildiyse ve resmi POD PDF'i henuz yoksa (veya not guncellendiyse),
    # resmi POD sablonumuzdan teslimat/PIN notlu PDF belgesini otomatik uretip arsivleyelim.
    if clean_note:
        try:
            from web import pod_document
            from web.routers.pod import store_pod
            pdf_bytes = pod_document.customer_document(updated_parcel, b"")
            store_pod(updated_parcel, pdf_bytes, media_type="pdf")
            updated_parcel = db.get(tracking_no)
        except Exception:
            log.exception("Teslimat notu icin POD belgesi uretilemedi: %s", tracking_no)

    events = _group_consecutive_events(db.events_for(tracking_no))
    ctx = {
        "request": request,
        "p": updated_parcel,
        "events": events,
        **deps.template_context(),
    }
    target = request.headers.get("HX-Target", "")
    template_name = "_partials/pod_modal.html" if target == "pod-modal-container" else "_partials/tracking_detail.html"
    response = templates.TemplateResponse(request, template_name, ctx)
    response.headers["HX-Trigger"] = "refresh"
    return response

