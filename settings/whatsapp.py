# -*- coding: utf-8 -*-
"""WhatsApp kimlik bilgileri — v1'de yalnızca saklama.

Test yolu bilerek yok. İki sebep:
  1. "WhatsApp API" tek bir ürün değil: Meta Cloud API, Twilio ve 360dialog'un
     kimlik başlıkları, uç noktaları ve alan adları birbirinden tamamen farklı.
     Sağlayıcı seçilmeden yazılacak kod üçünden ikisi için yanlış olur.
  2. Her "test" gerçek bir telefona giden gerçek bir mesajdır — bir ayar formunun
     yan etkisi olamaz.

Sağlayıcı netleştiğinde gönderim burada, bildirim özelliğiyle birlikte yazılacak.
"""
from __future__ import annotations

import i18n
from gls_api import config

PROVIDER_LABEL = {
    "openwa": "OpenWA",
    "meta": "Meta Cloud API",
    "twilio": "Twilio",
    "360dialog": "360dialog",
}


def configured() -> bool:
    if config.WA_PROVIDER == "openwa":
        return bool(config.OPENWA_URL and config.OPENWA_SESSION_ID and config.OPENWA_API_KEY and config.OPENWA_CHAT_ID)
    return bool(config.WA_PROVIDER and config.WA_ACCOUNT_ID and config.WA_TOKEN)


def status() -> tuple[bool, str]:
    """Arayuzde gosterilecek durum — AGA CIKMAZ."""
    if not config.WA_PROVIDER:
        return False, i18n.t("settings.wa_no_provider")
    label = PROVIDER_LABEL.get(config.WA_PROVIDER, config.WA_PROVIDER)
    if not configured():
        return False, i18n.t("settings.wa_incomplete", label=label)
    return True, i18n.t("settings.wa_ready", label=label)
