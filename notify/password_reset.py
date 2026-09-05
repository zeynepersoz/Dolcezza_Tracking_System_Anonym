# -*- coding: utf-8 -*-
"""Sifre sifirlama baglantisi maili.

`notify/alert.py` deseni: govde SABLONSUZ ve EKSIZ. Kullanici zaten panele
giremiyor; bu mailin gitmesi bir Excel sablonunun okunabilmesine bagli olamaz.

Dil `DOC_LANG` DEGIL, istegin o anki dili: kullanici giris ekranini hangi
dilde gorup "sifremi unuttum"a bastiysa mail de o dilde gelmeli. (Gunluk ozet
farkli — o kimsenin istegiyle degil, zamanlayiciyla uretiliyor.)

Alici adresi HICBIR YERE LOGLANMAZ (bkz. notify/mail.py:148).
"""
from __future__ import annotations

import html as html_escape
import logging

import i18n
from notify import mail

log = logging.getLogger("notify.password_reset")


def render(username: str, link: str) -> tuple[str, str]:
    """(konu, html). Aga ve DB'ye dokunmaz — test govdeyi dogrudan denetleyebilir."""
    subject = i18n.t("reset.mail.subject")
    greeting = html_escape.escape(i18n.t("reset.mail.greeting", username=username))
    body = html_escape.escape(i18n.t("reset.mail.body"))
    button = html_escape.escape(i18n.t("reset.mail.button"))
    expires = html_escape.escape(i18n.t("reset.mail.expires"))
    ignore = html_escape.escape(i18n.t("reset.mail.ignore"))
    href = html_escape.escape(link, quote=True)
    return subject, (
        '<div style="font-family:system-ui,sans-serif;font-size:14px;color:#0f172a">'
        f"<p>{greeting}</p>"
        f"<p>{body}</p>"
        f'<p><a href="{href}" style="display:inline-block;padding:10px 18px;'
        'background:#0f172a;color:#fff;border-radius:8px;text-decoration:none;'
        f'font-weight:600">{button}</a></p>'
        # Bazi posta istemcileri dugmeyi cizmez; ham adres her zaman gorunur olsun.
        f'<p style="font-size:12px;color:#64748b;word-break:break-all">{href}</p>'
        f'<p style="color:#64748b">{expires}</p>'
        f'<p style="color:#64748b">{ignore}</p>'
        "</div>"
    )


def send(to: str, username: str, link: str) -> tuple[bool, str]:
    subject, body = render(username, link)
    ok, detail = mail.send([to], subject, body)
    # ADRES YAZILMAZ — bu satir hem sunucu gunlugune hem de docker log'a duser.
    log.info("Şifre sıfırlama maili (%s): %s", username,
             "gönderildi" if ok else detail)
    return ok, detail
