# -*- coding: utf-8 -*-
"""Bildirim paketi — gunluk ic ekip ozeti.

  mail.py       Microsoft Graph tasiyicisi (token + sendMail)
  digest.py     veriyi topla -> HTML uret -> gonder
  scheduler.py  DailyDigest — her gun belirlenen saatte calisir
"""

# Ozet maili ve Excel eki HER ZAMAN Ingilizce uretilir; panelin dilinden
# (`config.UI_LANG`) bagimsizdir. Bu ciktilar kurum disina — Avrupa'daki
# magazalara ve satis temsilcilerine — gidiyor. Dil bir istekten degil
# zamanlayicidan geldigi icin acikca kurulmali (cerez yok).
DOC_LANG = "en"
