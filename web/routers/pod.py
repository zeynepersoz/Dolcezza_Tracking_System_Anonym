"""POD (teslimat kaniti) arsivi.

POD'un kaynagi kanala ve yapilandirilmis saglayiciya gore degisir:
    ShipIT REST -> /rs/tracking/parcelpod            (PDF)
    T&T SOAP    -> GetTuPOD                          (PDF veya TIFF/PNG)
    GLS NL      -> apm.gls.nl /api/tracktrace/v1/{no}/pod  (BMP, teslimat fotografi)

ARSIVDE HEM HAM GORUNTU HEM MUSTERI BELGESI DURUR.

GLS'in verdigi resim ciplak bir fotograf/imzadir; icinde takip no, alici,
teslim tarihi YOKTUR. O bilgileri iceren belgeyi biz uretiyoruz
(web/pod_document.py) ve iki surumu var (musteri / ic kayit). Ham goruntu
takip numarasiyla saklanir — ic kayit surumu ondan uretilebilsin diye. Yaninda
musteri belgesi de POD gelir gelmez `{MAGAZA}-{FATURA}` adiyla yazilir: operator
arsivi fatura numarasindan tariyor. Bu ad diskte, indirmede, toplu zip'te ve
gunluk ozet mailinin ekinde AYNIDIR (bkz. `pod_filename`).

ShipIT/T&T zaten hazir PDF dondurur; onlar oldugu gibi verilir.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, Response

import i18n
from web.templating import templates
from web import deps, pod_document
from web.uploads import read_limited
from web.routers.tracking import _group_consecutive_events, _read_filters
from gls_api import config, providers
from gls_api.nl_client import GLSNLError
from gls_api.shipit_client import ShipITError
from gls_api.tt_soap_client import TrackTraceError
from gls_api.pipeline import clean_cell, normalize_country, safe_name_part

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pod", tags=["pod"])

# Path traversal onlemi: tracking_no dosya adi olusturmada kullanildigi icin
# yalnizca alfanumerik karakterlere izin veriyoruz ("../", "/" vb. reddedilir).
TRACKING_NO_PATTERN = r"^[A-Za-z0-9]{1,20}$"
_TRACKING_NO_RE = re.compile(TRACKING_NO_PATTERN)

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "jpg": "image/jpeg",
    # GLS NL POD ucu "image/png" der ama icerik gercekte BMP'dir.
    "bmp": "image/bmp",
}
DEFAULT_MEDIA_TYPE = "pdf"

# Sinirsiz `UploadFile.read()` belleğe istenildiği kadar veri alır — kotusu bir
# istemci (ya da kazayla secilen buyuk bir dosya) sunucuyu bellek/disk tuketerek
# durdurabilir. Gercek POD belgeleri (taranmis PDF/foto) 20 MB'i asmaz.
MAX_POD_UPLOAD_BYTES = 20 * 1024 * 1024


# Ulke TAM ADLA gelirse ISO koduna cevrilir. Kaynaklar farkli yaziyor (ERP ISO
# kodu, eski Excel hatti "Germany") ve ikisi diskte AYRI klasor aciyordu:
# `De/` ile `Germany/` yan yana duruyordu, ayni ulkenin belgeleri iki yerdeydi.
_COUNTRY_CODES = {
    "germany": "DE", "france": "FR", "italy": "IT", "spain": "ES",
    "netherlands": "NL", "belgium": "BE", "austria": "AT", "poland": "PL",
    "czechia": "CZ", "czech republic": "CZ", "hungary": "HU", "sweden": "SE",
    "finland": "FI", "greece": "GR", "portugal": "PT", "ireland": "IE",
    "denmark": "DK", "romania": "RO", "slovakia": "SK", "slovenia": "SI",
    "croatia": "HR", "estonia": "EE", "latvia": "LV", "lithuania": "LT",
    "bulgaria": "BG", "luxembourg": "LU",
}


def _archive_parts(parcel: dict) -> tuple[str, str]:
    """POD arsivi icin klasor yolu: Sevkiyat Yontemi / Ulke — eski CLI pipeline'iyla
    (gls_api/pipeline.py) ayni klasorleme mantigi, disk uzerinde kolay bulunabilsin diye."""
    method = clean_cell(parcel.get("shipment_method") or "Genel Sevkiyatlar") or "Genel Sevkiyatlar"
    raw = clean_cell(parcel.get("country") or "") or "Bilinmiyor"
    country = normalize_country(_COUNTRY_CODES.get(raw.lower(), raw)) or "Bilinmiyor"
    return method, country


# Toplu etiket de ayni temizligi kullaniyor; tanim `gls_api/pipeline.py`de.
_name_part = safe_name_part


def pod_base_name(parcel: dict) -> str:
    """POD dosya adinin govdesi: `{MAGAZA}-{FATURA}` — kullanicinin istedigi bicim.

    Operator arsivi fatura numarasindan tariyor; takip numarasi ada girmiyor.
    Koli iki fatura tasiyorsa ikisi de yazilir ("ZHPDE1N-5000095+5000119").
    Magaza ya da fatura yoksa takip numarasina dusulur — ad bos kalamaz.

    Sira numarasi BURADA verilmez: cok kolili faturalarda numara kalici olmali,
    onu `ShipmentsDB.reserve_pod_name()` bir kez hesaplayip saklar.
    """
    store = _name_part(parcel.get("store_code"), 20)
    raw = (parcel.get("emc_invoices") or parcel.get("invoice_number")
           or parcel.get("reference") or "")
    invoices = [p for p in (_name_part(part, 30) for part in re.split(r"[,;]", str(raw))) if p]
    invoice = "+".join(invoices)
    if store and invoice:
        return f"{store}-{invoice}"
    return store or invoice or _name_part(parcel["tracking_no"], 20)


def pod_filename(parcel: dict, media_type: str = DEFAULT_MEDIA_TYPE,
                 kind: str = "customer") -> str:
    """Diskte, indirmede, zip icinde ve mail ekinde kullanilan TEK POD adi.

    Saklanmis `pod_name` varsa o kullanilir (sira numarasi orada). Iki belge
    surumu ayni klasore inebildigi icin ic kayit surumu onekle ayrilir.
    """
    name = parcel.get("pod_name") or pod_base_name(parcel)
    prefix = "IC-" if kind == "internal" else ""
    return f"{prefix}{name}.{media_type}"


def pod_archive_path(parcel: dict, media_type: str = DEFAULT_MEDIA_TYPE,
                     kind: str = "customer") -> str:
    """Zip icindeki tam yol: `FA26_POD/1st Truck Shipment/FR/QEBFR1N-5000012.pdf`.

    Kullanicinin istedigi duzen. Sezon kodu ayri bir ayar degil, sezon
    gorunumlerinden turer (bkz. `config.season_label`). Sevkiyat adi ERP'deki
    `UD_GonderimSekli`den gelir; alan bos oldugu surece hepsi tek klasorde
    ("Genel Sevkiyatlar") toplanir.

    Toplu indirme (`/pod/download-bulk`) ve gunluk ozet mailinin POD zip'i ayni
    yoldan gecer — arsiv iki kanalda da ayni gorunsun diye.
    """
    method, country = _archive_parts(parcel)
    season = config.season_label()
    root = f"{season}_POD/" if season else ""
    return f"{root}{method}/{country}/{pod_filename(parcel, media_type, kind)}"


def _content_disposition(filename: str) -> str:
    """HTTP header'lar Latin-1'e kisitli — gercek musteri isimlerinde Macarca/Cekce
    gibi Latin-1 disi karakterler oldugunda ham UTF-8 filename= header'i sunucuyu
    500'e dusurur. RFC 5987/6266: ASCII fallback + filename* (UTF-8, yuzde-kodlu)."""
    ascii_fallback = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    ascii_fallback = ascii_fallback or "POD.pdf"
    encoded = quote(filename)
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


DOCUMENT_BUILDERS = {
    "customer": pod_document.customer_document,
    "internal": pod_document.internal_document,
}


def _info_path(pod_path: Path) -> Path:
    """POD goruntusunun yanindaki GLS teslimat bilgileri dosyasi."""
    return pod_path.with_suffix(".json")


def _read_info(pod_path: Path) -> dict:
    """Arsivlenmis GLS teslimat bilgileri; yoksa bos sozluk."""
    try:
        return json.loads(_info_path(pod_path).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def archive_document(parcel: dict, data: bytes, media_type: str, info: dict,
                     folder: Path) -> Path | None:
    """Musteri belgesini POD gelir gelmez diske yazar (`{MAGAZA}-{FATURA}.pdf`).

    Ham goruntu takip numarasiyla saklaniyor; operatorun aradigi ise fatura
    numarasi. Belge indirme aninda da uretilebiliyor ama kullanici POD'un
    sisteme dustugu anda klasorde hazir olmasini istiyor.
    """
    try:
        document, ext = build_document(parcel, data, media_type, info)
        target = folder / pod_filename(parcel, ext)
        target.write_bytes(document)
        return target
    except OSError:
        log.exception("POD belgesi arsivlenemedi: %s", parcel.get("tracking_no"))
        return None


def store_pod(parcel: dict, data: bytes, media_type: str = DEFAULT_MEDIA_TYPE,
              info: dict | None = None) -> Path:
    """POD'u arsive HAM haliyle yazar ve DB'ye isler.

    `info` (GLS teslimat bilgileri) goruntunun yanina JSON olarak yazilir: belge
    her indirmede yeniden uretildigi icin, saklanmasa her indirme apm.gls.nl'e
    iki istek daha atardi.

    Ham goruntunun yani sira musteri belgesi de ayni klasore yazilir; DB'ye
    islenen yol ham goruntudur (ic kayit surumu ondan uretilebilsin diye).
    """
    media_type = (media_type or DEFAULT_MEDIA_TYPE).lower().lstrip(".")
    if media_type not in MEDIA_TYPES:
        media_type = DEFAULT_MEDIA_TYPE
    db = deps.get_shipments_db()
    # Belge adi ilk arsivlemede kesinlesir ve bir daha degismez.
    parcel = {**parcel, "pod_name": db.reserve_pod_name(parcel["tracking_no"],
                                                        pod_base_name(parcel))}
    method, country = _archive_parts(parcel)
    folder = deps.OUTPUT_DIR / "POD" / method / country
    target = folder / f"{parcel['tracking_no']}.{media_type}"
    folder.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    if info:
        _info_path(target).write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    archive_document(parcel, data, media_type, info or {}, folder)
    db.set_pod_path(parcel["tracking_no"], str(target), media_type)
    return target


def build_document(parcel: dict, data: bytes, media_type: str, info: dict,
                   kind: str = "customer") -> tuple[bytes, str]:
    """(belge baytlari, uzanti).

    ShipIT/T&T zaten eksiksiz bir PDF dondurur — dokunulmaz. GLS NL'in ciplak
    goruntusu ise belgeye sarilir. Belge uretilemezse (font yok, goruntu bozuk)
    ham goruntu kendi uzantisiyla verilir: kanit hic inmemektense eksik insin.
    """
    if media_type == "pdf":
        return data, "pdf"
    try:
        builder = DOCUMENT_BUILDERS.get(kind, pod_document.customer_document)
        return builder(parcel, data, info), "pdf"
    except Exception:
        log.exception("POD belgesi uretilemedi, ham goruntu veriliyor: %s",
                      parcel.get("tracking_no"))
        return data, media_type


def fetch_pod(parcel: dict) -> tuple[bytes, str, dict] | None:
    """POD'u kanalin saglayicisindan ceker: (bayt, medya_tipi, GLS bilgileri).

    Saglayici yapilandirilmamissa None. Saglayici hatalari (ShipITError,
    TrackTraceError, GLSNLError) YUKARI SIZAR — cagiran taraf bunu HTTP hatasina
    mi cevirecek yoksa yutup yeniden mi deneyecek kendi bilir.
    """
    provider = providers.pod_provider_for(parcel["channel"])
    if provider is None:
        return None
    name, client = provider
    tracking_no = parcel["tracking_no"]
    if name == "shipit":
        return client.parcel_pod(tracking_no), "pdf", {}
    if name == "nl_tt":
        # Goruntu + alici/teslimat bilgileri tek anahtarla, tek seferde.
        return client.parcel_pod_bundle(tracking_no)
    data, filename = client.get_tu_pod(tracking_no)
    return data, Path(filename).suffix.lstrip(".").lower() or "pdf", {}


def archive_pod(parcel: dict) -> Path | None:
    """Teslim edilen parcanin POD'unu ceker ve arsivler; yoksa/olmazsa None.

    Takip taramasi (tracking/scheduler.py) her turda bunu cagirir. Arka planda
    calistigi icin HICBIR hata yukari cikmaz: tek bir bozuk parca turu durdurmaz,
    sonraki turda yeniden denenir.
    """
    try:
        fetched = fetch_pod(parcel)
    except (ShipITError, TrackTraceError, GLSNLError) as exc:
        log.info("POD alinamadi (%s): %s", parcel.get("tracking_no"), exc)
        return None
    except Exception:
        log.exception("POD alinamadi (%s)", parcel.get("tracking_no"))
        return None
    if fetched is None or not fetched[0]:
        return None
    try:
        return store_pod(parcel, *fetched)
    except OSError:
        log.exception("POD arsivlenemedi (%s)", parcel.get("tracking_no"))
        return None


def _fetch_pod_bytes(tracking_no: str) -> tuple[bytes, dict, str, dict]:
    """POD byte'lari + parcel kaydi + medya tipi + GLS teslimat bilgileri.

    Indirilmisse diskten okunur; GLS'e yalnizca ilk seferde gidilir.
    """
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found_no", no=tracking_no))

    if parcel.get("pod_path") and Path(parcel["pod_path"]).exists():
        # Bu ozellikten once arsivlenmis POD'larin adi henuz yok — simdi verilir.
        # Belge her indirmede yeniden uretildigi icin eski dosyalari tasimaya
        # gerek yok: ad degisimi kendiliginden gecmise de uygulanir.
        if not parcel.get("pod_name"):
            parcel["pod_name"] = db.reserve_pod_name(tracking_no, pod_base_name(parcel))
        cached = Path(parcel["pod_path"])
        media_type = parcel.get("pod_media_type") or cached.suffix.lstrip(".")
        media_type = media_type or DEFAULT_MEDIA_TYPE
        data, info = cached.read_bytes(), _read_info(cached)
        # Bu ozellikten once arsivlenmis POD'lar: belge klasorde yoksa simdi yazilir.
        if not (cached.parent / pod_filename(parcel, "pdf")).exists():
            archive_document(parcel, data, media_type, info, cached.parent)
        return data, parcel, media_type, info

    # Takip saglayicisi degil: NL'de POD ayri bir uc noktadan gelir.
    try:
        fetched = fetch_pod(parcel)
    except (ShipITError, TrackTraceError, GLSNLError) as e:
        if parcel.get("issue_text"):
            pdf_bytes = pod_document.customer_document(parcel, b"")
            target = store_pod(parcel, pdf_bytes, "pdf")
            return target.read_bytes(), parcel, "pdf", {}
        raise HTTPException(400, i18n.t("err.pod_failed", no=tracking_no, detail=e))
    if fetched is None:
        if parcel.get("issue_text"):
            pdf_bytes = pod_document.customer_document(parcel, b"")
            target = store_pod(parcel, pdf_bytes, "pdf")
            return target.read_bytes(), parcel, "pdf", {}
        raise HTTPException(400, i18n.t("err.pod_no_provider", no=tracking_no,
                                        channel=parcel["channel"]))

    data, media_type, info = fetched
    target = store_pod(parcel, data, media_type, info)
    return target.read_bytes(), parcel, target.suffix.lstrip(".").lower(), info


@router.get("", response_class=HTMLResponse)
async def pod_page(request: Request, f_channel: str = ""):
    filters = _read_filters(request)
    db = deps.get_shipments_db()
    delivered = db.list_parcels(status="delivered", channel=f_channel or None, limit=500, **filters)
    # "Tumunu sec" checkbox'i icin: <script> icine gomulu JSON, "</script>" ile
    # tag erken kapatilip stored XSS olusmasin diye "<" karakteri kaciriliyor
    # (bkz. web/importers/tracking_list.py'deki tracking_no temizligi ile ayni gerekce).
    tracking_nos_json = json.dumps([p["tracking_no"] for p in delivered]).replace("<", "\\u003c")
    active_filter_count = sum(1 for v in (f_channel, *filters.values()) if v)
    ctx = {
        "request": request,
        "delivered": delivered,
        "tracking_nos_json": tracking_nos_json,
        "f": filters,
        "f_channel": f_channel,
        "active_filter_count": active_filter_count,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "pod.html", ctx)


@router.get("/download/{tracking_no}")
async def download_pod(
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
    kind: str = "customer",
):
    """kind=customer -> musteriye gonderilebilir belge; kind=internal -> ic kayit."""
    data, parcel, media_type, info = _fetch_pod_bytes(tracking_no)
    document, ext = build_document(parcel, data, media_type, info, kind)
    filename = pod_filename(parcel, ext, kind)
    return Response(document, media_type=MEDIA_TYPES.get(ext, "application/octet-stream"),
                    headers={"Content-Disposition": _content_disposition(filename)})


@router.post("/download-bulk")
async def download_pod_bulk(tracking_nos: list[str] = Form(...)):
    """Secilen birden fazla parca icin POD'lari tek bir ZIP olarak indirir
    (gercek GLS IE portalindaki coklu-secim + 'Proof of delivery' ozelligiyle uyumlu).
    Zip icindeki klasor duzeni icin bkz. `pod_archive_path`."""
    invalid = [t for t in tracking_nos if not _TRACKING_NO_RE.match(t)]
    if invalid:
        raise HTTPException(422, i18n.t("err.invalid_tracking_no", nos=", ".join(invalid[:5])))
    if not tracking_nos:
        raise HTTPException(400, i18n.t("err.select_at_least_one"))

    buf = BytesIO()
    errors = []
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for tn in tracking_nos:
            try:
                data, parcel, media_type, info = _fetch_pod_bytes(tn)
                # Toplu indirme musteriye gonderilen surumu verir; ic kayit
                # surumu tek tek, bilincli olarak indirilir.
                data, ext = build_document(parcel, data, media_type, info)
                arcname = pod_archive_path(parcel, ext)
                # Ayni klasorde isim carpismasi olursa (nadir) takip no zaten dosya
                # adinda oldugu icin pratikte olmaz; yine de garanti altina al.
                while arcname in used_names:
                    stem, _, ext = arcname.rpartition(".")
                    arcname = f"{stem}_2.{ext}"
                used_names.add(arcname)
                zf.writestr(arcname, data)
            except HTTPException as e:
                errors.append(f"{tn}: {e.detail}")
        if errors:
            zf.writestr("HATALAR.txt", "\n".join(errors))

    buf.seek(0)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="POD_secilenler.zip"'})


@router.get("/modal/{tracking_no}")
async def get_pod_modal(
    request: Request,
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
):
    """Ortalanmis POD yukleme ve teslimat notu modalinin icerigini dondurur."""
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found"))

    ctx = {
        "request": request,
        "p": parcel,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "_partials/pod_modal.html", ctx)


@router.post("/upload/{tracking_no}")
async def upload_parcel_pod(
    request: Request,
    tracking_no: str = PathParam(..., pattern=TRACKING_NO_PATTERN),
    file: UploadFile = File(...),
):
    """Kullanici sag detay panelinden dogrudan bu koliye ozel POD dosyasi (PDF veya Resim) yukler.

    PDF ise dogrudan arsivlenir. Resim (PNG, JPG, BMP vb.) ise otomatik olarak
    resmi POD teslimat belgesi sablonumuza gomulerek PDF olarak arsivlenir.
    """
    db = deps.get_shipments_db()
    parcel = db.get(tracking_no)
    if not parcel:
        raise HTTPException(404, i18n.t("err.parcel_not_found"))

    content = await read_limited(file, MAX_POD_UPLOAD_BYTES)
    if not content:
        raise HTTPException(400, "Dosya içeriği boş.")

    filename = (file.filename or "").lower()
    suffix = Path(filename).suffix.lstrip(".")

    # Sihirli baslik (magic header) / dosya uzantisi tespiti
    if content.startswith(b"%PDF"):
        media_type = "pdf"
    elif content.startswith(b"\x89PNG"):
        media_type = "png"
    elif content.startswith(b"\xff\xd8"):
        media_type = "jpg"
    elif content.startswith(b"BM"):
        media_type = "bmp"
    elif content.startswith(b"RIFF") and b"WEBP" in content[:16]:
        media_type = "webp"
    elif suffix in ("png", "jpg", "jpeg", "bmp", "webp", "gif"):
        media_type = suffix if suffix != "jpeg" else "jpg"
    else:
        media_type = "pdf"

    store_pod(parcel, content, media_type=media_type)
    if parcel.get("status") != "delivered":
        db.update_status(tracking_no, "delivered")

    events = _group_consecutive_events(db.events_for(tracking_no))
    updated_parcel = db.get(tracking_no)
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


