# -*- coding: utf-8 -*-
"""Kolinin icerigi: sorgu guvenligi + beden matrisi. Ag baglantisi yok."""
from decimal import Decimal

import pytest

from erp.client import ERPError
from erp.contents import (CHUNK, build_matrix, fetch_box_code, fetch_box_codes,
                          fetch_contents, fetch_many, total_quantities, views_for)
from gls_api import config


class Spy:
    """Her cagriyi kaydeder; `rows_by_view` bos donen gorunumu taklit eder."""

    def __init__(self, rows_by_view=None):
        self.rows_by_view = rows_by_view or {}
        self.calls = []

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        for view, rows in self.rows_by_view.items():
            if f"FROM {view} " in sql:
                return list(rows)
        return []


def _row(**over):
    row = {"Barcode": "8682500093675", "qty": Decimal("5.00000000"),
           "BCH_UPC": "8682500093675", "StyleNumber": "76604",
           "StyleColor": "A/S", "StyleSize": "M"}
    row.update(over)
    return row


@pytest.fixture
def views(monkeypatch):
    monkeypatch.setattr(config, "season_views",
                        lambda: ["Vision_FA26_Boxed_EUROPE",
                                 "Vision_SP26_Boxed_EUROPE"])


# ------------------------------------------------------------------ sorgu

def test_the_query_never_takes_a_row_lock(views):
    """`WITH (NOLOCK)` SART — bu gorunumler ilk denemede deadlock (1205) verdi.

    Panelin salt okunur bir merakı yuzunden ERP'nin yazma islemleri beklememeli.
    """
    spy = Spy()
    fetch_contents(spy, "38120177016222")

    assert "WITH (NOLOCK)" in spy.calls[0][0]


def test_the_tracking_number_is_a_parameter_not_sql_text(views):
    """Takip no kullanici girdisidir; sorgu metnine gomulemez."""
    spy = Spy()
    fetch_contents(spy, "38120177016222")

    sql, params = spy.calls[0]
    assert "38120177016222" not in sql
    assert params == ("38120177016222",)


def test_a_hostile_view_name_never_reaches_the_server(monkeypatch):
    """Gorunum adi T-SQL'de parametrelestirilemez -> `safe_identifier` suzer."""
    monkeypatch.setattr(config, "season_views", lambda: ["x; DROP TABLE parcels"])
    spy = Spy()

    with pytest.raises(ERPError):
        fetch_contents(spy, "38120177016222")

    assert spy.calls == []


# ------------------------------------------------------- gorunum secimi

def test_the_parcels_own_season_is_tried_first(views):
    """SP26 parcasi icin once SP26 gorunumu sorulmali.

    Sirasiz denemek her SP26 kolisinde bosa bir MSSQL gidis-gelisi demekti.
    """
    spy = Spy()
    fetch_contents(spy, "38120177016222", season="SP26")

    assert "Vision_SP26_Boxed_EUROPE" in spy.calls[0][0]


def test_the_other_view_is_tried_when_the_first_is_empty(views):
    """Kapsama tam degil: bir koli digerinin gorunumunde cikabiliyor."""
    spy = Spy({"Vision_SP26_Boxed_EUROPE": [_row()]})

    rows = fetch_contents(spy, "38120177016222", season="FA26")

    assert len(spy.calls) == 2
    assert len(rows) == 1


def test_a_box_with_no_record_anywhere_returns_nothing(views):
    """FA26'nin 52 kolisi yalnizca `Erp_Box`'ta; gorunumlerde hic yok."""
    assert fetch_contents(Spy(), "38120177016222") == []


def test_the_box_barcode_comes_from_the_base_table(views):
    """`BoxCode` sezon gorunumlerinde yok — taban tablo `Erp_Box`'ta.

    Ayni kural: kilit alinmaz, takip no parametrelenir.
    """
    spy = Spy({"Erp_Box": [{"BoxCode": "8682500093454"}]})

    assert fetch_box_code(spy, "38120177016222") == "8682500093454"
    sql, params = spy.calls[0]
    assert "WITH (NOLOCK)" in sql
    assert params == ("38120177016222",)


def test_a_box_without_a_barcode_is_not_an_error(views):
    """Barkod bulunamazsa icerik tablosu yine cizilmeli."""
    assert fetch_box_code(Spy(), "38120177016222") == ""


def test_an_unknown_season_still_asks_every_view(views):
    assert views_for("") == config.season_views()
    assert views_for("ZZ99") == config.season_views()


# ---------------------------------------------------------- deger cevrimi

def test_quantities_come_back_as_whole_numbers(views):
    """`qty` MSSQL'den `Decimal('5.00000000')` geliyor; ekranda "5" yazmali."""
    spy = Spy({"Vision_FA26_Boxed_EUROPE": [_row(qty=Decimal("5.00000000"))]})

    assert fetch_contents(spy, "38120177016222")[0]["qty"] == 5


def test_a_slash_in_a_colour_code_survives(views):
    """`clean_cell` KULLANILMAZ: `/`yi siliyor ve "A/S" -> "AS" oluyordu."""
    spy = Spy({"Vision_FA26_Boxed_EUROPE": [_row(StyleColor="A/S", StyleSize="O/S")]})

    row = fetch_contents(spy, "38120177016222")[0]

    assert row["color"] == "A/S"
    assert row["size"] == "O/S"


# ------------------------------------------------------------ beden matrisi

def _line(style="76604", color="A/S", size="M", qty=1, barcode="868"):
    return {"style": style, "color": color, "size": size, "qty": qty,
            "barcode": barcode}


def test_the_size_columns_are_always_xs_to_xxl():
    """Kullanici cetvelin her kolide AYNI olmasini istedi.

    Alfabetik sira "L < M < S < XL" verirdi; sira `BASE_SIZES`ten gelir.
    """
    matrix = build_matrix([_line(size=s) for s in ("XL", "S")])

    assert matrix["sizes"] == ["XS", "S", "M", "L", "XL", "XXL"]


def test_a_row_the_erp_never_named_still_shows_its_barcode():
    """Canlida 38120177012637'nin 4 barkodunda model/renk/beden BOS geliyordu.

    Hepsi tek bir ADSIZ satirda toplanip "4 adet" diyordu; kullanici o adedin
    nereden geldigini goremedi ("bilgisi olmayan ama 4 tane adedi olan bir sey").
    Barkod hic olmazsa depoda ve ERP'de aranabilir.
    """
    matrix = build_matrix([
        _line(style="", color="", size="", qty=1, barcode="8682506071948"),
        _line(style="", color="", size="", qty=1, barcode="8682506071955"),
    ])

    assert [g["style"] for g in matrix["groups"]] == ["8682506071948",
                                                      "8682506071955"]
    assert matrix["models"] == 2


def test_an_unknown_size_goes_to_the_end():
    """Yeni bir beden kodu tabloyu bozmamali, cetvelin sonuna alinmali."""
    matrix = build_matrix([_line(size="M"), _line(size="4XL")])

    assert matrix["sizes"][-1] == "4XL"


def test_rows_of_the_same_style_and_colour_become_one_line():
    """İş biriminin ekrani model+renk basina TEK satir gosteriyor."""
    matrix = build_matrix([
        _line(size="M", qty=2, barcode="111"),
        _line(size="L", qty=3, barcode="222"),
        _line(style="76624", size="M", qty=6, barcode="333"),
    ])

    assert len(matrix["groups"]) == 2
    first = matrix["groups"][0]
    assert first["qty"] == {"M": 2, "L": 3}
    assert first["total"] == 5
    assert matrix["total"] == 11
    assert matrix["lines"] == 3


def test_the_barcode_stays_on_its_own_cell():
    """Barkodlar satirin altina LISTELENMEZ — okunmaz bir yigin oluyordu.

    Her beden ayri bir barkod, yani hucre basina bir numara.
    """
    matrix = build_matrix([_line(size="M", barcode="111"),
                           _line(size="L", barcode="222")])

    assert matrix["groups"][0]["bc"] == {"M": "111", "L": "222"}


def test_the_box_number_is_read_once_for_the_whole_box():
    """`UD_SPS` koli basina tektir; basliga bir kez yazilir."""
    rows = [_line(size="M"), _line(size="L")]
    for row in rows:
        row["box_no"] = "30894"

    assert build_matrix(rows)["box_no"] == "30894"


def test_an_empty_box_produces_an_empty_matrix():
    matrix = build_matrix([])

    assert matrix["groups"] == [] and matrix["total"] == 0 and matrix["lines"] == 0


def test_the_three_numbers_at_the_foot_measure_three_different_things():
    """Kullanicinin ekrani: 5 model+renk, 28 barkod, 34 parca.

    "28 barcode lines" ile "34" ayni satirin iki ucundaydi ve esit sanildi
    (`sum` yerine `count` mu kullandin?). Ucu de ayri seyi olcer; bu test
    o senaryoyu birebir kurar.
    """
    rows = ([_line(style="76101", color="RED", size=s, qty=1) for s in "ABCDE"]
            + [_line(style="76103", color="RED", size=s, qty=1) for s in "ABCDE"]
            + [_line(style="76262", color="A/S", size=s, qty=1) for s in "ABCDEF"]
            + [_line(style="76263", color="A/S", size=s, qty=1) for s in "ABCDEF"]
            + [_line(style="76267", color="WHITE", size=s, qty=1) for s in "ABCDEF"])
    rows[0]["qty"] = 7  # 27 x 1 + 7 = 34 parca, yine 28 satir

    matrix = build_matrix(rows)

    assert matrix["models"] == 5
    assert matrix["lines"] == 28
    assert matrix["total"] == 34


# ------------------------------------------------------------- toplu okuma

class BulkSpy:
    """`IN (...)` sorgusunu taklit eder: yalnizca parametrede GECEN satirlari doner."""

    def __init__(self, rows_by_view=None):
        self.rows_by_view = rows_by_view or {}
        self.calls = []

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        for view, rows in self.rows_by_view.items():
            if f"FROM {view} " in sql:
                return [r for r in rows if r["UD_TrackingNumber"] in params]
        return []


def _many(no, view_row=None):
    row = _row()
    row["UD_TrackingNumber"] = no
    row.update(view_row or {})
    return row


def test_the_bulk_query_never_takes_a_row_lock(views):
    """Toplu okuma tekil okumadan daha uzun surer; kilit almasi HIC olmaz."""
    spy = BulkSpy()
    fetch_many(spy, ["38120177000001"])

    assert all("WITH (NOLOCK)" in sql for sql, _ in spy.calls)


def test_the_bulk_tracking_numbers_are_parameters_not_sql_text(views):
    """200 numara sorguya GOMULMEZ; hepsi `%s` yuvasindan gecer."""
    spy = BulkSpy()
    fetch_many(spy, ["38120177000001", "38120177000002"])

    sql, params = spy.calls[0]
    assert "38120177000001" not in sql
    assert sql.count("%s") == 2
    assert params == ("38120177000001", "38120177000002")


def test_more_numbers_than_mssql_allows_are_split_into_chunks(views):
    """MSSQL bir sorguda ~2100 parametre alir; asilirsa sorgu tumden patlar."""
    numbers = [f"381201770{i:05d}" for i in range(CHUNK + 5)]
    spy = BulkSpy()

    fetch_many(spy, numbers)

    first_view = [p for sql, p in spy.calls if "FA26" in sql]
    assert [len(p) for p in first_view] == [CHUNK, 5]


def test_boxes_from_two_seasons_are_both_resolved(views):
    """Tekil okumadaki "ilk dolu gorunum kazanir" kurali burada YANLIS olurdu:
    bir secimde FA26 ve SP26 kolileri birlikte bulunabilir."""
    spy = BulkSpy({
        "Vision_FA26_Boxed_EUROPE": [_many("38120177000001")],
        "Vision_SP26_Boxed_EUROPE": [_many("38120177000002")],
    })

    found = fetch_many(spy, ["38120177000001", "38120177000002"])

    assert set(found) == {"38120177000001", "38120177000002"}


def test_a_resolved_box_is_not_asked_of_the_next_view(views):
    """Bulunan koli bekleyen kumeden duser — ikinci gorunume gitmez."""
    spy = BulkSpy({"Vision_FA26_Boxed_EUROPE": [_many("38120177000001")]})

    fetch_many(spy, ["38120177000001", "38120177000002"])

    second = [p for sql, p in spy.calls if "SP26" in sql]
    assert second == [("38120177000002",)]


def test_a_box_with_no_record_is_simply_absent(views):
    """Canlida 986 parcanin 71'i sezon gorunumlerinde yok; cagiran bunu
    "kayit yok" diye isaretler, burada uydurma bos liste uretilmez."""
    assert fetch_many(BulkSpy(), ["38120177000001"]) == {}


def test_the_bulk_box_barcodes_come_from_the_base_table(views):
    spy = BulkSpy({"Erp_Box": [
        {"UD_TrackingNumber": "38120177000001", "BoxCode": "8682500093454"}]})

    codes = fetch_box_codes(spy, ["38120177000001", "38120177000002"])

    assert codes == {"38120177000001": "8682500093454"}
    assert "WITH (NOLOCK)" in spy.calls[0][0]


def test_a_number_repeated_in_the_selection_is_asked_once(views):
    """Kullanici ayni koliyi iki kez tikleyemez ama suzgec + \"hepsini sec\"
    ayni numarayi iki kez yollayabilir."""
    spy = BulkSpy()
    fetch_many(spy, ["38120177000001", "38120177000001", ""])

    assert spy.calls[0][1] == ("38120177000001",)


# ------------------------------------------------------------------ toplam adet (raporlar)

class QtySpy:
    """`SUM(qty) GROUP BY` sorgusunu taklit eder — satir satir degil TOPLAM doner."""

    def __init__(self, totals_by_view=None):
        self.totals_by_view = totals_by_view or {}
        self.calls = []

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        for view, totals in self.totals_by_view.items():
            if f"FROM {view} " in sql:
                return [{"UD_TrackingNumber": tn, "total_qty": q}
                        for tn, q in totals.items() if tn in params]
        return []


def test_total_quantities_groups_by_tracking_number(views):
    spy = QtySpy({"Vision_FA26_Boxed_EUROPE": {"38120177000001": Decimal("14.00000000")}})

    totals = total_quantities(spy, ["38120177000001"])

    assert totals == {"38120177000001": 14}
    assert "GROUP BY UD_TrackingNumber" in spy.calls[0][0]
    assert "SUM(qty)" in spy.calls[0][0]


def test_total_quantities_falls_back_to_the_other_season(views):
    """FA26'da olmayan bir numara SP26'da bulunabilir — `fetch_many` ile ayni desen."""
    spy = QtySpy({
        "Vision_FA26_Boxed_EUROPE": {"38120177000001": Decimal("5")},
        "Vision_SP26_Boxed_EUROPE": {"38120177000002": Decimal("9")},
    })

    totals = total_quantities(spy, ["38120177000001", "38120177000002"])

    assert totals == {"38120177000001": 5, "38120177000002": 9}


def test_a_box_with_no_content_row_is_absent_from_totals(views):
    assert total_quantities(QtySpy(), ["38120177000001"]) == {}


# --------------------------------------------------- buyuk kume optimizasyonu
# Canli olcum (2026-09-01): 1488 koli icin 500'luk `IN (...)` listeleriyle
# 81 SANIYE, gorunumu suzgecsiz gruplayarak 2 SANIYE (bkz. erp/contents.py).

def test_a_small_set_still_uses_a_filtered_query(monkeypatch):
    """Az koli sorulurken gorunumun tamami okunmamali."""
    from erp import contents
    from gls_api import config

    monkeypatch.setattr(config, "season_views", lambda: ["Vision_FA26_Boxed_EUROPE"])
    seen = []

    class C:
        def query(self, sql, params=()):
            seen.append((sql, params))
            return [{"UD_TrackingNumber": "38120177000001", "total_qty": 5}]

    contents.total_quantities(C(), ["38120177000001"])

    assert "IN (" in seen[0][0]
    assert seen[0][1] == ("38120177000001",)


def test_a_large_set_groups_the_whole_view_in_one_query(monkeypatch):
    """Esigin ustunde suzgecsiz tek GROUP BY; sonuc Python'da suzulur."""
    from erp import contents

    from gls_api import config

    monkeypatch.setattr(config, "season_views", lambda: ["Vision_FA26_Boxed_EUROPE"])
    monkeypatch.setattr(contents, "QTY_FULL_SCAN_THRESHOLD", 2)
    wanted = ["38120177000001", "38120177000002", "38120177000003"]
    seen = []

    class C:
        def query(self, sql, params=()):
            seen.append((sql, params))
            return [
                {"UD_TrackingNumber": "38120177000001", "total_qty": 5},
                {"UD_TrackingNumber": "38120177000002", "total_qty": 7},
                # Istenmeyen bir koli: Python tarafinda ELENMELI.
                {"UD_TrackingNumber": "99999999999999", "total_qty": 99},
            ]

    result = contents.total_quantities(C(), wanted)

    assert "IN (" not in seen[0][0]
    assert seen[0][1] == ()
    assert result == {"38120177000001": 5, "38120177000002": 7}


def test_the_large_set_path_still_falls_through_to_the_next_season_view(monkeypatch):
    """Ilk gorunumde bulunamayan koli ikinci gorunumde aranmaya devam eder."""
    from erp import contents
    from gls_api import config

    monkeypatch.setattr(contents, "QTY_FULL_SCAN_THRESHOLD", 1)
    monkeypatch.setattr(config, "season_views",
                        lambda: ["Vision_FA26_Boxed_EUROPE", "Vision_SP26_Boxed_EUROPE"])
    views = []

    class C:
        def query(self, sql, params=()):
            views.append(sql)
            if len(views) == 1:
                return [{"UD_TrackingNumber": "38120177000001", "total_qty": 5}]
            return [{"UD_TrackingNumber": "38120177000002", "total_qty": 9}]

    result = contents.total_quantities(C(), ["38120177000001", "38120177000002"])

    assert len(views) == 2
    assert result == {"38120177000001": 5, "38120177000002": 9}
