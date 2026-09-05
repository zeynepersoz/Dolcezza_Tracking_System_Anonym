"""Plani GLS'e gonderir, PDF'leri yazar, parcalari yerel DB'ye isler.

Tum yan etkiler burada; `dispatch/plan.py` saf kalir. Bagimliliklar (GLS istemcisi,
DB, cikti klasoru) DISARIDAN verilir — bu katman `web/`'i import etmez.

Iki kural pahaliya ogrenildi ve burada kodlanir:

1. Istemci `strict_provider_for("NL")` ile alinir. `label_provider_for()` NL
   yapilandirilmamissa sessizce baska bir saglayiciya duser; tek etikette
   operator fark eder, 30 sevkiyatlik toplu iste felaket olur.
2. Her sevkiyat KENDI try/except'inde. Biri patlayinca kalan 29 devam eder;
   etiket geri alinamadigi icin yarim kalmis bir isi bastan calistirmak
   cift etiket demektir.
"""
from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pypdf import PdfReader, PdfWriter

from dispatch.plan import Shipment
from gls_api import config
from gls_api.label_payload import build_nl_payload
from erp import writeback
from gls_api.pipeline import safe_name_part

# Klasor adi: ISO tarih + kanal ("2026-08-14 NL label"). ISO tarih siralanabilir
# olsun diye; kanal, ileride IE tarafi acilirsa ayni gunun iki klasoru karismasin.
LABEL_DIR_SUFFIX = "NL label"

# Etiket GERI ALINAMAZ: uretilen her takip numarasi sunucu gunlugune yazilir.
# Tarayici penceresi kapaninca is sonucu kayboluyordu; `docker compose logs web`
# artik "hangi gun, hangi magaza, hangi numaralar" sorusunu tek basina yanitlar.
log = logging.getLogger("dispatch")


@dataclass
class ShipmentResult:
    store_code: str
    reference: str
    tracking_numbers: list[str] = field(default_factory=list)
    pdf_path: str = ""
    error: str = ""
    # Etiketi olusan ama GLS'e ONAYLANAMAYAN koliler. Onay ikincildir (etiket
    # gecerlidir) ama sessiz kalmamali: onaysiz sevkiyat GLS'in kendi Shipments
    # ekraninda gorunmez, operator "etiket yok" sanip ikinci kez basabilir.
    unconfirmed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class BulkResult:
    day: str
    out_dir: str
    results: list[ShipmentResult] = field(default_factory=list)

    @property
    def created(self) -> list[ShipmentResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[ShipmentResult]:
        return [r for r in self.results if not r.ok]


def label_dir(day: str, base: Path | None = None) -> Path:
    """`{OUTPUT_DIR}/labels/2026-08-14 NL label/` — kullanicinin istedigi bicim."""
    root = Path(base) if base else config.OUTPUT_DIR
    return root / "labels" / f"{day} {LABEL_DIR_SUFFIX}"


def _payload(shipment: Shipment, shipper: dict) -> dict:
    """Koli basina ayri agirlik ve `unitId` — yanitta hangi takip no hangi koli, belli olsun."""
    units = [{"unitId": box.box_code, "weight": box.weight_kg} for box in shipment.boxes]
    return build_nl_payload(
        shipper=shipper,
        # Etiketin sol alt kosesi bu alanlardan basilir (RPXSE1N.pdf ile
        # dogrulandi): "Note:" satirlari `name2` ve `reference`'in yankisidir,
        # ayri bir not alani YOKTUR (2026-08-17'de GLS ucunda dogrulandi).
        #   name1 + contact -> musteri unvani    name2 -> magaza kodu
        #   reference       -> fatura numarasi   name3 -> kullanilmiyor
        # Unvan ERP'den gelir (adres defterindekinden guncel olabiliyor) ve
        # `contact` BOS birakilir: 30 karakteri asan unvanlarin devamini oraya
        # `address_to_nl` yaziyor (bkz. split_long_name).
        consignee={**(shipment.address or {}),
                   "company": shipment.customer_name or (shipment.address or {}).get("company"),
                   "name2": shipment.store_code,
                   "contact": ""},
        reference=shipment.reference,
        package_count=shipment.box_count,
        weight_kg=shipment.total_weight,
        units=units,
    )


def _ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 4 -> '4th' ... 11/12/13 istisnalari dahil.

    Kullanicinin istedigi dosya adi bicimi: `NUNAT1N-5000000 1st box.pdf`.
    """
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def box_label_dir(day: str, store_code: str, season: str = "",
                  base: Path | None = None) -> Path:
    """`{OUTPUT_DIR}/labels/FA26/04.09.2026/NUNAT1N/` — kullanicinin istedigi duzen.

    Gunluk duz klasorun (bkz. `label_dir`) YANINA yazilir, onun YERINE degil:
    toplu indirme (ZIP) ve tek-PDF birlestirme o duz klasorden calisir ve
    kullanici o davranisin aynen kalmasini istedi. Buradaki agac ise ARSIV
    icindir — her kolinin kendi etiketi ayri bir PDF olarak durur.

    Tarih GUN.AY.YIL yazilir (kullanici istegi); sezon `season_label()`ten
    turer, ayri bir ayar alani istemez.
    """
    root = Path(base) if base else config.OUTPUT_DIR
    try:
        y, m, d = day[:10].split("-")
        pretty = f"{d}.{m}.{y}"
    except ValueError:
        pretty = day
    season = safe_name_part(season or config.season_label(), 12) or "SEASON"
    store = safe_name_part(store_code, 20) or "store"
    return root / "labels" / season / pretty / store


def _write_box_pdfs(shipment: Shipment, units: list[dict], day: str,
                    base: Path | None = None) -> list[str]:
    """Her koli icin AYRI PDF: `{MAGAZA}-{FATURA} 1st box.pdf`.

    Arsivde satir basina o kolinin KENDI etiketi insin diye (kullanici istegi,
    2026-09-01). Onceden tum satirlar sevkiyatin cok sayfali ortak PDF'ine
    isaret ediyordu ve arsivde ayni belge tekrar tekrar gorunuyordu.
    """
    directory = box_label_dir(day, shipment.store_code, base=base)
    directory.mkdir(parents=True, exist_ok=True)
    name = safe_name_part(shipment.store_code, 20) or "label"
    ref = safe_name_part(shipment.reference, 40)
    stem = f"{name}-{ref}" if ref else name

    paths: list[str] = []
    for index, unit in enumerate(units, start=1):
        label_b64 = unit.get("label")
        if not label_b64:
            paths.append("")
            continue
        path = directory / f"{stem} {_ordinal(index)} box.pdf"
        writer = PdfWriter()
        reader = PdfReader(io.BytesIO(base64.b64decode(label_b64)))
        for page in reader.pages:
            writer.add_page(page)
        with open(path, "wb") as fh:
            writer.write(fh)
        paths.append(str(path))
    return paths


def _write_pdf(directory: Path, store_code: str, reference: str, units: list[dict]) -> str:
    """Kolileri TEK cok sayfali PDF'te birlestirir.

    Gercek GLS NL API'si ust seviyede birlesik bir `labels` alani DONMUYOR —
    her koli kendi `label`'ini `units[i]` icinde tasir (2026-08-14 canli
    dogrulandi; mock sunucu bunun disinda bir de kolaylik olsun diye birlesik
    `labels` donuyordu, bu yanilticiydi). Dosya adi `{magaza}-{fatura}.pdf`
    (ornek: ANQDE1N-5000476) — yalnizca magaza kodu ayni gunde birden fazla
    sevkiyat varsa (bkz. dispatch/plan.py `split_stores`) dosyalarin
    birbirini ezmesini de onler.
    """
    directory.mkdir(parents=True, exist_ok=True)
    name = safe_name_part(store_code, 20) or "label"
    ref = safe_name_part(reference, 40)
    if ref:
        name = f"{name}-{ref}"
    path = directory / f"{name}.pdf"
    writer = PdfWriter()
    for unit in units:
        label_b64 = unit.get("label")
        if not label_b64:
            continue
        reader = PdfReader(io.BytesIO(base64.b64decode(label_b64)))
        for page in reader.pages:
            writer.add_page(page)
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


def _record(db, shipment: Shipment, units: list[dict], pdf_path: str, day: str,
            box_pdfs: list[str] | None = None) -> list[str]:
    """Her koliyi yerel takip DB'sine isler; `box_rec_id` cift etiket korumasidir."""
    by_code = {box.box_code: box for box in shipment.boxes}
    tracking: list[str] = []
    for index, unit in enumerate(units):
        tracking_no = str(unit.get("unitNo") or "")
        if not tracking_no:
            continue
        # `unitId` gonderdigimiz koli barkodu olarak geri gelir; gelmezse sira
        # korunur (GLS `units` dizisini gonderdigimiz sirada donuyor).
        box = by_code.get(str(unit.get("unitId") or ""))
        if box is None and index < len(shipment.boxes):
            box = shipment.boxes[index]

        db.add_parcel(
            tracking_no=tracking_no,
            channel="NL",
            reference=shipment.reference,
            country=(shipment.country or "NL")[:2].upper(),
            consignee_name=shipment.customer_name,
            status="created",
            store_code=shipment.store_code,
            invoice_number=shipment.invoices[0] if shipment.invoices else "",
            emc_invoices=", ".join(shipment.invoices),
            shipment_date=day,
            weight_kg=box.weight_kg if box else None,
            # Sezonu HEMEN ERP'nin kendi `UD_Season`indan yaz (kullanici
            # bildirdi 2026-09-02: "bunların sezonu niye yok") — bir sonraki
            # ERP senkronunu BEKLEMEZ: o senkron yalnizca paketleme tarihi
            # GECMISTE olan kolileri kapsar (bkz. erp/sync.py), ileri tarihli
            # etiketler gunlerce sezonsuz kalirdi.
            season=box.season if box else "",
        )
        # Kayda KOLININ KENDI etiketi baglanir; sevkiyatin ortak cok sayfali
        # PDF'i yedek kalir (koli etiketi bir sekilde yazilamadiysa). Arsivde
        # her satir kendi belgesini acsin diye (kullanici istegi, 2026-09-01).
        own = (box_pdfs[index] if box_pdfs and index < len(box_pdfs) else "") or pdf_path
        db.set_label_path(tracking_no, own, box.rec_id if box else "")
        # Takip numarasi kolinin ERP satirina da yazilir (kullanici istegi,
        # 2026-09-01). Bu, etiketlenmis kolinin toplu etiket listesine bir daha
        # DUSMEMESINI saglar: liste `UD_TrackingNumber` bos olanlari gosterir,
        # yani uygunlugun tek dogruluk kaynagi kolinin kendisi olur.
        # BASARISIZLIK AKISI DURDURMAZ — etiket zaten basildi, ERP'ye
        # yazamamak onu geri almaz; iz log'a dusuruluyor.
        if box and box.rec_id:
            if writeback.set_tracking_number(box.rec_id, tracking_no):
                log.info("ERP'ye takip no yazildi: koli %s -> %s", box.rec_id, tracking_no)
            else:
                log.warning("ERP'ye takip no YAZILAMADI (koli %s, %s) — "
                            "koli listede tekrar gorunebilir", box.rec_id, tracking_no)
        tracking.append(tracking_no)
    return tracking


def run(
    shipments: list[Shipment],
    *,
    client,
    db,
    day: str,
    shipper: dict | None = None,
    base_dir: Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> BulkResult:
    """Gonderilebilir sevkiyatlari etiketler. Engelli olanlar CAGIRAN tarafindan elenir.

    `base_dir` yalnizca KOKU degistirir (testlerde tmp_path); tarih klasoru her
    zaman uretilir — "12 agustos label/AHAAT01.pdf" duzeni kullanicinin sarti.
    """
    directory = label_dir(day, base_dir)
    result = BulkResult(day=day, out_dir=str(directory))
    total = len(shipments)
    log.info("Toplu etiket basliyor: %s, %d sevkiyat -> %s", day, total, directory)

    for index, shipment in enumerate(shipments, start=1):
        if progress:
            progress(index, total, shipment.store_code)
        item = ShipmentResult(store_code=shipment.store_code, reference=shipment.reference)
        try:
            response = client.create_label(_payload(shipment, shipper or {}))
            units = response.get("units") or []
            if not units:
                raise ValueError("GLS yanitinda etiket (units) yok")
            item.pdf_path = _write_pdf(directory, shipment.store_code, shipment.reference, units)
            # Arsiv agaci: labels/{SEZON}/{GG.AA.YYYY}/{MAGAZA}/... — ZIP ve
            # tek-PDF birlestirme YINE `directory`den calisir, degismedi.
            box_pdfs = _write_box_pdfs(shipment, units, day, base=base_dir)
            item.tracking_numbers = _record(db, shipment, units, item.pdf_path, day,
                                            box_pdfs=box_pdfs)
            # Etiket zaten olusturuldu — onay (Label/Confirm) GLS'in kendi
            # Shipments/PrintShip ekraninda gorunmesi icin gerekir ama
            # ikincildir: basarisiz olsa da sevkiyat "olusturuldu" sayilir,
            # kalan sevkiyatlar etkilenmez. Yine de KAYDEDILIR ve loglanir.
            for unit in units:
                unit_no = unit.get("unitNo")
                if not unit_no:
                    continue
                try:
                    client.confirm_label(unit_no, shipping_date=day)
                except Exception as exc:      # noqa: BLE001 — onay ikincil, etiket gecerli
                    item.unconfirmed.append(str(unit_no))
                    log.warning("Onaylanamadi %s (%s): %s",
                                unit_no, shipment.store_code, exc)
            log.info("Etiket olustu %s ref=%s koli=%d takip=%s pdf=%s",
                     shipment.store_code, shipment.reference,
                     len(item.tracking_numbers), ",".join(item.tracking_numbers),
                     item.pdf_path)
        except Exception as exc:              # noqa: BLE001 — biri patlasa da kalanlar sursun
            item.error = str(exc) or exc.__class__.__name__
            log.error("Etiket basarisiz %s ref=%s: %s",
                      shipment.store_code, shipment.reference, item.error)
        result.results.append(item)

    unconfirmed = sum(len(r.unconfirmed) for r in result.results)
    log.info("Toplu etiket bitti: %s, olusan %d, basarisiz %d, onaysiz koli %d",
             day, len(result.created), len(result.failed), unconfirmed)
    return result
