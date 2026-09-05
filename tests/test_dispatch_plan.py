"""Sevkiyat plani — kullanicinin birlestirme kurali burada dogrulanir."""
from dispatch.plan import (AMBIGUOUS_ADDRESS, MERGE_QUESTION, MISSING_WEIGHT,
                           NO_ADDRESS, READY, build_plan, is_gls_store_code)

ADRES = {"name": "AHA Austria", "street": "Hauptplatz", "house_number": "1",
         "postal_code": "1010", "city": "Wien", "country": "AT"}


def _box(store="NUNAT1N", invoice="5000402", rec_id="1", weight=12.0, **over):
    row = {"rec_id": rec_id, "box_code": f"868250009{rec_id}", "store_code": store,
           "invoice": invoice, "weight_kg": weight, "dimensions": "60X42X46",
           "customer_name": "AHA Austria", "country": "AT"}
    row.update(over)
    return row


def _book(mapping):
    return lambda code: list(mapping.get(code, []))


# ---------- magaza kodu kapsami ----------

def test_only_n_suffixed_codes_are_in_scope():
    """`...GB1P` Irlanda/ShipIT tarafi — kullanici karariyla kapsam disi."""
    assert is_gls_store_code("NUNAT1N")
    assert is_gls_store_code("GERDE4N")     # `1`/`2` disinda rakam da var
    assert not is_gls_store_code("ZNEGB1P")
    assert not is_gls_store_code("")


def test_out_of_scope_codes_are_reported_not_swallowed():
    """Operator neyin gonderilmedigini gormeli."""
    plan = build_plan([_box(store="ZNEGB1P")], _book({}))

    assert plan.shipments == []
    assert [(s.store_code, s.reason) for s in plan.skipped] == [("ZNEGB1P", "not_gls_code")]


# ---------- gruplama ----------

def test_boxes_of_one_store_become_one_shipment():
    rows = [_box(rec_id="1", weight=17.0), _box(rec_id="2", weight=14.0)]
    ship = build_plan(rows, _book({"NUNAT1N": [ADRES]})).shipments[0]

    assert ship.state == READY
    assert ship.box_count == 2
    assert ship.total_weight == 31.0
    assert ship.reference == "5000402"


def test_two_stores_stay_separate():
    rows = [_box(store="NUNAT1N"), _box(store="ORYDE2N", rec_id="2")]
    plan = build_plan(rows, _book({"NUNAT1N": [ADRES], "ORYDE2N": [ADRES]}))

    assert [s.store_code for s in plan.shipments] == ["NUNAT1N", "ORYDE2N"]


# ---------- kullanicinin birlestirme kurali ----------

def test_same_store_two_invoices_one_address_is_merged_and_asked():
    """Olculdu (14 Agu): WNPAT1N -> 5000402 ve 5000419.

    Adres tek oldugu icin tek sevkiyat olur, referansa iki fatura da yazilir —
    ama durum `merge_question`: arayuz operatore sorar.
    """
    rows = [_box(rec_id="1", invoice="5000402"), _box(rec_id="2", invoice="5000419")]
    ship = build_plan(rows, _book({"NUNAT1N": [ADRES]})).shipments[0]

    assert ship.state == MERGE_QUESTION
    assert ship.merged
    assert ship.reference == "5000402+5000419"
    assert ship.sendable          # varsayilan birlestirmek


def test_operator_can_split_the_merge_into_one_shipment_per_invoice():
    rows = [_box(rec_id="1", invoice="5000402"), _box(rec_id="2", invoice="5000419")]
    plan = build_plan(rows, _book({"NUNAT1N": [ADRES]}), split_stores=["NUNAT1N"])

    assert [s.reference for s in plan.shipments] == ["5000402", "5000419"]
    assert all(s.state == READY for s in plan.shipments)


def test_two_addresses_for_one_code_block_the_shipment():
    """"farkli adresse fatura numarasini secip adresleri elle girebilsin" —
    sistem tahmin etmez, operatore sorar."""
    ship = build_plan([_box()], _book({"NUNAT1N": [ADRES, dict(ADRES, city="Graz")]})).shipments[0]

    assert ship.state == AMBIGUOUS_ADDRESS
    assert not ship.sendable
    assert len(ship.candidates) == 2


def test_missing_address_blocks_the_shipment():
    ship = build_plan([_box()], _book({})).shipments[0]

    assert ship.state == NO_ADDRESS
    assert not ship.sendable
    assert ship.address is None


def test_zero_weight_blocks_the_shipment():
    """Uydurma agirlikla etiket basmak yanlis navlun demek — Sentez'de duzeltilmeli."""
    rows = [_box(rec_id="1", weight=12.0), _box(rec_id="2", weight=0.0)]
    ship = build_plan(rows, _book({"NUNAT1N": [ADRES]})).shipments[0]

    assert ship.state == MISSING_WEIGHT
    assert not ship.sendable


# ---------- siparis bazinda detay kirilimi ----------

def test_invoice_lines_group_boxes_by_invoice_in_order():
    """30 kolilik gonderide operator hangi fatura kac koli/kg gorebilsin."""
    rows = [_box(rec_id="1", invoice="5000402", weight=10.0),
            _box(rec_id="2", invoice="5000402", weight=5.0),
            _box(rec_id="3", invoice="5000419", weight=8.0)]
    ship = build_plan(rows, _book({"NUNAT1N": [ADRES]})).shipments[0]

    lines = ship.invoice_lines
    assert [l["invoice"] for l in lines] == ["5000402", "5000419"]  # gorulme sirasi
    assert lines[0]["box_count"] == 2 and lines[0]["total_weight"] == 15.0
    assert lines[1]["box_count"] == 1 and lines[1]["total_weight"] == 8.0
    assert not any(l["missing_weight"] for l in lines)


def test_invoice_lines_flag_the_invoice_with_missing_weight():
    rows = [_box(rec_id="1", invoice="5000402", weight=12.0),
            _box(rec_id="2", invoice="5000419", weight=0.0)]
    ship = build_plan(rows, _book({"NUNAT1N": [ADRES]})).shipments[0]

    by_inv = {l["invoice"]: l for l in ship.invoice_lines}
    assert by_inv["5000402"]["missing_weight"] is False
    assert by_inv["5000419"]["missing_weight"] is True


# ---------- cift etiket korumasi ----------

def test_already_labeled_boxes_are_dropped():
    rows = [_box(rec_id="1"), _box(rec_id="2")]
    ship = build_plan(rows, _book({"NUNAT1N": [ADRES]}), already_labeled=["1"]).shipments[0]

    assert ship.box_count == 1
    assert ship.boxes[0].rec_id == "2"


def test_fully_labeled_store_is_skipped_not_resent():
    """Ayni gun ikinci kez calistirilirsa CIFT ETIKET basilmamali."""
    rows = [_box(rec_id="1"), _box(rec_id="2")]
    plan = build_plan(rows, _book({"NUNAT1N": [ADRES]}), already_labeled=["1", "2"])

    assert plan.shipments == []
    assert plan.skipped[0].reason == "already_labeled"


# ---------- siralama ----------

def test_blocked_shipments_sort_after_sendable_ones():
    rows = [_box(store="MMMAT1N", rec_id="1"), _box(store="QQQDE1N", rec_id="2")]
    plan = build_plan(rows, _book({"MMMAT1N": [ADRES]}))

    assert [s.store_code for s in plan.shipments] == ["MMMAT1N", "QQQDE1N"]
    assert len(plan.sendable) == 1 and len(plan.blocked) == 1


# ------------------------------------------- magaza kodu kanal ayrimi
# Kullanici bildirdi (2026-09-01): PNHFR1N ve T81FR1N "yazdirilacaklar"
# listesinde hic cikmiyordu. Kod bicimi sanildigi kadar duzenli degil —
# olculdu: GLS NL ile FIILEN gonderi yapilmis 536 magazanin 17'sini eski
# olumlu kalip (`^[A-Z]{3,8}\d[N]$`) reddediyordu.

def test_real_gls_nl_store_codes_are_all_accepted():
    """Hepsi canlida GLS NL (38120177...) ile gonderi yapmis GERCEK kodlar."""
    from dispatch.plan import is_gls_store_code

    for code in ("NUNAT1N",      # duzenli
                 "PNHFR1N",      # kullanicinin bildirdigi (icinde "AU" geciyor)
                 "T81FR1N",      # onekte RAKAM var
                 "OLIESCN",      # son harften once rakam yok
                 "ZNES12N",      # onek 2 karakter
                 "ZBDE10N",      # onek 2 karakter
                 "NIEPL01",      # `N` ile bitmiyor
                 "ONCBE01",
                 "3RDIT1N",      # rakamla BASLIYOR
                 "MARTESN"):
        assert is_gls_store_code(code), code


def test_ireland_shipit_codes_are_excluded():
    """Irlanda/ShipIT kodlari `P` ile biter — toplu etikete girmemeli."""
    from dispatch.plan import is_gls_store_code

    for code in ("JUVGB01P", "QBGGB1P-001", "WNLGB3P-001", "TTTGB1P", "MHMGB01P"):
        assert not is_gls_store_code(code), code


def test_a_blank_code_is_not_a_gls_store():
    from dispatch.plan import is_gls_store_code
    assert not is_gls_store_code("")
    assert not is_gls_store_code(None)


def test_the_rule_is_case_insensitive():
    from dispatch.plan import is_gls_store_code
    assert is_gls_store_code("pnhfr1n")
    assert not is_gls_store_code("juvgb01p")


def test_box_from_row_carries_the_season():
    """Kullanici bildirdi (2026-09-02): toplu etiketle olusan kolilerin sezonu
    bos gozukuyordu — bkz. `dispatch/engine.py:_record`."""
    from dispatch.plan import Box

    assert Box.from_row(_box(season="FA26")).season == "FA26"
    assert Box.from_row(_box()).season == ""
