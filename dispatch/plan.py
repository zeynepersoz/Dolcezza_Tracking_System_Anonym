"""Koli listesini sevkiyat planina cevirir — SAF, hicbir I/O yok.

Kullanicinin kurali burada kodlanir:

    "magaza kodu ayni olan ama farkli faturada olanlar adresleri ayniysa tek
     seferde girilsin referansa 2 fatura numarasi birden yazilsin ama adreslerin
     ayni olup olmadigini denk geldiginde sistem sorsun farkli adresse eger
     fatura numarasini secip adresleri elle girebilsin"

Adres ANAHTARI magaza kodudur. Bu yuzden "adresler ayni mi" sorusu pratikte
"adres defterinde bu kod icin kac kayit var" sorusudur:

    0 kayit  -> no_address        operator elle girer, defter'e yazilir
    1 kayit  -> ayni adres        birden cok fatura varsa BIRLESTIRME SORUSU
    >1 kayit -> ambiguous_address operator hangisi oldugunu secer/duzeltir
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

# Irlanda/ShipIT magaza kodlari `P` ile BITER (JUVGB01P, QBGGB1P-001) —
# kullanici karariyla toplu etiket kapsami disi. Kalan her sey GLS NL'dir.
#
# DIKKAT — kural OLUMSUZ yazilir (dislama), OLUMLU bir kalip DEGIL. Onceden
# `^[A-Z]{3,8}\d[N]$` vardi ve GERCEK magazalari eliyordu: kod bicimi sanildigi
# kadar duzenli degil (olculdu 2026-09-01, GLS NL ile fiilen gonderi yapilmis
# 536 magaza uzerinde — kalip bunlarin 17'sini reddediyordu):
#   T81FR1N  onek rakam iceriyor      ZNES12N / ZBDE10N  onek 2 karakter
#   OLIESCN  son harften once rakam yok   NIEPL01 / ONCBE01  `N` ile bitmiyor
# Kullanici bildirdi: PNHFR1N ve T81FR1N yazdirilacaklar listesinde cikmiyordu.
# "P ile biter" kurali ayni 536 magazanin HICBIRINI elemez, ERP'de ise 96 kodu
# (86 GB + 10 ulkesi bos GB magazasi) dogru sekilde disarida birakir.
IE_STORE_CODE_RE = re.compile(r"P(-\d+)?$")

READY = "ready"
NO_ADDRESS = "no_address"
AMBIGUOUS_ADDRESS = "ambiguous_address"
MERGE_QUESTION = "merge_question"
MISSING_WEIGHT = "missing_weight"

# Gonderilebilir durumlar. `merge_question` de gonderilebilir: soru bilgilendirme
# amaclidir, varsayilani birlestirmektir; operator isterse `split_stores` ile ayirir.
SENDABLE = (READY, MERGE_QUESTION)


@dataclass(frozen=True)
class Box:
    rec_id: str
    box_code: str
    store_code: str
    invoice: str
    weight_kg: float
    dimensions: str = ""
    customer_name: str = ""
    country: str = ""
    season: str = ""

    @classmethod
    def from_row(cls, row: dict) -> "Box":
        return cls(
            rec_id=str(row.get("rec_id", "")),
            box_code=str(row.get("box_code", "")),
            store_code=str(row.get("store_code", "")).upper(),
            invoice=str(row.get("invoice", "")),
            weight_kg=float(row.get("weight_kg") or 0.0),
            dimensions=str(row.get("dimensions", "")),
            customer_name=str(row.get("customer_name", "")),
            country=str(row.get("country", "")),
            season=str(row.get("season", "")),
        )


@dataclass
class Shipment:
    store_code: str
    invoices: list[str]
    boxes: list[Box]
    address: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    state: str = READY

    @property
    def reference(self) -> str:
        """Etiketteki `Ref.` alani — birlestirilmisse iki fatura birden."""
        return "+".join(self.invoices)

    @property
    def total_weight(self) -> float:
        return round(sum(b.weight_kg for b in self.boxes), 2)

    @property
    def box_count(self) -> int:
        return len(self.boxes)

    @property
    def customer_name(self) -> str:
        for box in self.boxes:
            if box.customer_name:
                return box.customer_name
        return self.store_code

    @property
    def country(self) -> str:
        if self.address and self.address.get("country"):
            return str(self.address["country"])
        for box in self.boxes:
            if box.country:
                return box.country
        return ""

    @property
    def sendable(self) -> bool:
        return self.state in SENDABLE

    @property
    def merged(self) -> bool:
        return len(self.invoices) > 1

    @property
    def invoice_lines(self) -> list[dict]:
        """Siparis (fatura) bazinda kirilim — detay panelinde gosterilir.

        30 kolilik bir sevkiyatta operator "hangi fatura kac koli/kg, agirligi
        eksik olan hangi siparis" sorusunu tek etikete basmadan gorebilsin diye.
        Gorulme sirasi referansla ayni tutulur; faturasiz koliler (invoice="")
        sona duser.
        """
        groups: dict[str, list[Box]] = {}
        for box in self.boxes:
            groups.setdefault(box.invoice, []).append(box)
        order = list(self.invoices)
        for inv in groups:
            if inv not in order:
                order.append(inv)
        lines: list[dict] = []
        for inv in order:
            boxes = groups.get(inv, [])
            if not boxes:
                continue
            lines.append({
                "invoice": inv,
                "box_count": len(boxes),
                "total_weight": round(sum(b.weight_kg for b in boxes), 2),
                "missing_weight": any(b.weight_kg <= 0 for b in boxes),
            })
        return lines


@dataclass
class Skipped:
    store_code: str
    reason: str            # not_gls_code | already_labeled
    box_count: int
    invoices: list[str]


@dataclass
class PlanResult:
    shipments: list[Shipment]
    skipped: list[Skipped]

    @property
    def sendable(self) -> list[Shipment]:
        return [s for s in self.shipments if s.sendable]

    @property
    def blocked(self) -> list[Shipment]:
        return [s for s in self.shipments if not s.sendable]


def is_gls_store_code(code: str) -> bool:
    """GLS NL kanalina ait mi. Irlanda/ShipIT (`...P`) disinda her sey oyledir."""
    code = (code or "").strip().upper()
    return bool(code) and not IE_STORE_CODE_RE.search(code)


def _group(boxes: Iterable[Box]) -> dict[str, list[Box]]:
    grouped: dict[str, list[Box]] = {}
    for box in boxes:
        grouped.setdefault(box.store_code, []).append(box)
    return grouped


def _invoices(boxes: Iterable[Box]) -> list[str]:
    """Gorulme sirasini korur — referans "5000402+5000419" bicimini tutturur."""
    out: list[str] = []
    for box in boxes:
        if box.invoice and box.invoice not in out:
            out.append(box.invoice)
    return out


def _classify(shipment: Shipment) -> str:
    if any(b.weight_kg <= 0 for b in shipment.boxes):
        return MISSING_WEIGHT
    if not shipment.candidates:
        return NO_ADDRESS
    if len(shipment.candidates) > 1:
        return AMBIGUOUS_ADDRESS
    if len(shipment.invoices) > 1:
        return MERGE_QUESTION
    return READY


def build_plan(
    rows: Iterable[dict],
    address_lookup: Callable[[str], list[dict]],
    *,
    split_stores: Iterable[str] = (),
    already_labeled: Iterable[str] = (),
) -> PlanResult:
    """Ham koli satirlarindan sevkiyat plani kurar.

    `address_lookup(store_code) -> list[dict]` adres defterine bakar.
    `split_stores`  operator birlestirmeyi kapattiysa: fatura basina ayri sevkiyat.
    `already_labeled` daha once etiketlenmis koli `rec_id`leri — CIFT ETIKET KORUMASI.
    """
    split = {s.strip().upper() for s in split_stores}
    labeled = {str(r) for r in already_labeled}

    shipments: list[Shipment] = []
    skipped: list[Skipped] = []

    for store_code, boxes in _group(Box.from_row(r) for r in rows).items():
        if not is_gls_store_code(store_code):
            skipped.append(Skipped(store_code, "not_gls_code", len(boxes), _invoices(boxes)))
            continue

        pending = [b for b in boxes if b.rec_id not in labeled]
        if not pending:
            skipped.append(Skipped(store_code, "already_labeled", len(boxes), _invoices(boxes)))
            continue

        candidates = list(address_lookup(store_code) or [])
        address = candidates[0] if len(candidates) == 1 else None

        # Birlestirme kapatildiysa her fatura kendi sevkiyati olur; adres yine ayni.
        if store_code in split:
            groups = [[b for b in pending if b.invoice == inv] for inv in _invoices(pending)]
        else:
            groups = [pending]

        for group in groups:
            shipment = Shipment(
                store_code=store_code,
                invoices=_invoices(group),
                boxes=group,
                address=address,
                candidates=candidates,
            )
            shipment.state = _classify(shipment)
            shipments.append(shipment)

    shipments.sort(key=lambda s: (not s.sendable, s.store_code))
    skipped.sort(key=lambda s: s.store_code)
    return PlanResult(shipments=shipments, skipped=skipped)
