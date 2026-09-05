"""ERP'ye geri yazma — kolon sinirlari ve kume secimi.

Buradaki testlerin isi tek: yazma yuzeyinin DAR kaldigini kanitlamak. ERP
uretim veritabani; genisleyen bir UPDATE'i geri almak kolay degil.
"""
import re

import pytest

from erp import writeback
from erp.client import ERPError
from tracking.db import ShipmentsDB


@pytest.fixture()
def db(tmp_path):
    return ShipmentsDB(path=tmp_path / "s.db")


def _add(db, tracking_no, status, explain="", checked=True):
    db.add_parcel(tracking_no=tracking_no, channel="NL", reference="r",
                  consignee_name="c", country="DE")
    if status != "created":
        db.update_status(tracking_no, status)
    elif checked:
        db.mark_checked(tracking_no)
    if explain:
        db.update_tracking_info(tracking_no, explain=explain)


class FakeWriter:
    """Gercek yazici gibi ULASAN takip numaralarini doner.

    `missing`: ERP'de karsiligi olmayan (0 satir guncellenen) numaralar.
    """

    def __init__(self, missing=()):
        self.rows = None
        self.missing = set(missing)

    def update_many(self, rows):
        self.rows = rows
        return [row[5] for row in rows if row[5] not in self.missing]


def test_update_sql_touches_only_five_columns():
    """Yazilan kolon kumesi genislerse test kirilsin — bu bilincli bir karardir.

    `UD_OkutmaTarihi` GLS'in koliyi fiziksel teslim aldigi tarihtir.
    `UD_Problemli` (2026-08-28, İş biriminin istegi) koli GECMISTE hic
    "exception" statusune girmis mi. `UD_TasimaFirmasi` hala bilerek disarida
    (kullanici karari); geri eklenirse burasi doner.
    """
    sets = re.search(r"SET (.+?) WHERE", writeback.UPDATE_SQL).group(1)
    columns = [part.split("=")[0].strip() for part in sets.split(",")]
    assert columns == ["UD_TasimaDurumu", "UD_TasimaAciklama", "UD_TeslimTarihi",
                       "UD_OkutmaTarihi", "UD_Problemli"]
    assert " WHERE UD_TrackingNumber = %s" in writeback.UPDATE_SQL


@pytest.mark.parametrize("sql", [
    "DELETE FROM dbo.erp_Box",
    "UPDATE dbo.erp_Box SET UD_Buyer = %s",
    "UPDATE dbo.Vision_FA26_Boxed_EUROPE SET UD_TasimaDurumu = %s",
    "INSERT INTO dbo.erp_Box (RecId) VALUES (1)",
])
def test_only_the_declared_update_shape_is_allowed(sql):
    assert writeback._ALLOWED_WRITE.match(sql) is None


def test_writer_refuses_if_statement_is_swapped(monkeypatch, db):
    """UPDATE_SQL biri tarafindan degistirilirse yazma hic baslamaz."""
    monkeypatch.setattr(writeback, "UPDATE_SQL", "DELETE FROM dbo.erp_Box")
    with pytest.raises(ERPError):
        writeback.ERPWriter().update_many([("DELIVERED", "x", None, None, 0, "38120177000001")])


def test_only_nl_prefix_is_pushed(db):
    _add(db, "38120177000001", "delivered", "The parcel has been delivered.")
    _add(db, "21569927889", "delivered", "Irlanda — PHP tarafinin isi")
    rows = writeback.rows_to_push(db)
    assert [r[5] for r in rows] == ["38120177000001"]


def test_unchecked_parcels_are_not_pushed(db):
    """GLS'e hic sorulmamis parcanin durumu ERP'ye yazilmaz."""
    db.add_parcel(tracking_no="38120177000002", channel="NL", reference="r",
                  consignee_name="c", country="DE")
    assert writeback.rows_to_push(db) == []


def test_status_is_translated(db):
    _add(db, "38120177000003", "out_for_delivery", "In delivery in France")
    (durum, aciklama, teslim, okutma, problemli, tn), = writeback.rows_to_push(db)
    assert durum == "INDELIVERY"
    assert aciklama == "In delivery in France"
    assert teslim is None          # teslim edilmedi -> ERP'de NULL kalir
    assert okutma is None          # GLS'e henuz teslim alinmadi -> ERP'de NULL kalir
    assert problemli == 0          # hic exception olmadi
    assert tn == "38120177000003"


def test_gls_english_text_wins_over_translated_panel_text(db):
    """Panelde 'Teslim edildi, teslim alan: X' yazar; ERP'ye GLS'in metni gider."""
    _add(db, "38120177000004", "delivered")
    db.update_tracking_info("38120177000004",
                            last_event_text="Teslim edildi, teslim alan: Hoppe",
                            explain="The parcel has been delivered.")
    (_, aciklama, _, _, _, _), = writeback.rows_to_push(db)
    assert aciklama == "The parcel has been delivered."


def test_an_exception_row_explains_the_reason_not_the_last_scan(db):
    """ERP'de durum EXCEPTION ama aciklama "parca merkeze ulasti" yaziyordu."""
    _add(db, "38120177000030", "exception", "The parcel has reached the parcel center.")
    db.update_tracking_info("38120177000030",
                            issue_text="Check scan/Damaged in location (Nancy FR0054)")
    (_, aciklama, _, _, _, _), = writeback.rows_to_push(db)
    assert aciklama == "Check scan/Damaged in location (Nancy FR0054)"


def test_explanation_ends_with_the_last_event_date(db):
    """ERP ekraninda tarih sutunu YOK — olayin ne zaman oldugu aciklamada durur."""
    _add(db, "38120177000031", "in_transit", "The parcel has left the parcel center.")
    db.update_tracking_info("38120177000031", last_event_at="2026-08-14T19:48:42.724Z")
    (_, aciklama, _, _, _, _), = writeback.rows_to_push(db)
    assert aciklama == "The parcel has left the parcel center. · 14.08.2026 22:48"


def test_explanation_has_no_dangling_separator_without_a_date(db):
    _add(db, "38120177000032", "in_transit", "The parcel has left the parcel center.")
    (_, aciklama, _, _, _, _), = writeback.rows_to_push(db)
    assert aciklama == "The parcel has left the parcel center."


def test_status_column_length_is_respected(db, monkeypatch):
    monkeypatch.setitem(writeback.ERP_STATUS, "delivered", "D" * 80)
    _add(db, "38120177000005", "delivered")
    (durum, _, _, _, _, _), = writeback.rows_to_push(db)
    assert len(durum) == 50


def test_delivery_datetime_is_converted_to_istanbul(db):
    """GLS'in UTC damgasi ERP'ye Europe/Istanbul saatiyle yazilir (2026-08-19 karari)."""
    _add(db, "38120177000007", "delivered", "The parcel has been delivered.")
    db.update_tracking_info("38120177000007", delivered_date="2026-07-31T09:33:47Z")
    (_, _, teslim, _, _, _), = writeback.rows_to_push(db)
    assert teslim == "2026-07-31 12:33:47"


def test_handover_datetime_is_converted_to_istanbul(db):
    """`UD_OkutmaTarihi`: GLS'in koliyi FIZIKSEL teslim aldigi an (2026-08-28,
    İş biriminin istegi) — 'The parcel was handed over to GLS.' olayi."""
    _add(db, "38120177000033", "in_transit", "The parcel has left the parcel center.")
    db.update_tracking_info("38120177000033", handed_over_at="2026-07-28T12:56:10.43Z")
    (_, _, _, okutma, _, _), = writeback.rows_to_push(db)
    assert okutma == "2026-07-28 15:56:10"


def test_a_new_erp_column_reaches_already_pushed_rows_without_a_backfill_script(db):
    """Kullanicinin istegi (2026-08-28): 'öncekileri güncellememiz lazım'.

    `fingerprint()`e yeni bir alan eklenince eski satirlarin kayitli izi artik
    uyusmaz — bir sonraki `push()` onlari DOGAL olarak (bu kez yeni alanla
    birlikte) yeniden yazar. Ayri bir geri-doldurma betigi gerekmez.
    """
    _add(db, "38120177000034", "delivered", "The parcel has been delivered.")
    assert writeback.push(db, writer=FakeWriter())["gonderilen"] == 1   # ilk yazma

    db.update_tracking_info("38120177000034", handed_over_at="2026-07-28T12:56:10.43Z")
    writer = FakeWriter()
    result = writeback.push(db, writer=writer)
    assert result["gonderilen"] == 1                # okutma tarihi eklendigi icin tekrar yazildi
    okutma = writer.rows[0][3]
    assert okutma == "2026-07-28 15:56:10"


def test_a_currently_problematic_parcel_is_flagged(db):
    _add(db, "38120177000035", "exception", "Cannot be delivered.")
    (_, _, _, _, problemli, _), = writeback.rows_to_push(db)
    assert problemli == 1


def test_a_recovered_parcel_stays_flagged(db):
    """`UD_Problemli` KALICIDIR — koli sonradan duzelse (delivered olsa) bile 1 kalir.

    Kullanicinin istegi (2026-08-28): "gls gecmisinde exception etiketi
    daha once almis olanlari" isaretle — GECMIS, SU ANKI durum degil.
    """
    _add(db, "38120177000036", "exception", "Cannot be delivered.")
    db.update_status("38120177000036", "delivered")
    (_, _, _, _, problemli, _), = writeback.rows_to_push(db)
    assert problemli == 1


def test_a_parcel_that_never_had_an_exception_is_not_flagged(db):
    _add(db, "38120177000037", "in_transit", "On the road.")
    db.update_status("38120177000037", "delivered")
    (_, _, _, _, problemli, _), = writeback.rows_to_push(db)
    assert problemli == 0


def test_a_new_exception_flags_an_already_pushed_row_again(db):
    """`UD_Problemli` 0'dan 1'e gecince satir DOGAL olarak yeniden yazilir."""
    _add(db, "38120177000038", "in_transit", "On the road.")
    assert writeback.push(db, writer=FakeWriter())["gonderilen"] == 1

    db.update_status("38120177000038", "exception", note="Cannot be delivered.")
    writer = FakeWriter()
    result = writeback.push(db, writer=writer)
    assert result["gonderilen"] == 1
    assert writer.rows[0][4] == 1


def test_date_without_time_still_works():
    """Bazi kanallar yalnizca tarih veriyor; saat uydurulmaz, gun KAYDIRILMAZ."""
    assert writeback._delivery_date("2026-07-31") == "2026-07-31"


@pytest.mark.parametrize("bozuk", ["", None, "bilinmiyor", "31/07/2026"])
def test_unparsable_delivery_date_becomes_null(bozuk):
    """Tarih anlasilamiyorsa MSSQL'e uydurma deger degil NULL gider."""
    assert writeback._delivery_date(bozuk) is None


def test_dry_run_writes_nothing(db):
    _add(db, "38120177000006", "delivered", "The parcel has been delivered.")
    writer = FakeWriter()
    result = writeback.push(db, writer=writer, dry_run=True)
    assert writer.rows is None
    assert result["gonderilen"] == 1


def test_limit_allows_a_single_test_write(db):
    for i in range(5):
        _add(db, f"3812017700001{i}", "delivered", "x")
    writer = FakeWriter()
    writeback.push(db, writer=writer, limit=1)
    assert len(writer.rows) == 1


def test_an_unchanged_parcel_is_not_written_twice(db):
    """Is her takip turunda kosuyor; degismeyen satir ERP'ye yuk olmamali."""
    _add(db, "38120177000020", "delivered", "The parcel has been delivered.")
    assert writeback.push(db, writer=FakeWriter())["gonderilen"] == 1
    assert writeback.push(db, writer=FakeWriter())["gonderilen"] == 0


def test_a_changed_status_is_written_again(db):
    _add(db, "38120177000021", "in_transit", "On the road.")
    writeback.push(db, writer=FakeWriter())
    db.update_status("38120177000021", "delivered")
    writer = FakeWriter()
    writeback.push(db, writer=writer)
    (durum, *_), = writer.rows
    assert durum == "DELIVERED"


def test_force_rewrites_even_unchanged_rows(db):
    """ERP tarafinda elle bir sey silinirse tek cikis yolu bu."""
    _add(db, "38120177000022", "delivered", "x")
    writeback.push(db, writer=FakeWriter())
    assert writeback.push(db, writer=FakeWriter(), force=True)["gonderilen"] == 1


def test_a_row_that_never_reached_erp_is_tried_again(db):
    """ERP'de karsiligi olmayan satir "yazildi" sayilmamali.

    Canli olcum 2026-08-13: 311 parca panelde DELIVERED, ERP'de hala ANNOUNCED.
    Sebep: 0 satir guncelleyen UPDATE hata atmiyor, satir damgalaniyor ve bir
    daha hic denenmiyordu.
    """
    _add(db, "38120177000026", "delivered", "x")
    writeback.push(db, writer=FakeWriter(missing=["38120177000026"]))

    assert writeback.push(db, writer=FakeWriter())["gonderilen"] == 1


def test_rows_that_did_reach_erp_are_not_written_twice(db):
    """Ayni turda biri ulasip biri ulasmadiysa yalnizca ulasan damgalanir."""
    _add(db, "38120177000027", "delivered", "x")
    _add(db, "38120177000028", "delivered", "x")
    result = writeback.push(db, writer=FakeWriter(missing=["38120177000028"]))

    assert result["etkilenen"] == 1
    assert result["ulasmayan"] == ["38120177000028"]
    assert [row[5] for row in writeback.rows_to_push(db)] == ["38120177000028"]


def test_a_failed_write_leaves_the_parcel_for_the_next_round(db):
    class Failing:
        def update_many(self, rows):
            raise ERPError("MSSQL kapali")

    _add(db, "38120177000023", "delivered", "x")
    with pytest.raises(ERPError):
        writeback.push(db, writer=Failing())
    assert writeback.push(db, writer=FakeWriter())["gonderilen"] == 1


def test_tracking_round_writes_to_erp(db, monkeypatch):
    """Kullanici "senkron olsun" dedi: yazma artik elle degil, her turda."""
    from tracking.scheduler import Tracker

    _add(db, "38120177000024", "delivered", "x")
    monkeypatch.setattr(writeback.config, "mssql_configured", lambda: True)
    writer = FakeWriter()
    monkeypatch.setattr(writeback, "ERPWriter", lambda: writer)

    Tracker(db=db).tick()

    assert [row[5] for row in writer.rows] == ["38120177000024"]


def test_tracking_round_survives_an_erp_outage(db, monkeypatch):
    """MSSQL kapaliyken takip turu DURMAMALI — GLS taramasi ERP'den bagimsiz."""
    from tracking.scheduler import Tracker

    _add(db, "38120177000025", "delivered", "x")
    monkeypatch.setattr(writeback.config, "mssql_configured", lambda: True)
    monkeypatch.setattr(writeback, "push",
                        lambda *a, **kw: (_ for _ in ()).throw(ERPError("kapali")))

    tracker = Tracker(db=db)
    tracker.tick()

    assert tracker.last_erp_error == "kapali"
    assert tracker.last_run_at


# ------------------------------------------------ takip numarasinin ERP'ye yazilmasi
# Kullanici istegi (2026-09-01): "label olusturulduktan sonra takip no vs.
# otomatik bir sekilde girilmis olsun" — boylece etiketlenmis koli toplu
# etiket listesine bir daha dusmez (uygunlugun kaynagi koli olur).

def test_the_tracking_write_only_touches_its_own_column():
    """Yazma allowlist'i genisledi; BASKA kolonlara hala kapali olmali."""
    from erp import writeback as w

    assert w._ALLOWED_WRITE.match(w.SET_TRACKING_SQL)
    assert w._ALLOWED_WRITE.match(w.CLEAR_TRACKING_SQL)
    assert w._ALLOWED_WRITE.match(w.UPDATE_SQL)
    assert not w._ALLOWED_WRITE.match("UPDATE dbo.erp_Box SET UD_Buyer = %s WHERE RecId = %s")
    assert not w._ALLOWED_WRITE.match("DELETE FROM dbo.erp_Box WHERE RecId = %s")
    assert not w._ALLOWED_WRITE.match("UPDATE dbo.BCH_Customers SET UD_TrackingNumber = %s")


def test_the_set_statement_never_overwrites_a_filled_field():
    """Dolu bir alani ezmek baska bir sevkiyatin numarasini silmek olurdu."""
    from erp.writeback import SET_TRACKING_SQL

    assert "UD_TrackingNumber IS NULL OR" in SET_TRACKING_SQL
    assert "RecId = %s" in SET_TRACKING_SQL


def test_the_clear_statement_only_removes_our_own_number():
    """Baskasinin yazdigi numara silinmemeli — WHERE'de deger de eslesir."""
    from erp.writeback import CLEAR_TRACKING_SQL

    assert "LTRIM(RTRIM(UD_TrackingNumber)) = %s" in CLEAR_TRACKING_SQL


def test_writing_is_skipped_when_mssql_is_not_configured(monkeypatch):
    from erp import writeback as w
    from gls_api import config

    monkeypatch.setattr(config, "mssql_configured", lambda: False)
    monkeypatch.setattr(w, "_write_one", lambda *a: pytest.fail("MSSQL yokken yazilmamaliydi"))

    assert w.set_tracking_number("143729", "38120177028850") is False
    assert w.clear_tracking_number("143729", "38120177028850") is False


def test_writing_is_skipped_when_writeback_is_disabled(monkeypatch):
    from erp import writeback as w
    from gls_api import config

    monkeypatch.setattr(config, "mssql_configured", lambda: True)
    monkeypatch.setattr(config, "ERP_WRITEBACK_ENABLED", "0")
    monkeypatch.setattr(w, "_write_one", lambda *a: pytest.fail("kapaliyken yazilmamaliydi"))

    assert w.set_tracking_number("143729", "38120177028850") is False


def test_missing_arguments_are_a_no_op(monkeypatch):
    from erp import writeback as w
    from gls_api import config

    monkeypatch.setattr(config, "mssql_configured", lambda: True)
    assert w.set_tracking_number("", "38120177028850") is False
    assert w.set_tracking_number("143729", "") is False


def test_an_erp_failure_does_not_break_the_label_flow(monkeypatch):
    """Etiket ZATEN basildi; ERP'deki aksaklik akisi durdurmamali."""
    from erp import writeback as w
    from gls_api import config

    monkeypatch.setattr(config, "mssql_configured", lambda: True)
    monkeypatch.setattr(config, "ERP_WRITEBACK_ENABLED", "1")
    monkeypatch.setattr(w, "ERPWriter",
                        lambda: (_ for _ in ()).throw(RuntimeError("MSSQL kapali")))

    assert w.set_tracking_number("143729", "38120177028850") is False
