# -*- coding: utf-8 -*-
"""Ayarlar sayfasindaki "Kimlik Dogrula" dugmesi — MAIL GONDERMEZ.

Token alma kodu burada DEGIL `notify/mail.py`'de: gonderim yolu ile dogrulama
yolu ayni auth kodunu kullansin, ikisi birbirinden ayrisip curumesin. Bu modul
yalnizca sonucu Ayarlar sayfasinin bekledigi Turkce cumleye cevirir.

Neden burada gonderim yok: bir ayar formundaki "test maili" dugmesi kime, hangi
sablonla, POD eki var mi sorularinin hepsini cevaplamak zorunda kalirdi. Gercek
gonderim Bildirimler sekmesindeki "Özeti şimdi gönder" dugmesinin isi.
"""
from __future__ import annotations

import i18n

from gls_api import config
from notify import mail as transport

configured = transport.configured


def check_credentials() -> tuple[bool, str]:
    token, detail = transport.get_token()
    if not token:
        return False, detail
    if not config.MAIL_SENDER:
        return True, i18n.t("check.mail_ok_no_sender")
    return True, i18n.t("check.mail_ok", sender=config.MAIL_SENDER)
