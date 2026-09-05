# -*- coding: utf-8 -*-
"""Tarama uzun suredir basarisizsa gonderilen ariza uyarisi.

Gunluk ozetten (`notify/digest.py`) AYRI bir istir ve bilerek oyle:

  * Ozet KAPALIYKEN de gitmeli. Uyari bir tercih degil, sistemin sessizce
    olmedigini bilmenin tek yolu; kullanicinin ozeti kapatmis olmasi arizayi
    da duymak istemedigi anlamina gelmez.
  * Ozet cron ile gunde birkac kez calisir; ariza haberi o kadar bekleyemez.
    Bu mail tarama turunun kendi icinden tetiklenir (bkz. Tracker.check_health).
  * Govdesi minicik ve EKSIZ: ariza aninda 1 MB'lik Excel uretmeye calismak,
    ayni anda onun da patlayabilecegi anlamina gelir — haber hic gitmezdi.

Dil `DOC_LANG`: gunluk ozetle ayni gerekce (mail panelin cerezini gormez).
"""
from __future__ import annotations

import html as html_escape
import logging

import i18n
from notify import DOC_LANG, mail

log = logging.getLogger("notify.alert")


def render(health: dict) -> tuple[str, str]:
    """(konu, html). Veritabanina ve aga dokunmaz — test sablonsuz dogrulayabilir."""
    hours = health.get("stale_hours", "?")
    with i18n.use(DOC_LANG):
        subject = i18n.t("alert.tracker.subject", hours=hours)
        rows = [
            (i18n.t("alert.tracker.last_success"), health.get("last_success_at")),
            (i18n.t("alert.tracker.last_run"), health.get("last_run_at")),
            (i18n.t("alert.tracker.last_error"), health.get("last_error")),
        ]
        body = "".join(
            f'<tr><td style="padding:4px 16px 4px 0;color:#64748b">{html_escape.escape(label)}</td>'
            f'<td style="padding:4px 0"><code>{html_escape.escape(str(value or "—"))}</code></td></tr>'
            for label, value in rows
        )
        head = html_escape.escape(i18n.t("alert.tracker.headline", hours=hours))
        foot = html_escape.escape(i18n.t("alert.tracker.footer"))
    return subject, (
        '<div style="font-family:system-ui,sans-serif;font-size:14px;color:#0f172a">'
        f'<p><strong>{head}</strong></p>'
        f'<table style="border-collapse:collapse">{body}</table>'
        f'<p style="color:#64748b">{foot}</p>'
        "</div>"
    )


def run(health: dict, recipients: list[str]) -> tuple[bool, str]:
    """Uyariyi gonderir. Alici yoksa aga hic cikmaz."""
    clean = [a.strip() for a in recipients if a and a.strip()]
    if not clean:
        return False, i18n.t("mail.no_recipients")
    subject, body = render(health)
    ok, detail = mail.send(clean, subject, body)
    log.warning("Tarama arıza uyarısı: %s saat, %d alıcı — %s",
                health.get("stale_hours"), len(clean),
                "gönderildi" if ok else detail)
    return ok, detail
