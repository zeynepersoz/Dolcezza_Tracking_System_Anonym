# -*- coding: utf-8 -*-
"""Kaydedilen ayarları çalışan uygulamaya iter — TEK giriş noktası.

Bağımlılık bilerek ters çevrilmiştir: `gls_api.config` bu paketi asla import etmez,
ayarlar config'e *itilir*. Böylece import döngüsü olmaz ve `config.X` her erişimde
disk okumaz.

Dört önbellek birden düşürülmezse kullanıcı "kaydettim ama değişmedi" der:
  1. `config._OVERRIDES`  — değerlerin kendisi
  2. `providers._shipit/_tt/_nl/_nl_tt` — kimliği `__init__`'te okuyan istemciler
  3. `Tracker` aralığı + tur başına tavanlar + ERP senkron aralığı
     (hepsi APScheduler işine gömülü)
  4. `DailyDigest` saati + alıcıları (APScheduler cron işine gömülü)
`ERPClient` listede yok çünkü her çağrıda yeniden kuruluyor (erp/sync.py).
"""
from __future__ import annotations

import logging

from gls_api import config, providers

log = logging.getLogger("settings")


def apply(store, tracker=None, digest=None) -> None:
    values = store.typed()
    config.apply_overrides(values)
    providers.reset_clients()
    if tracker is not None:
        tracker.reconfigure(
            interval_minutes=config.TRACKER_INTERVAL_MINUTES,
            max_per_tick=config.TRACKER_MAX_PER_TICK,
            max_pod_per_tick=config.TRACKER_MAX_POD_PER_TICK,
            erp_sync_minutes=config.ERP_SYNC_INTERVAL_MINUTES,
        )
    if digest is not None:
        digest.reconfigure(
            hours=config.notify_hours(),
            recipients=config.notify_recipients(),
            stale_days=config.NOTIFY_STALE_DAYS,
            enabled=config.NOTIFY_ENABLED == "1",
            panel_url=config.NOTIFY_PANEL_URL,
            inquiry_enabled=config.GLS_INQUIRY_ENABLED == "1",
            inquiry_recipients=config.gls_inquiry_recipients(),
            inquiry_wait_days=config.GLS_INQUIRY_WAIT_DAYS,
        )
    # DEGERLER LOGLANMAZ — aralarinda sifreler var.
    ignored = config.ignored_override_keys(values)
    log.info("Ayarlar uygulandi: %d deger%s", len(values),
             f" ({len(ignored)} tanesi mock modda atlandi)" if ignored else "")
