# -*- coding: utf-8 -*-
"""Microsoft Graph mail tasiyicisi — TEK gonderim yolu.

`settings/mail.py` (Ayarlar'daki "Kimlik Dogrula" dugmesi) buradaki
`get_token()`'i kullanir. Boylece dogrulama dugmesi gercek gonderim yolunun auth
kodunu sinar; iki ayri token uygulamasi birbirinden ayrisip curuyemez.

Donus bicimi `gls_api/providers.py`'deki `_check_*` fonksiyonlariyla ayni:
`(ok, Turkce aciklama)`. Ayarlar sayfasi ayni sonuc sablonunu kullanabilsin diye.
"""
from __future__ import annotations

import base64
import logging

import requests

import i18n
from gls_api import config

log = logging.getLogger("notify.mail")

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPE = "https://graph.microsoft.com/.default"
SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}
# Graph `sendMail` govdesi 4 MB; base64 %33 sisirdigi icin ham tavan 3 MB.
# Tavan TUM eklerin TOPLAMINA uygulanir — sinir govdenin tamamina ait.
MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024

# Gonderim `REQUEST_TIMEOUT`u KULLANMAZ. O deger GLS'in kucuk JSON istekleri icin
# ayarli (30 sn); 4 MB base64 govdenin yuklenmesi bunu asiyor ve zaman asimi
# "gonderilemedi" der ama Graph maili YINE DE gondermis olabilir — tekrar denemek
# alicilara ikinci kopya atar. O yuzden gonderime genis pay verilir.
SEND_TIMEOUT = 180


def configured() -> bool:
    return all((config.MAIL_TENANT_ID, config.MAIL_CLIENT_ID, config.MAIL_CLIENT_SECRET))


def get_token() -> tuple[str | None, str]:
    """`client_credentials` ile uygulama belirteci alir.

    Kimseye mail gondermez — yalnizca tenant + client id + secret uclusunun
    ve verilen iznin dogru oldugunu kanitlar.
    """
    if not configured():
        return None, i18n.t("mail.creds_missing")
    try:
        r = requests.post(
            TOKEN_URL.format(tenant=config.MAIL_TENANT_ID),
            data={
                "client_id": config.MAIL_CLIENT_ID,
                "client_secret": config.MAIL_CLIENT_SECRET,
                "scope": SCOPE,
                "grant_type": "client_credentials",
            },
            timeout=config.REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return None, i18n.t("mail.no_connection", detail=exc)

    if r.status_code == 200:
        token = r.json().get("access_token")
        if token:
            return token, i18n.t("mail.authenticated")

    # Azure hatayi govdede `error_description` ile aciklar; ilk satiri yeterlidir.
    try:
        detail = (r.json().get("error_description") or "").splitlines()[0]
    except Exception:
        detail = r.text[:200]
    return None, i18n.t("mail.rejected", status=r.status_code, detail=detail)


def send(to: list[str], subject: str, html: str,
         attachments: list[tuple[str, bytes]] | None = None,
         cc: list[str] | None = None) -> tuple[bool, str]:
    """HTML mail gonderir. Alici yoksa ya da mock moddaysa aga hic cikmaz.

    `attachments` = [(dosya adi, icerik), ...]. Graph `sendMail` govdesi toplam
    4 MB ile sinirli; buyuk ek 413 doner, o yuzden once boyut denetlenir.

    `cc` disariya giden yazismalar icin: GLS'e POD sorulurken ic ekip de kopyayi
    gorsun — kullanici mektubu elle yazarken de boyle yapiyordu.
    """
    recipients = [a.strip() for a in to if a and a.strip()]
    if not recipients:
        return False, i18n.t("mail.no_recipients")
    if not config.MAIL_SENDER:
        return False, i18n.t("mail.no_sender")
    # Bir gelistirme makinesi ya da CI gercek posta kutusuna mail atmasin.
    if config.IS_MOCK:
        log.info("mock mod — mail gönderilmedi (%d alıcı)", len(recipients))
        return True, i18n.t("mail.mock", count=len(recipients))

    token, detail = get_token()
    if not token:
        return False, detail

    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html},
        "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
    }
    copies = [a.strip() for a in (cc or []) if a and a.strip()]
    if copies:
        message["ccRecipients"] = [{"emailAddress": {"address": a}} for a in copies]
    # `from` BILEREK GONDERILMEZ. Denendi (2026-08-05, canli): govdeye
    # `from.emailAddress.name` konuldugunda Graph 202 doner ama Exchange adi yok
    # sayip posta kutusunun M365'teki gorunen adini basar. Gonderen adi ancak
    # yonetici merkezinden degisir — burada ayar sunmak yalan olurdu.
    if attachments:
        total = sum(len(content) for _, content in attachments)
        if total > MAX_ATTACHMENT_BYTES:
            return False, i18n.t("mail.attachments_too_big", size=total // 1024,
                                 limit=MAX_ATTACHMENT_BYTES // 1024)
        message["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": MIME.get(name.rsplit(".", 1)[-1].lower(),
                                    "application/octet-stream"),
            "contentBytes": base64.b64encode(content).decode("ascii"),
        } for name, content in attachments]
    payload = {"message": message, "saveToSentItems": True}
    try:
        r = requests.post(
            SEND_URL.format(sender=config.MAIL_SENDER),
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=SEND_TIMEOUT,
        )
    except requests.Timeout:
        # TEKRAR DENEME ONERILMEZ: Graph istegi almis ve gondermis olabilir.
        log.warning("sendMail zaman asimi (%d alıcı) — sonuc bilinmiyor",
                    len(recipients))
        return False, i18n.t("mail.timeout")
    except requests.RequestException as exc:
        return False, i18n.t("mail.send_failed", detail=exc)

    # Graph sendMail 202 doner, 200 DEGIL.
    if r.status_code == 202:
        # ADRESLER LOGLANMAZ, yalnizca sayi.
        log.info("Mail gönderildi (%d alıcı)", len(recipients))
        return True, i18n.t("mail.sent", count=len(recipients))
    return False, i18n.t("mail.send_failed_status", status=r.status_code, detail=r.text[:200])
