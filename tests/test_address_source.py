# -*- coding: utf-8 -*-
"""Alici adres kaynagi: prod=MSSQL (Erp_Address) / test=yerel defter.

`fetch_addresses` sorguyu MSSQL'e atmaz; sahte bir client ile satir eslemesi
(`_map_row`) dogrulanir. Gercek sorguyu canli ortam kapsar.
"""
from erp.addresses import _map_row, _text, fetch_addresses, split_house_number
from web import address_source


class _FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        return self.rows


# ------------------------------------------------------------------ _map_row
def test_free_text_lines_land_in_the_nearest_structured_fields():
    row = {"RecId": 42, "AddressCode": "NUNAT1N", "Explanation": "Ahatler",
           "Line1": "Hauptplatz 1", "Line2": "Wien", "Line3": "1010 AT",
           "PostalCode": None, "Phone": "+43 1 234", "EMail": "a@b.at",
           "CountryCode": "at"}

    out = _map_row(row)

    assert out["id"] == 42
    assert out["name"] == "Ahatler"
    assert out["name2"] == "NUNAT1N"
    assert out["consignee_id"] == "NUNAT1N"
    assert out["street"] == "Hauptplatz"
    assert out["house_number"] == "1"
    assert out["city"] == "Wien"
    assert out["region"] == "1010 AT"
    assert out["country"] == "AT"
    assert out["postal_code"] == ""       # ERP'de yok, kullanici formda girer


def test_split_house_number_cuts_the_trailing_number():
    """NL/DE stili: numara sokak adinin SONUNDA."""
    assert split_house_number("Kerkstraat 15") == ("Kerkstraat", "15")
    assert split_house_number("Hoofdstraat 22A") == ("Hoofdstraat", "22A")
    assert split_house_number("Bahnhofstr. 4-6") == ("Bahnhofstr.", "4-6")
    assert split_house_number("Am Goldenen Reiter 12") == ("Am Goldenen Reiter", "12")


def test_split_house_number_cuts_the_leading_number():
    """FR/BE stili: numara sokak adinin BASINDA (canli veriden ornekler)."""
    assert split_house_number("15 Rue de Domfront") == ("Rue de Domfront", "15")
    assert split_house_number("5 Rue Des Barons De Fleckenstein") == (
        "Rue Des Barons De Fleckenstein", "5")
    assert split_house_number("6 Place St Martin") == ("Place St Martin", "6")
    # "Bp 152," (Boite Postale) SONA numara gibi gorunse de virgulle bitiyor,
    # trailing regex bunu ATLAR — bastaki gercek kapi numarasi "91" alinir.
    assert split_house_number("91 Rue Jean Chatel Bp 152,") == (
        "Rue Jean Chatel Bp 152,", "91")


def test_split_house_number_prefers_trailing_over_a_dutch_ordinal_prefix():
    """'1e ...' Hollanda sokak SIRA SAYISIDIR, kapi numarasi degil — bastaki
    denenmeden ONCE sondaki numara yakalanmali, yoksa '1e' yanlislikla No
    sanilir ve gercek numara ('3') sokak adinda mahsur kalir."""
    assert split_house_number("1e Weteringplantsoen 3") == ("1e Weteringplantsoen", "3")


def test_split_house_number_rescues_a_room_number_at_the_end():
    """Canli olay 2026-09-03: bir musteri `Line1` =
    'Musterstrasse 42 Lagerhaus 2.OG Raum 2201/03'. Trailing regex sondaki oda
    no'sunu ('2201/03') kapi no sanip aliyordu; GLS NL 'houseNo 5 karakteri
    asamaz' (V009) ile sevkiyati KOMPLE reddediyordu. Sondaki 'numara' 5 haneyi
    asinca sokak adindan hemen sonraki gercek numara ('42') kurtarilir, kalan
    tanim metni sokak alanina geri katilir."""
    assert split_house_number("Musterstrasse 42 Lagerhaus 2.OG Raum 2201/03") == (
        "Musterstrasse Lagerhaus 2.OG Raum 2201/03", "42")
    # Sondaki numara 5 haneye siginca dokunulmaz (mevcut davranis korunur).
    assert split_house_number("Rue 4 Septembre 12") == ("Rue 4 Septembre", "12")


def test_split_house_number_leaves_a_number_free_line_untouched():
    assert split_house_number("Hauptplatz") == ("Hauptplatz", "")
    assert split_house_number("") == ("", "")
    assert split_house_number(None) == ("", "")


def test_literal_null_string_is_treated_as_empty():
    assert _text("NULL") == ""
    assert _text(None) == ""
    assert _text("  Ahatler  ") == "Ahatler"


def test_fetch_addresses_passes_the_account_id_as_a_string():
    client = _FakeClient([{"RecId": 1, "AddressCode": "X", "Explanation": "Y"}])

    result = fetch_addresses(client, account_id=9999)

    assert result[0]["name"] == "Y"
    assert client.calls[0][1] == ("9999",)


# -------------------------------------------------------- kaynak secimi + uyari
def test_mock_mode_uses_the_local_book(monkeypatch):
    monkeypatch.setattr(address_source.config, "IS_MOCK", True)

    assert isinstance(address_source.build_address_source(),
                      address_source.LocalAddressSource)


def test_live_with_address_source_erp_uses_the_erp_source(monkeypatch):
    monkeypatch.setattr(address_source.config, "IS_MOCK", False)
    monkeypatch.setattr(address_source.config, "mssql_configured", lambda: True)
    monkeypatch.setattr(address_source.config, "get",
                        lambda name: "erp" if name == "ADDRESS_SOURCE" else "")

    assert isinstance(address_source.build_address_source(),
                      address_source.ERPAddressSource)


def test_live_defaults_to_the_erp_source(monkeypatch):
    monkeypatch.setattr(address_source.config, "IS_MOCK", False)
    monkeypatch.setattr(address_source.config, "mssql_configured", lambda: True)
    monkeypatch.setattr(address_source.config, "get",
                        lambda name, default="": "erp" if name == "ADDRESS_SOURCE" else default)

    assert isinstance(address_source.build_address_source(),
                      address_source.ERPAddressSource)


def test_live_without_mssql_falls_back_to_local_book(monkeypatch):
    monkeypatch.setattr(address_source.config, "IS_MOCK", False)
    monkeypatch.setattr(address_source.config, "mssql_configured", lambda: False)

    assert isinstance(address_source.build_address_source(),
                      address_source.LocalAddressSource)


def test_a_name_with_two_stores_is_flagged_as_duplicate():
    rows = [{"name": "Ajans A"}, {"name": "Ajans A"}, {"name": "Tekil"}]

    dups = address_source._duplicate_names(rows)

    assert dups == {"ajans a"}


# ------------------------------------------------- irtibat alani (contact)
# Kullanici istegi (2026-09-01): "name 1 de yazani contacta da yaz ki bos
# kalmasin". ERP'de ayri bir irtibat kisisi alani yok.

def test_contact_is_filled_from_the_store_name():
    out = _map_row({"RecId": 1, "AddressCode": "YNOFR1N",
                    "Explanation": "LA BOUTIQUE", "CountryCode": "FR"})

    assert out["name"] == "LA BOUTIQUE"
    assert out["contact"] == "LA BOUTIQUE"


def test_contact_stays_empty_when_the_store_has_no_name():
    """Adsiz kayitlar da donebilir (bkz. fetch_addresses) — uydurma yapilmaz."""
    out = _map_row({"RecId": 2, "AddressCode": "KKKFR1N", "Explanation": ""})

    assert out["contact"] == ""


def test_a_long_name_still_overflows_into_contact_on_the_label():
    """GLS yukunde davranis DEGISMEMELI: 30 karakteri asan unvanda `contact`
    unvanin DEVAMINI tasir (bkz. label_payload.split_long_name), artik dolu
    gelen `contact` bunu bozmamali."""
    from gls_api.label_payload import address_to_nl

    long_name = "Lili Mode-Boutique am Goldenen Reiter"
    payload = address_to_nl(_map_row({"RecId": 3, "AddressCode": "YVYDE1N",
                                      "Explanation": long_name, "CountryCode": "DE"}))

    assert payload["name1"] == "Lili Mode-Boutique am Goldenen"   # 30 karakter
    assert payload["contact"] == "Reiter"                          # tasan kisim


def test_a_short_name_is_echoed_into_contact_on_the_label():
    from gls_api.label_payload import address_to_nl

    payload = address_to_nl(_map_row({"RecId": 4, "AddressCode": "ULQFR1N",
                                      "Explanation": "HYDRE 23", "CountryCode": "FR"}))

    assert payload["name1"] == "HYDRE 23"
    assert payload["contact"] == "HYDRE 23"
