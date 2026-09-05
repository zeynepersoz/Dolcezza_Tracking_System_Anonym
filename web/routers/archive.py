"""Belge arsivi: diskte duran POD ve etiket PDF'lerini arama/indirme.

Neden ayri bir sayfa: `/pod` teslim edilmis parcalari listeler (belge olsun
olmasin), `/dispatch` yalnizca SECILEN GUNUN etiketlerini gosterir. Gecmis bir
etiketi ya da POD'u aramanin tek yolu tarih tahmin etmekti. Burasi "dosyasi
olan her sey, tek yerde" sorusunu yanitlar.

Arsivleme mekanizmasi DEGISMEDI: POD teslimat algilandigi an
`tracking/scheduler.py:_archive_pods()` ile, etiket ise uretildigi an
`dispatch/engine.py` ile diske yaziliyor. Bu sayfa yalnizca zaten orada olani
gorunur kilar — GLS'in veriyi silmesinden onceki yakalama sorunu cozulmus
durumda (bkz. `web/routers/pod.py:_fetch_pod_bytes`, once diske bakar).
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, Response

import i18n
from auth import access
from auth.dependencies import require_role
from gls_api import config
from web.templating import templates
from web import deps

router = APIRouter(prefix="/archive", tags=["archive"])

TRACKING_NO_PATTERN = r"^[A-Za-z0-9]{1,20}$"

KINDS = ("", "pod", "label")

# EN UST KLASOR BELGE TURUDUR: POD ve etiket ayni klasorde gorunmemeli
# (kullanici istegi). Altlari diskteki duzenin ayni:
#   POD    -> `output/POD/{yontem}/{ulke}/{MAGAZA}-{FATURA}.pdf`
#   Etiket -> `output/labels/{SEZON}/{GG.AA.YYYY}/{MAGAZA}/{MAGAZA}-{FATURA} 1st box.pdf`
# Etiketin gunu ile `parcels.shipment_date` AYNI degerdir (dispatch/engine.py),
# bu yuzden panel klasoru diskteki klasorle birebir ortusur.
#
# Etiket dali onceden YALNIZCA gune bakiyordu; kullanici (2026-09-01) diskteki
# duzenin panelde de gorunmesini istedi: sezon -> gun -> magaza. Seviyelerin
# hepsi zaten vardi (bkz. ShipmentsDB.ARCHIVE_LEVELS), yalnizca bu dal
# eksikti — magaza klasorunun altinda her kolinin KENDI etiketi durur.
TREES = {"pod": ("season", "country", "store"),
         "label": ("season", "day", "store")}

# Etiket dali yalnizca yazabilenlere gorunur: `viewer` etiket isine hic
# bakmayacak (kullanici karari), POD arsivi ise isinin merkezi.
LABEL_ROLES = access.WRITERS

# Tum seviyelerin birlesimi — istekten okunan/URL'e yazilan alanlar.
LEVELS = tuple(dict.fromkeys(level for tree in TREES.values() for level in tree))

# Adi bos klasor (magaza kodu girilmemis parca) URL'de tasinamaz; bu isaret
# "adi bos olanlar" demektir. Gercek bir ulke/magaza kodu tek tire olamaz.
BLANK = "-"


def _season_map(db, kind: str) -> dict[str, str]:
    """`source_sheet` -> sezon kodu. Sezon `parcels`ta kolon DEGIL, kaynak
    adindan turer (`config.season_from_source`); suzgec kurulmadan once bu
    esleme lazim."""
    return {row["name"]: config.season_from_source(row["name"])
            for row in db.archive_folders("season", kind=kind)}


def _folders(db, level: str, seasons: dict, **filters) -> list[dict]:
    """Bir seviyenin klasorleri. Sezonda ayni koda dusen kaynaklar toplanir;
    aksi halde 'Vision_FA26_Boxed_EUROPE' ve 'Erp_Box' iki ayri FA26 klasoru
    olurdu."""
    rows = db.archive_folders(level, **filters)
    if level != "season":
        return rows
    merged: dict[str, int] = {}
    for row in rows:
        name = seasons.get(row["name"], "")
        merged[name] = merged.get(name, 0) + row["n"]
    return [{"name": name, "n": n} for name, n in sorted(merged.items())]


def _kind_folders(db) -> list[dict]:
    """Kokteki iki kart: POD ve Etiket. Sayim icin yeni sorgu YOK — sezon
    gruplamasinin toplami her parcayi bir kez sayar.

    Bos dal GIZLENMEZ: etiket hic basilmamissa bile kullanici klasoru gorup
    "0 belge" okumali, yoksa ozelligin kayboldugunu saniyor."""
    return [{"name": kind, "label": i18n.t(f"archive.tree_{kind}"),
             "n": sum(row["n"] for row in db.archive_folders("season", kind=kind))}
            for kind in TREES]


def _allowed_kinds(request: Request) -> tuple[str, ...]:
    role = (getattr(request.state, "user", None) or {}).get("role", "")
    return tuple(TREES) if role in LABEL_ROLES else ("pod",)


def _url(picked: dict, kind: str, level: str = "", value: str = "") -> str:
    """Klasor bagi. HTMX DEGIL duz bag: tarayici geri tusu ve paylasilabilir
    adres bedava geliyor, arsivde ikisi de lazim."""
    if level == "kind":
        return f"/archive?{urlencode({'kind': value})}"
    tree = TREES.get(kind, ())
    params = {name: picked.get(name) for name in tree if picked.get(name)}
    if level:
        params[level] = value or BLANK
        # Yeni secim alt seviyeleri gecersiz kilar.
        for deeper in tree[tree.index(level) + 1:]:
            params.pop(deeper, None)
    if kind:
        params = {"kind": kind, **params}
    return "/archive" + (f"?{urlencode(params)}" if params else "")


def _crumbs(picked: dict, kind: str) -> list[dict]:
    """Kirinti yolu: Belge Arsivi > POD > FA26 > DE > NQVDE1N."""
    crumbs = [{"label": i18n.t("archive.crumb_root"), "url": "/archive"}]
    if not kind:
        return crumbs
    crumbs.append({"label": i18n.t(f"archive.tree_{kind}"), "url": _url({}, kind)})
    tree = TREES[kind]
    for i, name in enumerate(tree):
        if not picked[name]:
            break
        crumbs.append({"label": picked[name],
                       "url": _url({k: picked[k] for k in tree[:i + 1]}, kind)})
    return crumbs


def _query(request: Request) -> dict:
    """Sayfa ve HTMX kismi ayni sorguyu kullansin diye tek yerde."""
    params = request.query_params
    search = params.get("q", "").strip()
    kind = params.get("kind", "")
    if kind not in KINDS:
        kind = ""

    # Yetki: menuyu gizleyip URL'i acik birakmak koruma degildir — elle
    # `?kind=label` yazan `viewer` da 403 alir. Tek dal kaliyorsa kok seviye
    # (tek kartlik "klasor" ekrani) anlamsiz olur, dogrudan o dala girilir.
    kinds = _allowed_kinds(request)
    if kind and kind not in kinds:
        raise HTTPException(403, i18n.t("err.forbidden"))
    if len(kinds) == 1:
        kind = kinds[0]

    db = deps.get_shipments_db()

    # Verilmemis ILK seviye klasor olarak listelenir; hepsi verilmisse dosyalar.
    # Arama TUM klasorleri atlar (bulundugu klasoru de): kullanici zaten dosyanin
    # hangi klasorde oldugunu bilmedigi icin ariyor. Kirinti yolu da bunu soyler.
    tree = TREES.get(kind, ())
    picked = {name: params.get(name, "") if not search and name in tree else ""
              for name in LEVELS}
    depth = next((i for i, name in enumerate(tree) if not picked[name]), len(tree))
    # Tur secilmemisse kok seviyedeyiz: once POD/Etiket ayrimi sorulur.
    level = "kind" if not kind and not search else \
        ("" if search or depth == len(tree) else tree[depth])

    # Sezon esleme yalnizca POD dalinda lazim; etiket dali gune gore ayriliyor.
    seasons = _season_map(db, kind) if "season" in tree else {}
    filters: dict = {"kind": kind, "search": search}
    if picked["season"]:
        filters["sheets"] = [sheet for sheet, name in seasons.items()
                             if name == picked["season"]]
    for name in ("country", "store", "day"):
        if picked[name]:
            filters[name] = "" if picked[name] == BLANK else picked[name]

    context = {"q": search, "kind": kind, "level": level, "folders": [],
               "parcels": [], "truncated": False, "links": {},
               "crumbs": _crumbs(picked, kind), **picked}
    if level == "kind":
        context["folders"] = _kind_folders(db)
        context["links"] = {row["name"]: _url(picked, kind, "kind", row["name"])
                            for row in context["folders"]}
    elif level:
        context["folders"] = _folders(db, level, seasons, **filters)
        context["links"] = {row["name"]: _url(picked, kind, level, row["name"])
                            for row in context["folders"]}
    else:
        rows = db.list_archive(limit=300, **filters)
        context["parcels"] = rows
        context["truncated"] = len(rows) >= 300
    return context


@router.get("", response_class=HTMLResponse)
async def archive_page(request: Request):
    return templates.TemplateResponse(request, "archive.html", {
        "request": request, **_query(request), **deps.template_context(),
    })


@router.get("/rows", response_class=HTMLResponse)
async def archive_rows(request: Request):
    """Arama kutusu her tusa basisinda burayi cagirir (HTMX)."""
    return templates.TemplateResponse(request, "_partials/archive_content.html", {
        "request": request, **_query(request), **deps.template_context(),
    })


@router.get("/label/{tracking_no}",
            dependencies=[Depends(require_role(*LABEL_ROLES))])
async def download_label(tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN)):
    """Etiketi PARCADAN indirir — `/dispatch/label` gibi magaza+gunden degil.

    Ayni magazanin ayni gun iki faturasi olabiliyor; magaza+gun ile arayan uc
    ilkine dusuyor. Burada dogru dosyanin yolu zaten kayitli (`label_pdf_path`),
    tahmine gerek yok.
    """
    parcel = deps.get_shipments_db().get(tracking_no)
    path = Path(parcel["label_pdf_path"]) if parcel and parcel["label_pdf_path"] else None
    # Yol kendi yazdigimiz kayittan geliyor; yine de cikti klasoru disina
    # cikmadigi dogrulanir — dosya sunan bir ucun sinir kontrolu olmali.
    if not path or not path.is_file() or \
            config.OUTPUT_DIR.resolve() not in path.resolve().parents:
        raise HTTPException(404, i18n.t("archive.err_no_label", no=tracking_no))
    return Response(path.read_bytes(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{path.name}"'})
