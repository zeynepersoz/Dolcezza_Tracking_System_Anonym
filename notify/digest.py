# -*- coding: utf-8 -*-
"""Gunluk ozet — veriyi topla, HTML uret, ek uret, gonder.

Mail bir YONETICI OZETIDIR: ust seride panelin KPI kartlari, altinda birkac
satirlik degerlendirme. Satir satir detay mail govdesine DEGIL, ekteki tek ham
Excel sayfasina yazilir (`notify/excel.py`). Sebep: 4000 parcalik bir sistemde
tablo maili sisirir, Gmail "mesajin tamami kirpildi" der, ve kimse mail govdesini
filtreleyip pivot kuramaz — Excel'de kurar.

Adimlar bilerek AYRI fonksiyon:
  gather()  saf veri, ag yok      -> testler sablon olmadan dogrulayabilir
  render()  saf HTML, veritabani yok
  run()     hepsini birlestirip gonderir

`run()` alici listesini PARAMETRE alir, config'ten kendi okumaz. Ileride
ajanslara rapor gonderilecegi zaman `gather`/`render` aynen kullanilip yalnizca
alici ve filtre degisecek.

`Tracker.on_change` kancasi bilerek KULLANILMIYOR: gunluk ozet veritabanini
zaten sorguluyor. Ayrica anlik bildirim POD'dan once giderdi — `tick()` icinde
`_archive_pods()` statu guncellemelerinden SONRA calisiyor.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import i18n
import timez
from gls_api import config
from notify import DOC_LANG, excel, mail
from web import pod_document as pod_pdf
from web.routers.pod import pod_archive_path

log = logging.getLogger("notify.digest")

# Konunun basina yazilir. Gonderen ADI degistirilemiyor: mail ortak
# `analyzer@` kutusundan cikiyor ve gelen kutusunda o kutunun M365'teki adi
# gorunuyor — govdedeki `from.name` Exchange tarafindan yok sayiliyor (canli
# denendi). Maili hangi sistemin yazdigi bu yuzden KONUDAN anlasilir.
SYSTEM_NAME = "Dolcezza Package System"

# Ekteki Excel'e yazilacak azami satir. Graph govdesi 4 MB; bu tavanda dosya
# ~1 MB'i gecmez.
MAX_DETAIL_ROWS = 5000

# Mutlak yol: ozet zamanlayicidan calisir, calisma dizinine guvenilemez.
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)
# Mail sablonu paneldeki Jinja ortamini kullanmaz (base.html'i genisletmiyor,
# Outlook icin ayri kurallari var) — `t`yi ayrica tanitmak gerekiyor.
_env.globals["t"] = i18n.t
# Panel ortamiyla (`web/templating.py`) AYNI filtre burada da tanitilmali:
# iki ayri Jinja ortami var, biri digerinin filtrelerini gormez.
_env.filters["localtime"] = timez.stamp


def gather(db, stale_days: int = 7, since: str = "", panel_url: str = "") -> dict:
    """Ozetin ham verisi. Ag cagrisi yok, sablon yok.

    Sayilar KUMULATIFTIR (sezonun tamami), "bu hafta" degil: istenen sey
    "FA26 sezonunda su kadar gonderiden su kadari teslim edildi". `delivered`
    yalnizca son 24 saati tutar — govdedeki "bugun ne oldu" cumlesi ve ekteki
    FLAG sutunu icin.
    """
    # UTC: `since` veritabanindaki naive UTC damgalarla metin olarak
    # karsilastiriliyor (`delivered_since`). Yerel saatle hesaplanirsa pencere
    # 3 saat kayar ve ozet yanlis teslimat listesi gonderir.
    if not since:
        since = (timez.utc_now() - timedelta(hours=24)).isoformat(timespec="seconds")

    problems = db.problem_parcels(stale_days=stale_days, limit=MAX_DETAIL_ROWS)
    by_kind: dict[str, list[dict]] = {kind: [] for kind in db.PROBLEM_KINDS}
    for row in problems:
        by_kind[row["problem_kind"]].append(row)

    counts = db.counts_by_status()
    total = db.total()
    parcels = db.list_parcels(limit=MAX_DETAIL_ROWS)
    return {
        # Bu deger mail basliginda ve dosya adinda GORUNUR -> yerel saat.
        "generated_at": timez.now(),
        "since": since,
        "stale_days": stale_days,
        "panel_url": panel_url.rstrip("/"),
        "season": config.season_label(),
        "counts": counts,
        "total": total,
        # Sezonun tamamindaki teslim orani — basliktaki tek cumlenin kaynagi.
        "delivered_pct": round(counts["delivered"] * 100 / total) if total else 0,
        "on_the_way": counts["in_transit"] + counts["out_for_delivery"],
        "problem_total": len(problems),
        "delivered": db.delivered_since(since, limit=MAX_DETAIL_ROWS),
        "exceptions": by_kind["exception"],
        "damaged": by_kind["damaged"],
        "stale": by_kind["stale"],
        "pod_missing": by_kind["pod_missing"],
        "partial": by_kind["partial"],
        "not_handed_over": by_kind["not_handed_over"],
        "returned": by_kind["returned"],
        "parcels": parcels,
        "detail_rows": len(parcels),
    }


def pod_document(parcel: dict) -> bytes | None:
    """Arsivdeki POD'u indirilebilir PDF belgesine cevirir; olmazsa None.

    Arsivde HAM goruntu (GLS NL'de BMP/PNG) durur — ciplak bir fotograf, icinde
    takip no ya da alici yoktur. Mail ekine ham goruntuyu koymak ise yaramaz;
    panelin indirdigi belgenin AYNISI konur (`web/pod_document.py`).
    ShipIT/T&T zaten hazir PDF verir, ona dokunulmaz.

    Belge uretilemezse ham goruntuye DUSULMEZ: ek zaten kolaylik, dosyanin
    tamami arsivde ve panelde duruyor.
    """
    path = Path(parcel["pod_path"])
    try:
        raw = path.read_bytes()
    except OSError:                       # arsivden elle silinmis olabilir
        log.warning("POD dosyasi okunamadi: %s", parcel.get("tracking_no"))
        return None
    if path.suffix.lower() == ".pdf":
        return raw
    try:
        info = json.loads(path.with_suffix(".json").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):         # GLS bilgileri yoksa belge yine uretilir
        info = {}
    try:
        return pod_pdf.customer_document(parcel, raw, info)
    except Exception:
        log.exception("POD belgesi uretilemedi: %s", parcel.get("tracking_no"))
        return None


def pod_zip(data: dict) -> tuple[str, bytes] | None:
    """Son 24 saatte teslim edilenlerin POD belgelerini tek zip'e koyar.

    Arsivden URETIR — ham goruntuler `OUTPUT_DIR` altinda kalir, bu ek yalnizca
    kolaylik. Tek tek PDF eklemek yerine zip: 30 ek bir maili okunamaz yapar.
    Toplam tavan asilirsa kalanlar DUSURULUR (mail yine gider, panelde hepsi
    var); bir POD yuzunden ozetin tamamini kaybetmek kabul edilemez.
    """
    parcels = [p for p in data["delivered"] if p.get("pod_path")]
    if not parcels:
        return None
    # Ad ve klasor duzeni panelden inen zip'le AYNI (`pod_archive_path`) —
    # aliciya ayni dosya iki farkli adla ulasmasin. Siralama ayni faturanin
    # kolilerini yan yana getirir: bir fatura 34 koliye kadar cikabiliyor.
    parcels.sort(key=pod_archive_path)

    buf = io.BytesIO()
    written = added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for parcel in parcels:
            content = pod_document(parcel)
            if content is None:
                continue
            if written + len(content) > mail.MAX_ATTACHMENT_BYTES:
                log.warning("POD zip tavani asildi, kalanlar atlandi")
                break
            zf.writestr(pod_archive_path(parcel), content)
            written += len(content)
            added += 1

    data["pod_zip_count"] = added
    if not added:
        return None
    return f"POD_{data['generated_at'].strftime('%Y-%m-%d')}.zip", buf.getvalue()


def render(data: dict) -> tuple[str, str]:
    """(konu, html) dondurur. Veritabanina dokunmaz; cikti daima Ingilizce."""
    day = data["generated_at"].strftime("%d.%m.%Y")
    with i18n.use(DOC_LANG):
        subject = i18n.t("mail.subject", system=SYSTEM_NAME,
                         season=f"{data['season']} " if data["season"] else "",
                         day=day, delivered=len(data["delivered"]),
                         problems=len(data["exceptions"]))
        html = _env.get_template("mail/digest.html").render(
            day=day, data=data, system_name=SYSTEM_NAME, lang=DOC_LANG)
    return subject, html


def run(db, recipients: list[str], stale_days: int = 7, hours: int = 24,
        panel_url: str = "") -> tuple[bool, str]:
    """Ozeti + Excel ekini uretip gonderir. Alici yoksa aga hic cikmaz."""
    clean = [a.strip() for a in recipients if a and a.strip()]
    if not clean:
        return False, i18n.t("mail.no_recipients")
    since = (timez.utc_now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    data = gather(db, stale_days=stale_days, since=since, panel_url=panel_url)

    # SIRA: zip once, cunku govdedeki "N POD ekte" cumlesi sayisini ondan alir.
    attachments: list[tuple[str, bytes]] = []
    pods = pod_zip(data)
    if data["detail_rows"]:
        attachments.append((excel.filename(data), excel.build(data)))
    if pods:
        attachments.append(pods)
    subject, html = render(data)
    ok, detail = mail.send(clean, subject, html, attachments=attachments)
    log.info("Günlük özet: %d teslim, %d sorun, %d satır, %d POD, %d alıcı — %s",
             len(data["delivered"]), len(data["exceptions"]), data["detail_rows"],
             data.get("pod_zip_count", 0), len(clean),
             "gönderildi" if ok else detail)
    return ok, detail
