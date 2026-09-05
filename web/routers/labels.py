"""Etiket olusturma sayfasi (NL veya IE)."""
from __future__ import annotations

import html
import json

from pydantic import ValidationError

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

from web.templating import templates
from web import deps
from gls_api import providers
from gls_api.nl_client import GLSNLError
from gls_api.shipit_client import ShipITError
from gls_api.schemas import AddressIn, LabelCreateIn
from gls_api.label_payload import build_nl_payload, build_shipit_payload

router = APIRouter(prefix="/labels", tags=["labels"])

# Alici formunda onceden-doldurma (JS) icin adres defterinden aktarilan alanlar.
_CONSIGNEE_FIELDS = [
    "id", "name", "name2", "name3", "company", "address_type", "street",
    "house_number", "addition", "postal_code", "city", "country",
    "consignee_id", "contact", "phone", "mobile", "email",
]


@router.get("", response_class=HTMLResponse)
async def labels_page(request: Request):
    # Alici adresleri: canlida MSSQL (Erp_Address), mock/test'te yerel defter.
    # Kaynak gondericiyi zaten disariyor; ekstra suzgec gerekmez.
    source = deps.get_address_source()
    addresses = source.list(limit=5000)
    # Ayni ada sahip birden fazla magaza (ajans) — kullanici yanlisini secmesin
    # diye secim listesinde isaretlenir (kullanici karari: ajans = ad).
    dup_names = source.duplicate_names()
    for a in addresses:
        a["dup"] = (a.get("name") or "").strip().lower() in dup_names
    # "<" karakterini "<" olarak kaciriyoruz: adres kayitlarindaki (ornegin bir
    # isim alanindaki) "</script>" dizisi <script> etiketini erken kapatip
    # stored XSS'e yol acmasin diye.
    addresses_json = json.dumps(
        [{**{k: a.get(k, "") for k in _CONSIGNEE_FIELDS}, "dup": a.get("dup", False)}
         for a in addresses]
    ).replace("<", "\\u003c")
    ctx = {
        "request": request,
        "addresses": addresses,
        "addresses_json": addresses_json,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "labels.html", ctx)


@router.post("/create")
async def create_label(
    request: Request,
    channel: str = Form(...),
    consignee_id: int = Form(0),
    consignee_name: str = Form(...),
    consignee_name2: str = Form(""),
    consignee_name3: str = Form(""),
    consignee_company: str = Form(""),
    consignee_street: str = Form(""),
    consignee_house_number: str = Form(""),
    consignee_addition: str = Form(""),
    consignee_postal_code: str = Form(""),
    consignee_city: str = Form(""),
    consignee_country: str = Form(""),
    consignee_contact: str = Form(""),
    consignee_phone: str = Form(""),
    consignee_mobile: str = Form(""),
    consignee_email: str = Form(""),
    reference: str = Form(""),
    # Not1/Not2: GLS etiketine ek referans satiri olarak basilir (kullanici
    # karari). `reference` gibi ham gecirilir; payload katmani bos/uzunlugu yonetir.
    note1: str = Form(""),
    note2: str = Form(""),
    # Her koli KENDI agirligiyla gonderilir: formda alan tekrarlanir, koli sayisi
    # listenin uzunlugudur. Tek agirligi koli sayisina yaymak yanlis navlundu.
    weight_kg: list[float] = Form([1.0]),
    services: list[str] = Form([]),
):
    try:
        req = LabelCreateIn(
            channel=channel, consignee_id=consignee_id,
            reference=reference, weights=weight_kg, services=services,
        )
    except ValidationError as e:
        raise HTTPException(422, e.errors()[0]["msg"])
    channel = req.channel
    services = req.services
    weights = req.weights
    units = [{"weight": w} for w in weights]

    ab = deps.get_address_book()
    # Gonderici (sabit gonderen adresi) su an icin devre disi — gercek GLS hesap
    # bilgileri netlesene kadar bos birakiliyor; yapilandirilmis bir adres varsa
    # yine kullanilir, yoksa bos alanlarla devam edilir (mock modda sorun cikarmaz).
    shipper = ab.get_default_shipper() or {}

    # Alici: adres defterinden secilip onceden dolduruldu, ama kullanici burada
    # herhangi bir alani manuel duzenlemis olabilir — gonderilen degerler nihai kabul edilir
    # ve aynen adres defteri kayitlariyla ayni kurallarla (Eircode/NL posta kodu vb.) dogrulanir.
    try:
        consignee_data = AddressIn(
            name=consignee_name, name2=consignee_name2, name3=consignee_name3,
            company=consignee_company, street=consignee_street,
            house_number=consignee_house_number, addition=consignee_addition,
            postal_code=consignee_postal_code, city=consignee_city, country=consignee_country,
            contact=consignee_contact, phone=consignee_phone, mobile=consignee_mobile,
            email=consignee_email,
        )
    except ValidationError as e:
        return HTMLResponse(
            f'<div class="text-rose-600 text-sm p-4 bg-rose-50 rounded-lg">Hata: {html.escape(e.errors()[0]["msg"])}</div>',
            status_code=422,
        )
    consignee = consignee_data.model_dump()

    provider = providers.label_provider_for(channel)
    if provider is None:
        return HTMLResponse(
            '<div class="text-rose-600 text-sm p-4 bg-rose-50 rounded-lg">Hata: Etiket '
            'üretebilecek bir GLS sağlayıcısı yapılandırılmamış — .env\'de SHIPIT_* '
            'veya NL_* alanlarını doldurun.</div>',
            status_code=503,
        )
    provider_name, client = provider

    db = deps.get_shipments_db()
    consignee_label = consignee.get("company") or consignee.get("name")

    try:
        if provider_name == "nl":
            payload = build_nl_payload(shipper, consignee, reference,
                                       services=services, units=units,
                                       note1=note1, note2=note2)
            result = client.create_label(payload)
            created = result.get("units") or []
            country = (consignee.get("country") or "NL")[:2].upper()
            for index, unit in enumerate(created):
                db.add_parcel(
                    tracking_no=str(unit.get("unitNo")),
                    channel="NL",
                    reference=reference,
                    country=country,
                    consignee_name=consignee_label,
                    status="in_transit",
                    # GLS `units` dizisini gonderdigimiz sirada donuyor.
                    weight_kg=weights[index] if index < len(weights) else None,
                )
                # Etiket zaten olusturuldu/faturalandi — onay GLS'in kendi
                # Shipments/PrintShip ekraninda gorunmesi icin gerekir ama
                # ikincildir, basarisiz olsa da etiket gecerlidir.
                unit_no = unit.get("unitNo")
                if unit_no:
                    try:
                        client.confirm_label(unit_no)
                    except GLSNLError:
                        pass
            parcel_no = str(created[0].get("unitNo")) if created else ""
        else:  # ShipIT
            payload = build_shipit_payload(shipper, consignee, reference,
                                           services=services, units=units,
                                           note1=note1, note2=note2)
            result = client.create_parcels(payload)
            parcels = result.get("CreatedShipment", {}).get("ParcelData") or []
            country = (consignee.get("country") or "IE")[:2].upper()
            for index, p in enumerate(parcels):
                db.add_parcel(
                    tracking_no=str(p.get("TrackID")),
                    channel=channel,
                    reference=reference,
                    country=country,
                    consignee_name=consignee_label,
                    status="in_transit",
                    weight_kg=weights[index] if index < len(weights) else None,
                )
            parcel_no = str(parcels[0].get("TrackID")) if parcels else ""
    except (ShipITError, GLSNLError) as e:
        return HTMLResponse(
            f'<div class="text-rose-600 text-sm p-4 bg-rose-50 rounded-lg">Hata: {html.escape(str(e))}</div>',
            status_code=400,
        )

    # HTMX partial cevabi — sonuc kartini yerinde gosterir
    ctx = {
        "request": request,
        "parcel_no": parcel_no,
        "channel": channel,
        "reference": reference,
    }
    return templates.TemplateResponse(request, "_partials/label_created.html", ctx)
