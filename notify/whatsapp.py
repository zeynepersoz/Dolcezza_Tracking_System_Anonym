# -*- coding: utf-8 -*-
"""OpenWA WhatsApp bildirim modülü.

Üç farklı durum için WhatsApp bildirimi gönderir:
  1. Teslimat (`delivered`) -> 'delivery-confirmation' şablonu
  2. Sorun (`exception`)    -> 'delivery-problem' şablonu
  3. İade (`returned`)      -> İadeye özel WhatsApp bildirimi (şablon veya formatlı metin)

Kullanıcı kuralları:
  * Yalnızca bugünden (OPENWA_START_DATE) itibaren gerçekleşen yeni olaylar için bildirim gider.
  * Her durum için her koliye en fazla BİRER KEZ bildirim yapılır (wa_delivered_at / wa_problem_at / wa_returned_at).
  * Hafta sonu (Cumartesi/Pazar) sorun ve iade bildirimleri TAMAMEN atlanır — ertelenmez,
    kuyruğa alınmaz. Yalnızca teslimat bildirimi hafta sonu da anında gider.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import timedelta
from typing import Optional

import requests

import timez
from gls_api import config
from tracking.db import ShipmentsDB

log = logging.getLogger("notify.whatsapp")


def send_template(
    template_name: str,
    vars: dict,
    chat_id: str = "",
    url: str = "",
    session_id: str = "",
    api_key: str = "",
    timeout: int = 10,
) -> tuple[bool, str]:
    """OpenWA şablon mesajı gönderir.

    Döner: (başarılı_mı, yanıt_veya_hata_özeti)
    """
    target_chat = chat_id or config.OPENWA_CHAT_ID
    if not target_chat:
        return False, "chatId yok"

    if config.MODE == "mock" and not getattr(config, "OPENWA_FORCE_LIVE", False):
        log.info("Mock OpenWA template send: %s -> %s (vars=%s)", template_name, target_chat, vars)
        return True, "mock_sent"

    base_url = (url or config.OPENWA_URL).rstrip("/")
    sess_id = session_id or config.OPENWA_SESSION_ID
    key = api_key or config.OPENWA_API_KEY
    endpoint_url = f"{base_url}/api/sessions/{sess_id}/messages/send-template"

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": key,
    }
    payload = {
        "chatId": target_chat,
        "templateName": template_name,
        "vars": vars,
    }

    try:
        resp = requests.post(endpoint_url, json=payload, headers=headers, timeout=timeout)
        if 200 <= resp.status_code < 300:
            log.info("WhatsApp template '%s' gönderildi: %s (vars=%s)", template_name, target_chat, vars)
            return True, resp.text
        log.warning("WhatsApp template '%s' başarısız: HTTP %s - %s", template_name, resp.status_code, resp.text[:200])
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        log.exception("WhatsApp template '%s' gönderim hatası: %s", template_name, exc)
        return False, str(exc)


# OpenWA oturumunun "bagli" sayildigi durum(lar). WhatsApp Web oturumu
# telefondan koparsa sunucu AYAKTA kalir ve `send-text` cagrilari HTTP 2xx
# donmeye devam edebilir; mesaj ise hicbir yere gitmez.
#
# CANLI OLCUM (2026-09-01, kullanici bildirdi: "wp grubuna dun neden hicbir
# sey gelmedi"): oturum `qr_ready` durumundaydi, yani 29 Agustos'ta dusmus ve
# yeniden QR bekliyordu. Uc gun boyunca hicbir uyari cikmadi — 31 Agustos'ta
# algilanan 23 teslimatin hicbiri gruba dusmedi. Bu kontrol o sessizligi
# kapatir.
WA_CONNECTED_STATES = ("connected", "working", "authenticated", "ready")

# Oturum durumu her sayfa acilisinda sorulmaz — kisa omurlu onbellek
# (adres kaynagindaki desenin aynisi, bkz. web/address_source.py).
_STATUS_TTL_SECONDS = 120
_status_cache: dict = {"at": 0.0, "value": None}


def session_status(force: bool = False) -> dict:
    """OpenWA oturumunun durumu: {ok, status, detail}.

    `ok=True` yalnizca oturum GERCEKTEN bagliysa. Sunucuya ulasilamazsa da
    `ok=False` — ikisi de "mesaj gitmiyor" demektir ve operator ikisini de
    gormeli. Bildirimler kapaliysa (`OPENWA_ENABLED != 1`) kontrol yapilmaz.
    """
    if str(config.OPENWA_ENABLED).strip() != "1":
        return {"ok": True, "status": "disabled", "detail": ""}
    # Mock/gelistirme ortami aga CIKMAZ (`send_text` ile ayni kural) — yoksa
    # testler ve yerel calisma canli OpenWA sunucusuna baglanirdi.
    if config.MODE == "mock" and not getattr(config, "OPENWA_FORCE_LIVE", False):
        return {"ok": True, "status": "mock", "detail": ""}

    now = time.monotonic()
    if not force and _status_cache["value"] and (now - _status_cache["at"]) < _STATUS_TTL_SECONDS:
        return _status_cache["value"]

    base = (config.OPENWA_URL or "").rstrip("/")
    sid = config.OPENWA_SESSION_ID
    if not (base and sid):
        result = {"ok": False, "status": "unconfigured", "detail": "OPENWA_URL/SESSION_ID"}
    else:
        try:
            resp = requests.get(f"{base}/api/sessions/{sid}",
                                headers={"X-API-Key": config.OPENWA_API_KEY}, timeout=8)
            if resp.status_code >= 300:
                result = {"ok": False, "status": f"http_{resp.status_code}",
                          "detail": resp.text[:120]}
            else:
                state = str((resp.json() or {}).get("status") or "").strip().lower()
                result = {"ok": state in WA_CONNECTED_STATES, "status": state or "unknown",
                          "detail": ""}
        except Exception as exc:                      # noqa: BLE001
            result = {"ok": False, "status": "unreachable",
                      "detail": f"{type(exc).__name__}: {exc}"[:120]}

    _status_cache.update(at=now, value=result)
    return result


def send_text(
    text: str,
    chat_id: str = "",
    url: str = "",
    session_id: str = "",
    api_key: str = "",
    timeout: int = 10,
    link_preview: bool = True,
) -> tuple[bool, str]:
    """OpenWA doğrudan formatlı metin mesajı gönderir.

    Döner: (başarılı_mı, yanıt_veya_hata_özeti)

    `link_preview=False`, WhatsApp'ın ürettiği DEV önizleme kartını (kapak
    görseli + başlık + açıklama) kapatır — kullanıcı isteği (2026-09-02):
    "bu kadar kocaman olmasın bağlantı linki". Alan adı CANLI YOKLAMAYLA
    doğrulandı: `linkPreview` 201 verir, `linkpreview`/`preview` 400 — şema
    tanımadığı anahtarı reddettiği için bu alan gerçekten OKUNUYOR.
    """
    target_chat = chat_id or config.OPENWA_CHAT_ID
    if not target_chat:
        return False, "chatId yok"

    if config.MODE == "mock" and not getattr(config, "OPENWA_FORCE_LIVE", False):
        log.info("Mock OpenWA text send: -> %s: %s", target_chat, text[:100])
        return True, "mock_sent"

    base_url = (url or config.OPENWA_URL).rstrip("/")
    sess_id = session_id or config.OPENWA_SESSION_ID
    key = api_key or config.OPENWA_API_KEY
    endpoint_url = f"{base_url}/api/sessions/{sess_id}/messages/send-text"

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": key,
    }
    payload = {
        "chatId": target_chat,
        "text": text,
        "linkPreview": link_preview,
    }

    try:
        resp = requests.post(endpoint_url, json=payload, headers=headers, timeout=timeout)
        if 200 <= resp.status_code < 300:
            log.info("WhatsApp text gönderildi: %s", target_chat)
            return True, resp.text
        log.warning("WhatsApp text başarısız: HTTP %s - %s", resp.status_code, resp.text[:200])
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        log.exception("WhatsApp text gönderim hatası: %s", exc)
        return False, str(exc)


def send_document(pdf_bytes: bytes, filename: str, caption: str = "",
                  chat_id: str = "", timeout: int = 60) -> tuple[bool, str]:
    """PDF'i WhatsApp'a BELGE olarak gonderir. Doner: (basarili_mi, ayrinti).

    OpenWA sozlesmesi CANLI YOKLAMAYLA cikarildi (2026-09-01) — makine-okur
    dokumantasyon yok. Alan adlari KUCUK HARFTIR ve buyuk harflisi 400 verir:
        chatId, base64, mimetype, filename, caption
    (`fileName`/`mimeType`/`text`/`message` REDDEDILIR.) Basarili yanit 201.
    """
    target = chat_id or config.OPENWA_FEDEX_CHAT_ID
    if not target:
        return False, "chatId yok"
    if not pdf_bytes:
        return False, "bos dosya"

    if config.MODE == "mock" and not getattr(config, "OPENWA_FORCE_LIVE", False):
        log.info("Mock OpenWA document send: -> %s: %s (%s bayt)",
                 target, filename, len(pdf_bytes))
        return True, "mock_sent"

    url = (f"{(config.OPENWA_URL or '').rstrip('/')}"
           f"/api/sessions/{config.OPENWA_SESSION_ID}/messages/send-document")
    payload = {
        "chatId": target,
        "base64": base64.b64encode(pdf_bytes).decode(),
        "mimetype": "application/pdf",
        "filename": filename,
    }
    if caption:
        payload["caption"] = caption
    try:
        resp = requests.post(url, json=payload,
                             headers={"Content-Type": "application/json",
                                      "X-API-Key": config.OPENWA_API_KEY},
                             timeout=timeout)
        if 200 <= resp.status_code < 300:
            log.info("WhatsApp belge gonderildi: %s -> %s", filename, target)
            return True, resp.text
        log.warning("WhatsApp belge basarisiz: HTTP %s - %s",
                    resp.status_code, resp.text[:200])
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:                      # noqa: BLE001
        log.exception("WhatsApp belge gonderilemedi: %s", filename)
        return False, f"{type(exc).__name__}: {exc}"


def fedex_tracking_url(tracking_no: str) -> str:
    """FedEx takip adresi — KISA biçim, `https://www.` öneki YOK.

    Uzun biçim WhatsApp balonunda üç satıra sarıyordu (kullanıcı isteği,
    2026-09-02). WhatsApp çıplak alan adını da bağlantıya çevirdiği için
    tıklanabilirlik kaybolmaz.
    """
    return f"fedex.com/fedextrack/?trknbr={tracking_no}" if tracking_no else ""


def _customer_code(parcel: dict) -> str:
    """Şablon için mağaza/müşteri kodunu belirler (Örn: 'NUNAT1N', 'AHATEST')."""
    return (parcel.get("store_code")
            or parcel.get("consignee_name")
            or parcel.get("buyer")
            or "Müşteri").strip()


def _season_code(parcel: dict) -> str:
    """Şablon için sezon kodunu belirler (Örn: 'FA26')."""
    season = parcel.get("season") or ""
    if not season and parcel.get("source_sheet"):
        season = config.season_from_source(parcel["source_sheet"])
    return season.strip() or "FA26"


def _format_date(val: Optional[str]) -> str:
    """ISO tarihini 'DD.MM.YYYY' formatına çevirir — bkz. notify/partial_delivery.py."""
    if not val:
        return "—"
    day = str(val).strip()[:10]
    parts = day.split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 else day


def notify_delivery(parcel: dict, chat_id: str = "") -> tuple[bool, str]:
    """Teslimat bildirimini gönderir.

    Eskiden OpenWA'nın kendi 'delivery-confirmation' şablonu kullanılıyordu;
    metin OpenWA tarafında sabitti ve buradan degistirilemiyordu. Kullanicinin
    istegiyle (sevk tarihinin de mesaja eklenmesi) digerleri (notify_problem/
    notify_returned) gibi burada serbest metin olarak kuruldu — sablona bagli
    kalmadan tam kontrol.

    Tarih `created_at` DEGIL `shipment_date`dir — `created_at` bu kolinin
    BIZIM veritabanimiza NE ZAMAN eklendigini (ERP senkronu ani) tutar, ERP'nin
    kendi "Sevk Tarihi"yle (musterinin/magazanin bildigi gercek tarih) gunler
    farkli olabilir (canli ornek 2026-08-27: 38120177017908 icin created_at
    18.08, shipment_date 14.08). Kullanici bunu fark edip duzelttirdi.
    """
    customer = _customer_code(parcel)
    season = _season_code(parcel)
    tracking = str(parcel.get("tracking_no", "")).strip()
    shipped = _format_date(parcel.get("shipment_date"))

    text = (
        f"📦 *Package Delivered* 📦\n\n"
        f"*{customer}* için {shipped} tarihinde oluşturulan, {season} sezonuna ait "
        f"*{tracking}* takip numaralı gönderi teslim edilmiştir."
    )
    return send_text(text, chat_id=chat_id)


def notify_problem(parcel: dict, chat_id: str = "") -> tuple[bool, str]:
    """Sorun bildirimini gönderir — detaylı açıklama ile formatlı metin."""
    customer = _customer_code(parcel)
    tracking = str(parcel.get("tracking_no", "")).strip()
    shipped = _format_date(parcel.get("shipment_date"))

    # Sorun açıklaması: önce explain, sonra last_event_text, en son issue_text
    reason = (
        parcel.get("explain")
        or parcel.get("last_event_text")
        or parcel.get("issue_text")
        or ""
    ).strip()

    text = (
        f"⚠️ *Delivery Problem* ⚠️\n\n"
        f"*{customer}* için {shipped} tarihinde oluşturulan, gönderilen "
        f"*{tracking}* takip numaralı gönderide bir sorun oluşmuş. "
        f"Lütfen panelden takip edin."
    )
    if reason:
        text += f"\n\n📋 *Reason:* _{reason}_"

    return send_text(text, chat_id=chat_id)


def notify_returned(parcel: dict, chat_id: str = "") -> tuple[bool, str]:
    """İadeye özel WhatsApp bildirimini gönderir."""
    customer = _customer_code(parcel)
    tracking = str(parcel.get("tracking_no", "")).strip()
    season = _season_code(parcel)
    shipped = _format_date(parcel.get("shipment_date"))

    text = (
        f"🔄 *Gönderi İade Edildi* 🔄\n\n"
        f"*{customer}* ({season}) için {shipped} tarihinde oluşturulan, gönderilen "
        f"*{tracking}* takip numaralı gönderi göndericiye iade edilmiştir. "
        f"Lütfen panelden takip edin."
    )
    return send_text(text, chat_id=chat_id)


def notify_fedex_created(parcel: dict, pdf_bytes: bytes | None = None,
                         chat_id: str = "", filename: str = "") -> tuple[bool, str]:
    """Koli fotografi yuklenince FedEx grubuna BELGE + metin gonderir.

    Kullanici istegi (2026-09-01): "pdf olustugu anda pdf ekli bir mesaj gitsin,
    ... takip numarali gonderiniz olusturulmustur diye". Mesaj Ingilizce
    (2026-09-01/02: "fedexle alakali tum bildirimlerin ingilizce olmasi lazim").

    Sevk tarihi TEK cumleye gomuludur ("has been created at DD.MM.YYYY."), ayri
    bir "Created at:" satiri DEGIL (kullanici 2026-09-02: "olmasina gerek yok").
    Alici/magaza adi ve koli icerigi YAZILMAZ (kullanici 2026-09-02:
    "olusturuldu demen yeterli", "bunu mesaja ekleme") — icerigin tamami zaten
    ekteki PDF'te ve icerik listesi mesaji okunmaz hale getiriyordu.

    FedEx'in takip BAGLANTISI acikca basilir: WhatsApp duz metni tiklanabilir
    yapamaz, yalnizca URL'leri baglantiya cevirir.

    `pdf_bytes` yoksa yalnizca metin gider — belge uretilemediyse bildirim
    tumden kaybolmasin.
    """
    target_chat = chat_id or config.OPENWA_FEDEX_CHAT_ID
    if not target_chat:
        return False, "OPENWA_FEDEX_CHAT_ID tanimli degil"

    tn = str(parcel.get("tracking_no") or "").strip()
    shipped = _format_date(parcel.get("shipment_date"))
    satirlar = [
        "📦 *FedEx — Shipment Created*", "",
        f"Your shipment with tracking number *{tn}* has been created at {shipped}.",
        "", fedex_tracking_url(tn),
    ]
    text = "\n".join(satirlar)
    if pdf_bytes:
        # Ek adi cagirandan gelir (`tracking.box_image_filename`) ki gruba
        # dusen dosya panelden indirilenle AYNI adi tasisin.
        return send_document(pdf_bytes, filename or f"Shipment Details {tn}.pdf",
                             caption=text, chat_id=target_chat)
    return send_text(text, chat_id=target_chat, link_preview=False)


def notify_fedex_status(parcel: dict, chat_id: str = "") -> tuple[bool, str]:
    """FedEx kolisinin durumu degisince FedEx grubuna bilgi mesaji.

    Kullanici istegi: "kargo hareketi geldikce takip numarasinin oldugu bir
    mesajla durum bildirsin, takip numarasina tiklayinca tracking baglantisi
    acilsin".
    """
    from tracking.db import status_label

    tn = str(parcel.get("tracking_no") or "").strip()
    # Dil SABIT INGILIZCE (kullanici karari, 2026-09-01: "fedexle alakali tum
    # bildirimlerin ingilizce olmasi lazim"). `status_label(code)` argumansiz
    # cagrilirsa ISTEGIN dilini alir — arka plan tarayicisinda bu Turkce'ye
    # duser ve gruba "Yolda"/"Teslim Edildi" giderdi.
    durum = status_label(parcel.get("status") or "", "en")
    olay = (parcel.get("last_event_text") or "").strip()
    shipped = _format_date(parcel.get("shipment_date"))
    satirlar = ["🚚 *FedEx — Tracking Update*", "",
                f"*{tn}*", f"Status: *{durum}*", f"Created at: {shipped}"]
    if olay:
        satirlar.append(f"Latest event: {olay}")
    satirlar += ["", fedex_tracking_url(tn)]
    return send_text("\n".join(satirlar),
                     chat_id=chat_id or config.OPENWA_FEDEX_CHAT_ID,
                     link_preview=False)


def notify_fedex_daily_summary(db: ShipmentsDB, chat_id: str = "",
                               days: int = 7) -> tuple[bool, str]:
    """Son N gunun FedEx gonderilerinin durum+link ozetini TEK mesajda gonderir.

    Kullanici istegi (2026-09-02): "saat aksam 6 da bir mesaj yollansin son 7
    gonderi statuleri ve linklerinin oldugu tek bir mesaj ... son 7 günü baz
    alacağı için orada last 7 days yazabilir". Her koli icin ayri mesaj degil —
    `notify_fedex_status` zaten her olayda kendi mesajini gonderiyor, bu OZET
    akistan bagimsiz gunluk bir hatirlaticidir.

    Pencere `shipment_date` (Istanbul saatiyle BUGUNden geriye `days` gun)
    uzerinden secilir — "son 7 gonderi" (sabit sayida kayit) DEGIL, "son 7
    gun" (tarih penceresi): baslikta sayi yazmiyor artik, cunku o gunku koli
    sayisina gore degisiyordu ve baslikta gereksiz gorunuyordu.
    """
    from tracking.db import status_label

    cutoff = (timez.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    parcels = [p for p in db.list_parcels(channel="FEDEX", limit=500)
              if str(p.get("shipment_date") or "")[:10] >= cutoff]
    if not parcels:
        return True, "son 7 günde fedex kolisi yok"

    satirlar = ["📋 *FedEx — Daily Summary (Last 7 Days)*", ""]
    for p in parcels:
        tn = str(p.get("tracking_no") or "").strip()
        durum = status_label(p.get("status") or "", "en")
        shipped = _format_date(p.get("shipment_date"))
        satirlar += [f"*{tn}* — {durum} (created {shipped})",
                    fedex_tracking_url(tn), ""]
    return send_text("\n".join(satirlar).rstrip(),
                     chat_id=chat_id or config.OPENWA_FEDEX_CHAT_ID,
                     link_preview=False)


def is_eligible_date(parcel: dict, kind: str, start_date: str = "") -> bool:
    """Kaydın başlangıç tarihinden (OPENWA_START_DATE) sonra olup olmadığını denetler."""
    cutoff = start_date or config.OPENWA_START_DATE
    if not cutoff:
        return True

    if kind == "delivery":
        del_date = str(parcel.get("delivered_date") or "").strip()
        if del_date and del_date[:10] < cutoff:
            return False
        last_evt = str(parcel.get("last_event_at") or "").strip()
        if last_evt and last_evt[:10] < cutoff and not del_date:
            return False
        return True

    if kind in ("problem", "returned"):
        last_evt = str(parcel.get("last_event_at") or "").strip()
        if last_evt and last_evt[:10] < cutoff:
            return False
        return True

    return True


def _is_weekend() -> bool:
    """Hafta sonu mu (Cumartesi/Pazar), Europe/Istanbul saatine göre."""
    return timez.now().weekday() >= 5


def handle_tracker_event(db: ShipmentsDB, event: dict) -> None:
    """Tracker durum değişikliği bildirdiğinde çağrılır."""
    if str(config.OPENWA_ENABLED).strip() != "1":
        return

    # FedEx ANA GLS grubuna HALA girmez (kullanici karari 2026-08-31: "wp
    # bildirimi haric") — ama 2026-09-01'den beri KENDI grubuna bildiriyor.
    # Grup tanimli degilse hic gonderilmez; ana gruba SIZMASI mumkun degil,
    # cunku bu dal her durumda burada biter.
    if (event.get("channel") or "").strip().upper() == "FEDEX":
        _handle_fedex_event(db, event)
        return

    tn = event.get("tracking_no")
    if not tn:
        return

    parcel = db.get(tn)
    if not parcel:
        return

    new_status = event.get("new_status") or event.get("status") or parcel.get("status")

    # 1) Teslimat bildirimi
    if new_status == "delivered":
        if parcel.get("wa_delivered_at"):
            return
        if not is_eligible_date(parcel, kind="delivery"):
            log.debug("Koli %s geçmiş tarihli teslimat — WhatsApp atlandı", tn)
            return
        ok, detail = notify_delivery(parcel)
        if ok:
            db.mark_wa_delivered(tn)

    # 2) Sorun bildirimi
    elif new_status == "exception":
        if parcel.get("wa_problem_at"):
            return
        if not is_eligible_date(parcel, kind="problem"):
            log.debug("Koli %s geçmiş tarihli sorun — WhatsApp atlandı", tn)
            return
        if _is_weekend():
            log.debug("Koli %s hafta sonu — sorun bildirimi atlandı", tn)
            return
        ok, detail = notify_problem(parcel)
        if ok:
            db.mark_wa_problem(tn)

    # 3) İade bildirimi (sorundan tamamen ayrı özel mesaj)
    elif new_status == "returned":
        if parcel.get("wa_returned_at"):
            return
        if not is_eligible_date(parcel, kind="returned"):
            log.debug("Koli %s geçmiş tarihli iade — WhatsApp atlandı", tn)
            return
        if _is_weekend():
            log.debug("Koli %s hafta sonu — iade bildirimi atlandı", tn)
            return
        ok, detail = notify_returned(parcel)
        if ok:
            db.mark_wa_returned(tn)


def _handle_fedex_event(db: ShipmentsDB, event: dict) -> None:
    """FedEx durum degisikligi -> FedEx grubuna mesaj (ana gruba DEGIL).

    "Olusturuldu" burada bildirilmez: o mesaj koli gorseli PDF'i uretilince
    EK ile gidiyor (bkz. `notify_fedex_created`), iki kez duyurulmasin.
    """
    if not config.OPENWA_FEDEX_CHAT_ID:
        return
    tn = event.get("tracking_no")
    parcel = db.get(tn) if tn else None
    if not parcel:
        return
    new_status = event.get("new_status") or event.get("status") or parcel.get("status")
    if new_status in ("created", "cancelled"):
        return

    # ESKI KAYIT KORUMASI — GLS dalindakiyle AYNI (`is_eligible_date`).
    # 2026-09-03'e kadar bu dal korumaya HIC ugramiyordu: FedEx olaylari
    # yukarida erken `return` ile buraya sapiyor ve `handle_tracker_event`
    # icindeki kontrollere ulasmiyordu. Sonucu olculdu — 2025 tarihli 285 koli
    # ilk taramada "delivered" olunca TEK SAATTE 207 mesaj gitti (kullanici
    # bildirdi 2026-09-03). Statu "delivered" ise teslim tarihine, degilse son
    # olaya bakilir; ikisi de `OPENWA_START_DATE` oncesiyse susulur.
    kind = "delivery" if new_status == "delivered" else "problem"
    if not is_eligible_date(parcel, kind=kind):
        log.debug("FedEx %s: %s öncesi kayıt — WhatsApp atlandı", tn,
                  config.OPENWA_START_DATE)
        return

    notify_fedex_status(parcel)
