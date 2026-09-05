# -*- coding: utf-8 -*-
"""Gunluk ozeti belirlenen saatlerde gonderir.

`tracking/scheduler.py:Tracker` ile ayni sekil: `start()` / `reconfigure()` /
`shutdown()`, `app.state`'te tutulur, `settings/apply.py` calisirken yeniden
yapilandirir. Yeni bir desen icat edilmiyor.

`interval` DEGIL `cron`: istenen sey "her sabah 09:00", "her 24 saatte bir"
degil. `interval` konteyner her yeniden basladiginda saati kaydirirdi.

Saat TEK DEGIL LISTE: teslimat gun boyunca suruyor, sabahki ozet aksama kadar
bayatliyordu. Cron tetigi virgullu saat listesini dogrudan aliyor
(`hour="9,18"`) — ikinci bir is eklemeye gerek yok.
"""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import timez
from notify import digest, pod_chase

log = logging.getLogger("notify.scheduler")

# Konteyner UTC calisir. Kullanici "sabah 9" derken 09:00 UTC'yi degil 09:00
# Istanbul'u kastediyor; saat dilimi verilmezse ozet 12:00'de giderdi.
# Tanim `timez`e tasindi — tek saat dilimi kaynagi.
TZ = timez.TZ

JOB_ID = "daily_digest"
# GLS'e POD sorgusu AYRI istir, ozetin eki degil: alicisi disaridadir ve ozet
# kapaliyken de calisabilmeli. Ayni saatte tetiklenir — kullanici tek bir
# "sabah postasi" bekliyor.
INQUIRY_JOB_ID = "gls_pod_inquiry"
# Kısmi teslimatlar için gün sonu WhatsApp bildirimi (Her gün 18:00)
PARTIAL_DELIVERY_JOB_ID = "partial_deliveries_wa"
# FedEx son 7 gonderi ozeti (Her gun 18:00) — kullanici istegi 2026-09-02:
# "bize gore saat aksam 6 da ... son 7 gonderi statuleri ve linklerinin
# oldugu tek bir mesaj". `TZ` (Europe/Istanbul) uzerinden calisir, ayni
# PARTIAL_DELIVERY_JOB_ID gibi.
FEDEX_SUMMARY_JOB_ID = "fedex_daily_summary_wa"


def _clean_hours(hours) -> list[int]:
    """0-23 arasi, tekrarsiz, sirali. Bosalirsa 09:00 — is sessizce kaybolmasin."""
    return sorted({int(h) for h in hours if 0 <= int(h) <= 23}) or [9]


class DailyDigest:
    def __init__(self, db, hours=(9, 18), recipients=(), stale_days: int = 7,
                 enabled: bool = False, panel_url: str = "",
                 inquiry_enabled: bool = False, inquiry_recipients=(),
                 inquiry_wait_days: int = 7):
        self.db = db
        self.hours = _clean_hours(hours)
        self.recipients = list(recipients)
        self.stale_days = stale_days
        self.enabled = enabled
        self.panel_url = panel_url
        self.inquiry_enabled = inquiry_enabled
        self.inquiry_recipients = list(inquiry_recipients)
        self.inquiry_wait_days = inquiry_wait_days
        self.scheduler = AsyncIOScheduler(timezone=TZ)
        self.last_run_at: str | None = None
        self.last_error: str | None = None
        self.last_detail: str | None = None
        self.last_inquiry_at: str | None = None
        self.last_inquiry_detail: str | None = None

    # ---------- yasam dongusu ----------

    def start(self) -> None:
        self.scheduler.start()
        self._sync_job()

    def shutdown(self) -> None:
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass

    def reconfigure(self, hours=None, recipients=None,
                    stale_days: int | None = None, enabled: bool | None = None,
                    panel_url: str | None = None, inquiry_enabled: bool | None = None,
                    inquiry_recipients=None, inquiry_wait_days: int | None = None) -> None:
        """Ayarlar sayfasindan gelen degisiklikleri CALISIRKEN uygular."""
        old = (self.hours, self.enabled, self.inquiry_enabled)
        if hours is not None:
            self.hours = _clean_hours(hours)
        if recipients is not None:
            self.recipients = list(recipients)
        if stale_days is not None:
            self.stale_days = stale_days
        if enabled is not None:
            self.enabled = enabled
        if panel_url is not None:
            self.panel_url = panel_url
        if inquiry_enabled is not None:
            self.inquiry_enabled = inquiry_enabled
        if inquiry_recipients is not None:
            self.inquiry_recipients = list(inquiry_recipients)
        if inquiry_wait_days is not None:
            self.inquiry_wait_days = inquiry_wait_days
        self._sync_job()
        if old != (self.hours, self.enabled, self.inquiry_enabled):
            log.info("Günlük özet: %s, GLS POD sorgusu: %s, saatler %s",
                     "açık" if self.enabled else "kapalı",
                     "açık" if self.inquiry_enabled else "kapalı",
                     ", ".join(f"{h:02d}:00" for h in self.hours))

    def _sync_job(self) -> None:
        """Isleri acik/kapali durumuna gore ekler, cikarir ya da saatini gunceller.

        Zamanlayici henuz baslamamissa (CLI, testler) sessizce atlanir —
        `Tracker.reconfigure`'daki `scheduler.running` denetiminin ayni gerekcesi.
        """
        if not self.scheduler.running:
            return
        cron_hours = ",".join(str(h) for h in self.hours)
        for job_id, on, func in (
            (JOB_ID, self.enabled, self._tick_wrapper),
            (INQUIRY_JOB_ID, self.inquiry_enabled, self._inquiry_wrapper),
        ):
            job = self.scheduler.get_job(job_id)
            if not on:
                if job:
                    self.scheduler.remove_job(job_id)
            elif job:
                self.scheduler.reschedule_job(job_id, trigger="cron",
                                              hour=cron_hours, minute=0)
            else:
                self.scheduler.add_job(func, "cron", hour=cron_hours, minute=0,
                                       id=job_id)

        # Kısmi teslimat WhatsApp bildirimi: Her gün saat 18:00'de
        from gls_api import config as gls_config
        partial_on = str(gls_config.OPENWA_ENABLED).strip() == "1"
        pjob = self.scheduler.get_job(PARTIAL_DELIVERY_JOB_ID)
        if not partial_on:
            if pjob:
                self.scheduler.remove_job(PARTIAL_DELIVERY_JOB_ID)
        elif pjob:
            self.scheduler.reschedule_job(PARTIAL_DELIVERY_JOB_ID, trigger="cron",
                                          hour=18, minute=0)
        else:
            self.scheduler.add_job(self._partial_delivery_wrapper, "cron",
                                   hour=18, minute=0, id=PARTIAL_DELIVERY_JOB_ID)

        # FedEx gunluk ozeti: Her gun saat 18:00'de, FedEx grubu tanimliysa.
        fedex_on = partial_on and bool(gls_config.OPENWA_FEDEX_CHAT_ID)
        fjob = self.scheduler.get_job(FEDEX_SUMMARY_JOB_ID)
        if not fedex_on:
            if fjob:
                self.scheduler.remove_job(FEDEX_SUMMARY_JOB_ID)
        elif fjob:
            self.scheduler.reschedule_job(FEDEX_SUMMARY_JOB_ID, trigger="cron",
                                          hour=18, minute=0)
        else:
            self.scheduler.add_job(self._fedex_summary_wrapper, "cron",
                                   hour=18, minute=0, id=FEDEX_SUMMARY_JOB_ID)

    # ---------- calisma ----------

    async def _tick_wrapper(self) -> None:
        try:
            await asyncio.to_thread(self.send_now)
        except Exception as exc:          # mail hatasi uygulamayi dusurmemeli
            self.last_error = str(exc)
            log.exception("Günlük özet gönderilemedi")

    def send_now(self) -> tuple[bool, str]:
        """Ozeti hemen gonderir. Ayarlar'daki test dugmesi de bunu cagirir."""
        self.last_run_at = timez.utc_iso()
        ok, detail = digest.run(
            self.db,
            recipients=self.recipients,
            stale_days=self.stale_days,
            panel_url=self.panel_url,
        )
        self.last_detail = detail
        self.last_error = None if ok else detail
        return ok, detail

    async def _inquiry_wrapper(self) -> None:
        try:
            await asyncio.to_thread(self.send_inquiry_now)
        except Exception as exc:
            self.last_inquiry_detail = str(exc)
            log.exception("GLS POD sorgusu gönderilemedi")

    def send_inquiry_now(self) -> tuple[bool, str]:
        """POD'u gelmeyen teslimatlari GLS'e sorar. Ozetten BAGIMSIZ calisir.

        Ic ekip bilgi (CC) olarak eklenir: disariya giden bir yazismanin kopyasi
        gonderende kalmali, kullanici elle yazarken de ekibi CC'liyordu.
        """
        self.last_inquiry_at = timez.utc_iso()
        ok, detail = pod_chase.run(
            self.db,
            wait_days=self.inquiry_wait_days,
            recipients=self.inquiry_recipients,
            cc=self.recipients,
        )
        self.last_inquiry_detail = detail
        return ok, detail

    async def _partial_delivery_wrapper(self) -> None:
        try:
            await asyncio.to_thread(self.send_partial_deliveries_now)
        except Exception as exc:
            log.exception("Kısmi teslimat WhatsApp bildirimi gönderilemedi")

    def send_partial_deliveries_now(self) -> tuple[int, list[str]]:
        """Kısmi teslimatları tarar ve WhatsApp mesajlarını gönderir."""
        from notify import partial_delivery
        return partial_delivery.run_partial_deliveries_daily(self.db)

    async def _fedex_summary_wrapper(self) -> None:
        try:
            await asyncio.to_thread(self.send_fedex_summary_now)
        except Exception:
            log.exception("FedEx günlük özeti gönderilemedi")

    def send_fedex_summary_now(self) -> tuple[bool, str]:
        """Son 7 FedEx gonderisinin ozetini FedEx grubuna gonderir."""
        from notify import whatsapp
        return whatsapp.notify_fedex_daily_summary(self.db)
