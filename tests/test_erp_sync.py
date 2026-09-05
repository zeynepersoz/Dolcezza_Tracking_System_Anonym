"""MSSQL takip kaynagi — ag baglantisi olmadan, sahte istemciyle.

Kullanicinin acik kisiti: "dbde degisiklik olmasin, orada birseyleri silmeyelim".
Bu yuzden salt-okunurlugun gercekten zorlandigi burada test edilir.
"""
from datetime import datetime

import pytest

from erp.client import ERPError, safe_identifier
from erp.sync import BOX_TABLE, fetch_boxes, fetch_season, map_row, season_codes, sync
from gls_api import config
from tracking.db import ShipmentsDB
from tracking.scheduler import Tracker


def _row(tracking_no="38120177012231", buyer="NUNAT1N", name="AHA Austria",
         country="AT", invoice="5000202", method="5th AIR SHIPMENT"):
    return {
        "UD_TrackingNumber": tracking_no,
        "UD_Buyer": buyer,
        "CustomerCountry": country,
        "UD_BCHInvoiceNumber": invoice,
        "UD_PaketlemeTarihi": datetime(2026, 7, 31, 14, 5),
        "CustomerName": name,
        "UD_GonderimSekli": method,
    }


class FakeClient:
    """Gorunum adi -> satirlar. `fetch_season` sorguyu icine gomdugu icin adi ayikliyoruz.

    `Erp_Box` anahtari taban tablo taramasini besler (sorguda `FROM Erp_Box b`).
    """

    def __init__(self, by_view: dict[str, list[dict]]):
        self.by_view = by_view

    def query(self, sql, params=()):
        for view, rows in self.by_view.items():
            if f"FROM {view} v" in sql or f"FROM {view} b" in sql:
                return rows
        return []


@pytest.fixture
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "shipments.db")


# ---------- salt okunurluk / enjeksiyon ----------

def test_safe_identifier_rejects_injection():
    with pytest.raises(ERPError):
        safe_identifier("Vision_SP26; DROP TABLE parcels")


@pytest.mark.parametrize("sql", ["DELETE FROM parcels", "UPDATE x SET y=1", " exec sp_who"])
def test_query_refuses_writes(sql):
    """Yazma ifadesi SUNUCUYA GITMEDEN reddedilmeli — baglanti bile acilmamali."""
    from erp.client import ERPClient

    with pytest.raises(ERPError):
        ERPClient(server="yok", database="yok", username="u", password="p").query(sql)


def test_excluded_countries_are_filtered_in_sql_not_in_python():
    """'Un'/RU/AU... SUNUCUDA elenmeli — 3500 satiri cekip Python'da atmak israf.

    Ulke bir DEGERDIR: sorguya gomulmeyip parametre olarak gecmeli. IE artik
    dislanmiyor (T&T v1 ile takip ediliyor); IE-kanali ulke suzgeci `map_row`da.
    """
    seen = {}

    class Spy:
        def query(self, sql, params=()):
            if "INFORMATION_SCHEMA" in sql:
                return []
            seen["sql"], seen["params"] = sql, params
            return []

    fetch_season(Spy(), "Vision_FA26_Boxed_EUROPE")
    assert "v.CustomerCountry NOT IN (%s, %s, %s, %s, %s, %s)" in seen["sql"]
    assert tuple(seen["params"]) == ("Un", "RU", "AU", "NZ", "AUS", "NZL")


def test_box_seasons_and_countries_are_parameters_not_sql_text():
    """Sezon kodu da ulke gibi bir DEGERDIR — sorgu metnine gomulmemeli."""
    seen = {}

    class Spy:
        def query(self, sql, params=()):
            seen["sql"], seen["params"] = sql, params
            return []

    fetch_boxes(Spy(), ["SP26", "FA26"])
    assert "b.UD_Season IN (%s, %s)" in seen["sql"]
    assert "ISNULL(c.CustomerCountry, 'Un') NOT IN (%s, %s, %s, %s, %s, %s)" in seen["sql"]
    # SIRA sorgudaki `%s` sirasidir: tasiyicilar, FedEx tarih tabani,
    # sezonlar, ulkeler.
    assert tuple(seen["params"]) == (
        "GLS-NL", "GLS-IE", "FEDEX", config.FEDEX_SYNC_FROM_DATE,
        "SP26", "FA26", "Un", "RU", "AU", "NZ", "AUS", "NZL")


def test_every_row_must_pass_the_carrier_gate():
    """Kullanici karari (2026-09-03): "tum sorgular tasima firmasina gore".

    2026-09-02'de burada "takip no 12 hane" diye bir TAHMIN dali vardi ve
    felaketle sonuclandi: sezon/ulke/tarih suzgeclerinin hicbirine tabi
    olmadigi icin Erp_Box'in tum gecmisini supurdu (287 koli bir gecede,
    285'i "delivered", tek saatte 207 WhatsApp mesaji). Tahmin gitti, ERP'nin
    kendi alani geldi.
    """
    seen = {}

    class Spy:
        def query(self, sql, params=()):
            seen["sql"] = sql
            return []

    fetch_boxes(Spy(), ["SP26"])
    sql = seen["sql"]
    # tasiyici kapisi: uc kanal disinda hicbir satir gecemez
    assert "UD_TasimaFirmasi, '')))) IN (%s, %s, %s)" in sql
    # FedEx sezon/ulke disinda ama TARIH tabanina bagli
    assert "b.UD_PaketlemeTarihi >= %s" in sql
    # tahmin dali tamamen gitti
    assert "LEN(LTRIM(RTRIM(b.UD_TrackingNumber))) = 12" not in sql


def test_season_view_uses_the_carrier_when_it_has_the_column():
    """FA26 gorunumunde alan VAR (olculdu: GLS-NL 1515 / GLS-IE 69 / FEDEX 3),
    SP26'da YOK — sorgu buna gore kurulur, eski sezon davranisi bozulmaz."""
    seen = {}

    class Spy:
        def __init__(self, columns):
            self.columns = columns

        def query(self, sql, params=()):
            if "INFORMATION_SCHEMA" in sql:
                return [{"COLUMN_NAME": c} for c in self.columns]
            seen["sql"], seen["params"] = sql, params
            return []

    fetch_season(Spy(["UD_TrackingNumber", "UD_TasimaFirmasi"]), "Vision_FA26_Boxed_EUROPE")
    assert "v.UD_TasimaFirmasi AS UD_TasimaFirmasi" in seen["sql"]
    assert tuple(seen["params"])[-3:] == ("GLS-NL", "GLS-IE", "FEDEX")

    seen.clear()
    fetch_season(Spy(["UD_TrackingNumber"]), "Vision_SP26_Boxed_EUROPE")
    assert "NULL AS UD_TasimaFirmasi" in seen["sql"]
    assert "GLS-NL" not in str(seen["params"])


def test_gls_ie_numbers_are_not_mistaken_for_fedex():
    """`2158…` GLS IE serisidir; 12 hane oldugu icin FedEx sanilmisti."""
    row = _row(tracking_no="215880073733", country="IE")
    row["UD_TasimaFirmasi"] = ""
    assert map_row(row, "v")["channel"] == "IE"


def test_the_carrier_field_decides_the_channel():
    """GLS-NL / GLS-IE / FEDEX -> kanal. Onek tahminine DUSULMEZ.

    Ulke her satirda kanala UYGUN secildi: IE kanalinda ayrica
    `IE_CHANNEL_COUNTRIES` suzgeci var (Irlanda disi teslimatlar kapsam disi),
    bu test onu degil TASIYICI->KANAL eslemesini olcuyor.
    """
    for carrier, channel, country in (("GLS-NL", "NL", "DE"),
                                      ("GLS-IE", "IE", "IE"),
                                      ("FEDEX", "FEDEX", "CA")):
        # Takip numarasi BILEREK kanala uymuyor (hepsinde ayni FedEx numarasi):
        # kanali belirleyen sey onek degil, tasiyici alani olmali.
        row = _row(tracking_no="876634105532", country=country)
        row["UD_TasimaFirmasi"] = carrier
        assert map_row(row, "v")["channel"] == channel, carrier


def test_a_blank_carrier_falls_back_to_the_prefix():
    """Tasiyicisi olmayan sezon gorunumleri (SP26) icin son care."""
    row = _row(tracking_no="38120177012231", country="DE")
    row["UD_TasimaFirmasi"] = ""
    assert map_row(row, "v")["channel"] == "NL"
