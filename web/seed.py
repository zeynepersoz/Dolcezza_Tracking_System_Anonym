"""Demo verisi tohumlama - ilk aciliste bos DB'ye ornek kayitlar ekler.

Bunlar 'fake' takip numaralaridir; mock server bunlari 'not found' dondugu icin
otomatik tarayici bunlari degistirmez. Boylece dashboard ve tablo hep dolu gorunur.
Her satir tam anlamiyla doldurulur (fatura, sales rep, kg, cost, tarihler) — GUI'de
her detay gorunur.
"""
from __future__ import annotations

from datetime import date, timedelta

from address_book.db import AddressBook
from tracking.db import ShipmentsDB


DEMO_ADDRESSES = [
    dict(name="Merkez Depo", company="Bizim Sirket A.S.", street="Ornek Cad. 12",
         postal_code="34000", city="Istanbul", country="TR",
         contact="Zeynep", phone="+90 212 000 0000", email="depo@bizimsirket.com"),
    dict(name="Amsterdam Store", company="Retailer NL BV", street="Damrak 45",
         postal_code="1012 LP", city="Amsterdam", country="NL",
         contact="Jan de Vries", email="orders@retailernl.com"),
    dict(name="Rotterdam Outlet", company="Fashion NL", street="Coolsingel 100",
         postal_code="3011 AG", city="Rotterdam", country="NL",
         email="rotterdam@fashion.nl"),
    dict(name="Dublin Flagship", company="Retail IE Ltd", street="Grafton St 22",
         postal_code="D02 XY45", city="Dublin", country="IE",
         email="dublin@retail.ie"),
    dict(name="Cork Warehouse", company="Retail IE Ltd", street="Patrick St 55",
         postal_code="T12 R6E1", city="Cork", country="IE",
         email="cork@retail.ie"),
    dict(name="Salzburg Boutique", company="Alpen Fashion GmbH", street="Getreidegasse 9",
         postal_code="AT 5020", city="Salzburg", country="AT",
         contact="Egon Gassner", email="salzburg@alpen.at"),
    dict(name="Berlin Store", company="Mode Berlin GmbH", street="Kurfurstendamm 200",
         postal_code="10719", city="Berlin", country="DE",
         email="berlin@modeberlin.de"),
]


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


# Zengin demo — mock server "not found" donen sahte takip numaralari.
# Her alan doldurulur ki UI'de her hucre gorunur.
DEMO_PARCELS = [
    # (tn, ch, ref,               country,   consignee,          status,           invoice, store,    zip,        sales_rep,           method,                ship_d, deliv_d, kg,   cost)
    ("70000000001", "NL", "1038397 / LINSE01", "SE", "Linkoping Store",  "delivered",       "1038397", "LINSE01", "58175",   "Egon Gassner",     "10th Air Shipment",       12, 2,     2.5, 22.50),
    ("70000000002", "NL", "1038466 / MODSE01", "SE", "Malmo Outlet",     "out_for_delivery","1038466", "MODSE01", "21120",   "Egon Gassner",     "10th Air Shipment",       11, None,  3.2, 24.14),
    ("70000000003", "NL", "1038424 / RYDSE01", "SE", "Ryd Store",        "in_transit",      "1038424", "RYDSE01", "36252",   "Sieglinde Gassner","1st Tricotto Shipment",    4, None,  4.1, 25.22),
    ("70000000004", "NL", "1036616 / FRIDE01", "DE", "Friedberg Outlet", "in_transit",      "1036616", "FRIDE01", "61169",   "Nadja Etzold",     "1st Truck Shipment",       3, None,  5.0, 26.30),
    ("70000000005", "IE", "1040057 / NYNIE1N", "IE", "Dublin Flagship",  "delivered",       "1040057", "NYNIE1N", "D02 XY45","Deborah Soave",    "1st Truck Shipment",       8, 3,     3.5, 24.70),
    ("70000000006", "IE", "1040056 / ZVYIE1N", "IE", "Cork Warehouse",   "in_transit",      "1040056", "ZVYIE1N", "T12 R6E1","Deborah Soave",    "1st Truck Shipment",       3, None,  4.8, 25.86),
    ("70000000007", "IE", "1040060 / GALIE1S", "IE", "Galway Store",     "exception",       "1040060", "GALIE1S", "H91 XN2N","Deborah Soave",    "1st Truck Shipment",       5, None,  2.9, 24.02),
    ("70000000008", "NL", "1036554 / HARDE02", "DE", "Hamburg Store",    "exception",       "1036554", "HARDE02", "20095",   "Nadja Etzold",     "1st Truck Shipment",       6, None,  3.7, 25.06),
    ("70000000009", "IE", "1040058 / PBEIE1N", "IE", "Cork Warehouse",   "out_for_delivery","1040058", "PBEIE1N", "T12 R6E1","Deborah Soave",    "10th Air Shipment",       10, None,  3.1, 24.02),
    ("70000000010", "NL", "1038454 / SIGSE01", "SE", "Sigtuna Store",    "delivered",       "1038454", "SIGSE01", "19335",   "Egon Gassner",     "10th Air Shipment",       15, 5,     4.0, 25.60),
    ("70000000011", "NL", "1036999 / SALAT01", "AT", "Salzburg Boutique","delivered",       "1036999", "SALAT01", "5020",    "Sieglinde Gassner","1st Tricotto Shipment",   20, 8,     2.8, 22.86),
    ("70000000012", "NL", "1037010 / VIEAT02", "AT", "Vienna Store",     "delivered",       "1037010", "VIEAT02", "1010",    "Sieglinde Gassner","1st Tricotto Shipment",   22, 10,    3.4, 24.38),
    ("70000000013", "NL", "1037155 / BAREB01", "ES", "Barcelona Outlet", "delivered",       "1037155", "BAREB01", "08002",   "Egon Gassner",     "10th Air Shipment",       25, 12,    4.2, 25.44),
    ("70000000014", "NL", "1037155 / MADES01", "ES", "Madrid Store",     "in_transit",      "1037155", "MADES01", "28013",   "Egon Gassner",     "10th Air Shipment",        3, None,  3.8, 24.86),
    ("70000000015", "IE", "1040070 / LIMIE1E", "IE", "Limerick Store",   "delivered",       "1040070", "LIMIE1E", "V94 X5W7","Deborah Soave",    "1st Truck Shipment",      14, 6,     2.6, 22.62),
]


def seed_if_empty():
    ab = AddressBook()
    if len(ab.list()) == 0:
        for a in DEMO_ADDRESSES:
            ab.add(**a)

    db = ShipmentsDB()
    if db.total() == 0:
        for row in DEMO_PARCELS:
            tn, ch, ref, cc, cn, st, inv, sc, zc, sr, meth, ship_d, deliv_d, kg, cost = row
            db.add_parcel(
                tracking_no=tn, channel=ch, reference=ref, country=cc,
                consignee_name=cn, status=st,
                invoice_number=inv, store_code=sc, zip_code=zc,
                sales_rep=sr, shipment_method=meth,
                shipment_date=_d(ship_d),
                delivered_date=_d(deliv_d) if deliv_d is not None else "",
                weight_kg=kg,
                freight_cost=cost,
            )
