"""Takip durumunu Sentez/BlueCherry ERP'ye geri yazar (`dbo.erp_Box`).

Bu modul, `erp/sync.py`'nin TERSI yonde calisir: orada MSSQL'den okuyoruz,
burada panelde toplanan GLS takip durumunu ERP'nin koli tablosuna isliyoruz ki
ERP kullanicisi kargonun nerede oldugunu kendi ekraninda gorsun.

YAZMA SINIRLARI — bilerek dar tutuldu:
  * Yalnizca `dbo.erp_Box` tablosunun BES kolonu yazilir:
        UD_TasimaDurumu   (nvarchar 50)   ornek: DELIVERED
        UD_TasimaAciklama (nvarchar max)  GLS'in son olay metni
        UD_TeslimTarihi   (datetime)      Europe/Istanbul saati; teslim yoksa NULL
        UD_OkutmaTarihi   (datetime)      GLS'in koliyi FIZIKSEL teslim aldigi
                                           an ("The parcel was handed over to
                                           GLS."), Europe/Istanbul saati; henuz
                                           teslim alinmadiysa NULL. İş biriminin
                                           2026-08-28 istegiyle eklendi — eski
                                           satirlar da (fingerprint degisince)
                                           dogal olarak geri doldurulur, ayri bir
                                           betik gerekmez.
        UD_Problemli      (tinyint, 0/1)  Koli GECMISTE (su an degil) HIC
                                           "exception" statusune girmis mi —
                                           `status_events`teki KALICI izden
                                           (bir daha DUZELSE bile 1 kalir).
                                           İş biriminin 2026-08-28 istegiyle
                                           eklendi: "gls gecmisinde exception
                                           etiketi almis olanlari isaretle."
    `UD_TasimaFirmasi` KASITLI olarak disarida — tasiyici zaten takip
    numarasinin oneginden belli, ERP'de ayrica tutulmasi istenmedi.
  * INSERT ve DELETE YOKTUR; tek izin verilen ifade asagidaki UPDATE'tir.
  * Satirlar `UD_TrackingNumber` ile eslesir — magaza kodu ya da fatura no
    ESLESMEYE GIRMEZ. Ayni takip numarasi birden fazla koli satirinda olabilir
    (olcum 2026-08-18: 1114 numara tek satir, 8 numara iki satir) — hepsi ayni
    durumu alir, bu dogrudur cunku durum PARCAYA aittir. Ayni olcumde bir
    numaranin farkli magazaya ya da farkli paketleme tarihine yayildigi HIC
    gorulmedi; dar olcut bizi koruyor.
  * ERP'nin `UpdatedAt`/`UpdatedBy` alanlarina DOKUNULMAZ; onlar Sentez'in
    kendi kullanici izidir, disaridan kirletilmemeli.

Is bolumu (2026-08 olcumu): `2156…` (GLS Irlanda) numaralarini İş biriminin
PHP projesi zaten yaziyor (1100 satir DELIVERED). Bizim yazdigimiz kume
`38120177…` (GLS NL) numaralaridir ve o satirlarda bu kolonlar bostur —
cakisma yoktur. `ERP_TRACKING_PREFIX` bu sinirin kod karsiligidir.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys

from erp.client import ERPClient, ERPError

import i18n
import timez
from gls_api import config
from tracking.db import ShipmentsDB, with_stamp

log = logging.getLogger("erp.writeback")

# Yalnizca bu onekli numaralar yazilir (bkz. modul basligi).
ERP_TRACKING_PREFIX = "38120177"

# Kolon uzunlugu (INFORMATION_SCHEMA'dan). Asilirsa MSSQL satiri reddeder.
_MAX_DURUM = 50

# Panel statusu -> ERP'nin bekledigi metin. PHP tarafi buyuk harf yaziyor,
# ayni gorunumu koruyoruz.
ERP_STATUS = {
    "delivered": "DELIVERED",
    "out_for_delivery": "INDELIVERY",
    "in_transit": "INTRANSIT",
    "created": "ANNOUNCED",
    "exception": "EXCEPTION",
    "returned": "RETURNED",
}

UPDATE_SQL = (
    "UPDATE dbo.erp_Box "
    "SET UD_TasimaDurumu = %s, UD_TasimaAciklama = %s, UD_TeslimTarihi = %s, "
    "UD_OkutmaTarihi = %s, UD_Problemli = %s "
    "WHERE UD_TrackingNumber = %s"
)

# Etiket basilinca kolinin KENDI satirina takip numarasi yazilir (kullanici
# istegi, 2026-09-01: "label olusturulduktan sonra takip no vs. otomatik bir
# sekilde girilmis olsun"). Bunun asil degeri sudur: toplu etiket listesi
# `Erp_Box.UD_TrackingNumber` BOS olan kolileri gosterir, yani etiketlenmis
# koli bir daha listeye HIC dusmez — uygunlugun tek dogruluk kaynagi koli olur
# (kullanicinin "sorgular koliden olmali" kurali).
#
# `RecId` ile eslesir (kolinin kimligi), takip numarasiyla degil: numara zaten
# yeni yaziliyor. Ustune YAZMAZ — dolu bir alani ezmek, baska bir sevkiyatin
# numarasini silmek demek olurdu.
SET_TRACKING_SQL = (
    "UPDATE dbo.erp_Box "
    "SET UD_TrackingNumber = %s "
    "WHERE RecId = %s AND (UD_TrackingNumber IS NULL OR LTRIM(RTRIM(UD_TrackingNumber)) = '')"
)

# Etiket SILININCE alan bosaltilir: koli yeniden etiketlenebilmeli (ayni
# kural — bkz. tracking/db.py:delete_parcel). Yalnizca BIZIM yazdigimiz numara
# silinir; baskasinin yazdigi bir numara oldugu gibi kalir.
CLEAR_TRACKING_SQL = (
    "UPDATE dbo.erp_Box "
    "SET UD_TrackingNumber = NULL "
    "WHERE RecId = %s AND LTRIM(RTRIM(UD_TrackingNumber)) = %s"
)

# Yazma istemcisine baska hicbir ifade gecmesin: yalnizca yukaridaki UC sekil.
_ALLOWED_WRITE = re.compile(
    r"^\s*UPDATE\s+dbo\.erp_Box\s+SET\s+(UD_Tasima\w+|UD_TrackingNumber)\s*=\s*(%s|NULL)\b",
    re.IGNORECASE,
)


class ERPWriter(ERPClient):
    """`erp_Box`'in tasima kolonlarini gunceller. Baska hicbir yazma yapmaz."""

    def update_many(self, rows: list[tuple]) -> list[str]:
        """rows: (durum, aciklama, teslim_tarihi, okutma_tarihi, problemli, tracking_no). ULASAN takip numaralari.

        Satirlar TEK TEK yazilir, `executemany` ile degil: hangi takip
        numarasinin ERP'de karsiligi olmadigini ancak boyle bilebiliriz.
        `executemany` toplu bir `rowcount` (cogu zaman -1) doner ve ERP'de hic
        satiri olmayan bir numara sessizce "yazildi" sayilirdi — cagiran onu
        damgalayip bir daha hic denemiyordu (canli olcum 2026-08-13: 311 parca
        panelde DELIVERED, ERP'de hala ANNOUNCED).

        `rowcount` -1 (surucu bildirmedi) ise ulasmis sayilir: bilinmezligi
        "yazilmadi" saymak her turda ayni satirlari yeniden yazmak olurdu.
        """
        if not rows:
            return []
        if not _ALLOWED_WRITE.match(UPDATE_SQL):
            raise ERPError(i18n.t("erp.write_not_allowed"))
        written: list[str] = []
        with self._connect() as conn:
            cursor = conn.cursor()
            try:
                for row in rows:
                    cursor.execute(UPDATE_SQL, row)
                    if cursor.rowcount != 0:
                        written.append(row[5])
            except Exception as exc:
                conn.rollback()
                raise ERPError(i18n.t("erp.update_failed", detail=exc)) from exc
            conn.commit()
        return written


def set_tracking_number(box_rec_id: str, tracking_no: str) -> bool:
    """Etiketi basilan kolinin ERP satirina takip numarasini yazar.

    True: yazildi. False: satir yok ya da alan ZATEN DOLU (ustune yazilmaz).
    Yazma kapaliysa/MSSQL yoksa sessizce False — etiket zaten basildi, ERP'ye
    yazamamak onu geri alamaz (cagiran taraf loglar).
    """
    if not (box_rec_id and tracking_no) or not config.mssql_configured():
        return False
    if config.ERP_WRITEBACK_ENABLED != "1":
        return False
    return _write_one(SET_TRACKING_SQL, (str(tracking_no), str(box_rec_id)))


def clear_tracking_number(box_rec_id: str, tracking_no: str) -> bool:
    """Etiket silinince kolinin takip numarasini bosaltir (yalnizca BIZIMKINI)."""
    if not (box_rec_id and tracking_no) or not config.mssql_configured():
        return False
    if config.ERP_WRITEBACK_ENABLED != "1":
        return False
    return _write_one(CLEAR_TRACKING_SQL, (str(box_rec_id), str(tracking_no)))


def _write_one(sql: str, params: tuple) -> bool:
    """Tek satirlik yazma; degisen satir varsa True. Hata YUKARI SIZMAZ:
    etiket akisini ERP'deki bir aksaklik durdurmamali."""
    if not _ALLOWED_WRITE.match(sql):
        raise ERPError(i18n.t("erp.write_not_allowed"))
    try:
        writer = ERPWriter()
        with writer._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            changed = cursor.rowcount
            conn.commit()
        return changed != 0
    except Exception:
        log.exception("ERP takip numarasi yazilamadi: %s", params)
        return False


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text[:limit]


def _delivery_date(value: str | None) -> str | None:
    """`2026-07-31T09:33:47Z` -> `2026-07-31 12:33:47`. Bozuk/bos ise None (SQL NULL).

    2026-08-19 KARARI: GLS'in UTC damgasi Europe/Istanbul'a CEVRILIR — ERP
    kullanicisi kendi saatini gormeli. Onceki davranis UTC'yi oldugu gibi
    yaziyordu. `tracking/db.py:event_stamp` ayni turda cevrilmeye baslandi;
    aciklamadaki damga ile teslim tarihi tutarli kalir.

    Yalniz tarih gelirse (saat yok) oldugu gibi doner: kaydirilirsa gun kayar.

    Ayni cevrim `UD_OkutmaTarihi` (handed_over_at) icin de kullanilir — GLS'in
    her iki olayi da (teslim alma / teslim etme) ayni UTC+"Z" bicimiyle verir.
    """
    text = (value or "").strip()
    when = timez.local(text)
    if when is not None:
        return when.strftime("%Y-%m-%d %H:%M:%S")
    # Saatsiz gun degeri — `timez.parse` bilerek None doner, burada korunur.
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    return None


def fingerprint(row: tuple) -> str:
    """Bir satirin ERP'ye yazilan degerlerinin ozeti — degisiklik boyle anlasilir.

    `UD_OkutmaTarihi`/`UD_Problemli` eklenince bu parmak izi de degisti —
    eskiden yazilmis TUM satirlarin kayitli izi artik uyusmuyor, bu yuzden bir
    sonraki `push()` onlari da (bu kez yeni alanlarla birlikte) DOGAL olarak
    yeniden yazar. Ayri bir geri-doldurma betigine gerek yok.
    """
    durum, note, teslim, okutma, problemli, _tracking_no = row
    return f"{durum}|{teslim or ''}|{okutma or ''}|{problemli}|{note}"


def rows_to_push(db: ShipmentsDB, prefix: str = ERP_TRACKING_PREFIX,
                 force: bool = False) -> list[tuple]:
    """ERP'ye yazilacak (durum, aciklama, teslim_tarihi, okutma_tarihi, problemli,
    takip_no) listesi.

    Durumu hic sorgulanmamis (GLS'ten cevap gelmemis) parcalar disarida kalir;
    ERP'ye "ANNOUNCED" yazip sonra hicbir zaman guncellememektense hic
    yazmamak dogrudur.

    Bir onceki yazmadan bu yana DEGISMEMIS satirlar da atlanir (`erp_pushed`
    parmak izi): is her takip turunda calisiyor ve degismeyen 500 satiri her 15
    dakikada bir yeniden yazmanin ERP'ye tek faydasi yuk olurdu. `force` bu
    korumayi kaldirir — ERP tarafinda elle bir sey silinirse gerekir.
    """
    # `UD_Problemli`: koli SU AN degil, GECMISTE HIC "exception" statusune
    # girmis mi. İş biriminin istegi (2026-08-28): "gls gecmisinde exception
    # etiketi almis olanlari isaretle" — bir kere 1 olunca (koli sonradan
    # duzelse bile) KALICI kalir, bu yuzden guncel `status` yerine `status_events`
    # KALICI izine bakilir. Su anki durum da AYRICA kontrol edilir (savunma
    # amacli — status_events'e henuz hic yazilmamis bir ilk tur ihtimaline karsi).
    # writeback yalnizca NL onekli kolileri yaziyor (asagidaki `LIKE ?`);
    # `exception_ever_tracking_nos` 2026-09-03'te oneki birakip KANALA gecti.
    exception_ever = db.exception_ever_tracking_nos("NL")

    rows = []
    for parcel in db.conn.execute(
        "SELECT tracking_no, status, issue_text, explain, last_event_text, "
        "last_event_at, delivered_date, handed_over_at, erp_pushed "
        "FROM parcels WHERE tracking_no LIKE ? AND last_checked_at IS NOT NULL",
        (f"{prefix}%",),
    ):
        durum = ERP_STATUS.get(parcel["status"])
        if not durum:
            continue
        # EXCEPTION satirinda aciklama SEBEBI anlatmali. Son olay cogu zaman
        # masumdur ("parca merkeze ulasti") — ERP ekraninda durum EXCEPTION
        # ama aciklama rutin gorunuyordu (kullanicinin Sentez ekrani, 2026-08-11).
        # Aciklamanin sonuna son islem tarihi eklenir: ERP ekraninda tarih
        # sutunu yok, kullanici olayin NE ZAMAN oldugunu baska turlu goremiyor.
        note = with_stamp(
            (parcel["issue_text"] or parcel["explain"] or parcel["last_event_text"] or "").strip(),
            parcel["last_event_at"],
        )
        problemli = 1 if (parcel["status"] == "exception"
                          or parcel["tracking_no"] in exception_ever) else 0
        row = (_clip(durum, _MAX_DURUM), note,
               _delivery_date(parcel["delivered_date"]),
               _delivery_date(parcel["handed_over_at"]), problemli, parcel["tracking_no"])
        if force or fingerprint(row) != (parcel["erp_pushed"] or ""):
            rows.append(row)
    return rows


def push(db: ShipmentsDB, writer: ERPWriter | None = None, dry_run: bool = False,
         limit: int = 0, force: bool = False) -> dict:
    """Panel durumunu ERP'ye yazar. `dry_run` hicbir sey yazmaz, sayar."""
    rows = rows_to_push(db, force=force)
    if limit > 0:
        rows = rows[:limit]

    result = {
        "gonderilen": len(rows),
        "etkilenen": 0,
        "ulasmayan": [],
        "dry_run": dry_run,
        "ornek": rows[:5],
    }
    if dry_run or not rows:
        return result

    writer = writer or ERPWriter()
    written = set(writer.update_many(rows))
    result["etkilenen"] = len(written)
    result["ulasmayan"] = [row[5] for row in rows if row[5] not in written]
    # Isaretleme YALNIZCA ERP'ye ulasan satira yapilir: hata alan da, ERP'de
    # karsiligi olmayan da bir sonraki tura kalmali. Aksi halde parmak izi
    # yazilir ve satir bir daha hic denenmez.
    db.mark_erp_pushed([(row[5], fingerprint(row)) for row in rows
                        if row[5] in written])
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Takip durumunu ERP'ye (erp_Box) yazar. Yalnizca uc tasima kolonu."
    )
    parser.add_argument("--dry-run", action="store_true", help="Hicbir sey yazma, sadece goster")
    parser.add_argument("--limit", type=int, default=0, help="En fazla kac parca yazilsin (deneme icin)")
    parser.add_argument("--force", action="store_true",
                        help="Degismemis satirlari da yeniden yaz")
    args = parser.parse_args(argv)

    if not config.mssql_configured():
        print("❌ MSSQL yapilandirilmamis (.env: MSSQL_*).")
        return 2

    result = push(ShipmentsDB(), dry_run=args.dry_run, limit=args.limit, force=args.force)
    print(f"gonderilen : {result['gonderilen']}")
    if not args.dry_run:
        print(f"etkilenen  : {result['etkilenen']}")
        ulasmayan = result["ulasmayan"]
        if ulasmayan:
            print(f"ULASMAYAN  : {len(ulasmayan)} (ERP'de bu takip numarasiyla satir yok)")
            for tn in ulasmayan[:10]:
                print(f"  {tn}")
    print("ornek:")
    for durum, aciklama, teslim, okutma, problemli, tn in result["ornek"]:
        print(f"  {tn} | {durum} | teslim={teslim or '-':10} | okutma={okutma or '-':10} "
              f"| problemli={problemli} | {aciklama[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
