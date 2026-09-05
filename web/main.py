"""GLS Gonderi Takip Sistemi - FastAPI web uygulamasi.

Calistir:
    # Mock server (baska terminalde):
    uvicorn gls_api.mock_server:app --port 8788
    # Panel:
    uvicorn web.main:app --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import i18n
import timez
from erp.sync import sync as erp_sync
from gls_api import config
from web import deps, seed
from web.routers import (dashboard, labels, tracking as tracking_router, pod,
                         problems, archive, auth as auth_router, lang as lang_router,
                         dispatch as dispatch_router, settings as settings_router,
                         reports)
from notify import alert as tracker_alert
from notify.scheduler import DailyDigest
from settings import apply as settings_apply
from tracking.scheduler import Tracker, hours_since
from auth.dependencies import NotAuthenticated, require_role
from auth import access, seed as auth_seed
from db.backup import run_backup

_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

# `docker logs` KONTEYNER OMRUNE baglidir: bir deploy imaji degistirip
# konteyneri yeniden olusturdugunda (`docker compose up -d`) o ana kadarki
# TUM konsol gecmisi kaybolur. Bir arizanin (GLS grubunun basarisiz olmasi
# gibi) izini SONRADAN aramak icin `docker logs` yetersiz kaldi (kullanici
# bildirdi, 2026-08-27 — "dunku loglar" birden fazla deploy'da silinmisti).
# `~/.gls_pod` zaten kalici bir bind-mount (bkz. docker-compose.yml
# `./data:/home/appuser/.gls_pod`) — log dosyasi da oraya yazilir, boylece
# konteyner yeniden olusturulsa bile gecmis kalir. 10 MB x 5 dosya donen.
_log_dir = Path.home() / ".gls_pod" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    _log_dir / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
logging.getLogger().addHandler(_file_handler)
# fpdf2'nin font altkumeleyicisi her POD belgesi icin ~50 satir INFO basiyor.
# Tarayici artik arka planda toplu POD arsivledigi icin bu tek basina log'u
# doldurup gercek uyarilari goze gorunmez yapiyor.
logging.getLogger("fontTools").setLevel(logging.WARNING)
logger = logging.getLogger("web.main")


def _check_readiness():
    issues = config.readiness_check()
    if issues:
        logger.warning(
            "MODE=%s ama %d GLS kimlik ayarı eksik/mock: %s",
            config.MODE, len(issues), "; ".join(issues),
        )


def _send_tracker_alert(health: dict) -> None:
    """Tarama ariza uyarisini gonderir (Tracker'a disaridan verilir).

    Alicilar gunluk ozetle ayni listedir ama `NOTIFY_ENABLED`e BAKILMAZ: ozet
    bir tercihtir, sistemin oldugunu duymak degil. Alici hic yoksa
    `notify/alert.py` zaten aga cikmadan doner.
    """
    tracker_alert.run(health, config.notify_recipients())


def _on_tracker_change(event: dict) -> None:
    """Durum degisikligi oldugunda WhatsApp (OpenWA) bildirimlerini tetikler."""
    from notify import whatsapp as wa_notify
    try:
        wa_notify.handle_tracker_event(deps.get_shipments_db(), event)
    except Exception:
        logger.exception("WhatsApp bildirim hatasi: %s", event.get("tracking_no"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SIRA ONEMLI: panelden kaydedilmis ayarlar `.env`'i ezer. Bu cagri once
    # gelmezse hem readiness hem tracker eski (.env) degerleri okur.
    settings_apply.apply(deps.get_settings_store())

    _check_readiness()

    # Ilk aciliste demo seed
    seed.seed_if_empty()
    auth_seed.ensure_admin(deps.get_auth_db())

    # Arka plan tarayici. POD cekimi disaridan verilir: `tracking/` katmani
    # `web/`'i import etmez, arsivleme kurallari (klasor, belge uretimi) `web`de.
    # `erp_syncer` de disaridan — testler ve CLI canli MSSQL'e uzanmasin.
    tracker = Tracker(db=deps.get_shipments_db(),
                      interval_minutes=config.TRACKER_INTERVAL_MINUTES,
                      on_change=_on_tracker_change,
                      pod_fetcher=pod.archive_pod,
                      alert_sender=_send_tracker_alert,
                      erp_syncer=erp_sync,
                      erp_sync_minutes=config.ERP_SYNC_INTERVAL_MINUTES)
    tracker.start()
    app.state.tracker = tracker

    # Gunluk ozet. Ayarlardan gelen degerlerle kurulur (apply yukarida calisti);
    # NOTIFY_ENABLED kapaliyken `start()` zamanlayiciyi acar ama IS EKLEMEZ.
    digest = DailyDigest(db=deps.get_shipments_db(),
                         hours=config.notify_hours(),
                         recipients=config.notify_recipients(),
                         stale_days=config.NOTIFY_STALE_DAYS,
                         enabled=config.NOTIFY_ENABLED == "1",
                         panel_url=config.NOTIFY_PANEL_URL,
                         inquiry_enabled=config.GLS_INQUIRY_ENABLED == "1",
                         inquiry_recipients=config.gls_inquiry_recipients(),
                         inquiry_wait_days=config.GLS_INQUIRY_WAIT_DAYS)
    digest.start()
    app.state.digest = digest

    # Gunluk otomatik yedek: 3 SQLite dosyasi (shipments/addressbook/auth) TEK
    # diskte yasiyor, oncesinde HICBIR otomatik yedek yoktu (guvenlik denetimi,
    # 2026-08-24). `settings.db` da eklendi — dorduncusu unutulmasin.
    from address_book.db import DEFAULT_DB_PATH as ADDRESS_BOOK_DB
    from auth.db import DEFAULT_DB as AUTH_DB
    from settings.store import DEFAULT_DB as SETTINGS_DB
    from tracking.db import DEFAULT_DB as SHIPMENTS_DB

    backup_dir = Path.home() / ".gls_pod" / "backups"
    backup_sources = [SHIPMENTS_DB, ADDRESS_BOOK_DB, AUTH_DB, SETTINGS_DB]

    async def _run_backup_job():
        try:
            await asyncio.to_thread(run_backup, backup_sources, backup_dir)
        except Exception:                        # noqa: BLE001 — yedek hatasi uygulamayi dusurmemeli
            logger.exception("Gunluk DB yedegi basarisiz")

    backup_scheduler = AsyncIOScheduler(timezone=timez.TZ)
    backup_scheduler.add_job(_run_backup_job, "cron", hour=3, minute=30, id="daily_db_backup")
    backup_scheduler.start()
    app.state.backup_scheduler = backup_scheduler

    yield

    backup_scheduler.shutdown(wait=False)
    digest.shutdown()
    tracker.shutdown()


app = FastAPI(title="GLS Gönderi Takip Sistemi", lifespan=lifespan)


@app.middleware("http")
async def _set_language(request: Request, call_next):
    """Istek basina dili kurar; `t()` bundan sonra her yerde dogru dili verir.

    Deger bir `ContextVar`e yazilir (bkz. `i18n/__init__.py`) — boylece sablon,
    Excel uretici ve mail sablonu `request` gormeden dogru dili bulur.
    """
    i18n.set_current(request.cookies.get(i18n.COOKIE, ""))
    response = await call_next(request)

    # Tarayici korumalari. Panel POD gorüntüleri ve etiket PDF'leri servis ediyor:
    # nosniff olmadan tarayici bir dosyayi HTML sanip calistirabilir. Cerezler
    # zaten SameSite=Lax (bkz. auth/dependencies.py) — frame-options onu tamamlar.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    # CSP: `'unsafe-inline'`/`'unsafe-eval'` BILEREK var — Alpine.js `x-data`
    # ifadelerini `Function()` ile calistiriyor, sablonlarda da coklu satir-ici
    # `<script>` bloklari var (bkz. detail_drawer.html). Nonce'a gecmek TUM
    # sablonlari degistirmeyi gerektirir; bu haliyle CSP bir XSS FILTRESI
    # DEGIL ama kaynaklari kendi origin'imize kilitler, enjekte edilen bir
    # script'in DIS bir sunucuya veri sizdirmasini (`connect-src`) ve sayfayi
    # baska yerden calistirmasini (`frame-ancestors`) engeller.
    #
    # Tum on yuz varliklari (Tailwind/htmx/Alpine/Chart.js) artik
    # `web/static/vendor`'dan YERELDEN sunuluyor — dis CDN izinleri kaldirildi
    # (bkz. base.html: ic agda CDN erisimi donmalara yol aciyordu, 2026-09-03).
    response.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ))
    return response


def _return_path(request: Request) -> str:
    """Giristen sonra kullanicinin donecegi adres.

    HTMX isteginde `request.url.path` KISMI bir adrestir (`/tracking/rows`) ve
    oraya donen kullanici sablonsuz, ciplak bir `<tr>` yigini gorur — beyaz
    ekranda alt alta takip numaralari (kullanici ekran goruntusu, 2026-08-18).
    Tarayicinin gercekte durdugu sayfa `HX-Current-URL` basligindadir; sorgu
    dizesiyle birlikte alinir ki kullanicinin suzgecleri de korunsun.

    Baslik da istekle gelir, yani KULLANICI GIRDISIDIR: baska bir konaga isaret
    ediyorsa yolu da kullanilmaz (`//kotu.site/giris` yalnizca yola bakan bir
    kurala `/giris` diye sizardi). `web/routers/auth.py:_safe_next` ayni
    korumayi formdan gelen deger icin tekrar yapar — iki kapi da kilitli kalsin.
    """
    parts = urlsplit(request.headers.get("HX-Current-URL") or "")
    if not parts.path or (parts.netloc and parts.netloc != request.url.netloc):
        return request.url.path
    path = f"{parts.path}?{parts.query}" if parts.query else parts.path
    return path if path.startswith("/") and not path.startswith("//") else "/"


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated):
    """Oturum bitince giris sayfasina goturur.

    HTMX icin AYRI yol: kismi istekler (tablo satirlari, ilerleme cubugu) 303'u
    kendi izler ve donen GIRIS SAYFASINI hedef alana basar — tablonun ortasinda
    kucuk bir giris formu belirir, sayfanin geri kalani eski haliyle durur.
    `HX-Redirect` govdeyi degil TARAYICIYI tasir; kullanici normal giris
    sayfasini gorur.
    """
    target = f"/auth/login?next={quote(_return_path(request), safe='/')}"
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


# Statik dosyalar (opsiyonel — sablonlar CDN kullaniyor)
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# Auth router — giris sayfasi kimlik dogrulama gerektirmez
app.include_router(auth_router.router)

# Dil anahtari da korumasiz: giris sayfasinda da gorunuyor.
app.include_router(lang_router.router)

# Diger tum router'lar oturum acilmasini gerektirir (Depends router seviyesinde
# uygulaniyor ki tek tek endpoint'lere eklemek unutulmasin). Hangi sayfanin
# hangi role acik oldugu `auth/access.py`de — menuyu suzen `can()` de ayni
# sozlugu okur, yani menude olmayan bir sayfa URL'den de acilmaz.
def _guard(path: str):
    return [Depends(require_role(*access.roles_for(path)))]


app.include_router(dashboard.router, dependencies=_guard("/"))
app.include_router(labels.router, dependencies=_guard("/labels"))
app.include_router(dispatch_router.router, dependencies=_guard("/dispatch"))
app.include_router(tracking_router.router, dependencies=_guard("/tracking"))
app.include_router(pod.router, dependencies=_guard("/pod"))
app.include_router(problems.router, dependencies=_guard("/problems"))
app.include_router(archive.router, dependencies=_guard("/archive"))
app.include_router(reports.router, dependencies=_guard("/reports"))
# Ayarlar: router'in kendi icinde bolum bazli rol kontrolu var (bkz. _guard).
app.include_router(settings_router.router, dependencies=_guard("/settings"))


@app.get("/health")
async def health():
    """Ayakta olmak yetmez — tarama da calisiyor olmali.

    Uzun suredir basarili tur yoksa 503 doner. Uygulama acilir ama isini
    yapmazken "ok" demek sessiz arizanin ta kendisidir; docker/izleme bunu
    goremezdi. Esik uyari maliyle AYNI (`TRACKER_ALERT_HOURS`) — iki kanalin
    farkli sey soylemesi teshisi zorlastirirdi.

    Hic tur donmemis olmasi ariza degil: acilisin ilk dakikalari.
    """
    # DB erisimi AYRI is parcaciginda: tarayici turu SQLite'i mesgul ettiginde
    # (yuzlerce yazma) buradaki okumalar saniyelerce surebiliyor. `async def`
    # icinde DOGRUDAN cagrilirsa o sure boyunca TUM event loop (yani her
    # kullanicinin her istegi) donuyordu (kullanici 2026-09-03: "yine
    # kilitleniyor"). Docker healthcheck'i de ayni pencerede dusuyordu.
    body = await asyncio.to_thread(_health_body)
    return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)


def _health_body() -> dict:
    db = deps.get_shipments_db()
    info = db.health()
    stale = hours_since(info.get("last_success_at") or info.get("last_run_at"))
    limit = config.TRACKER_ALERT_HOURS
    ok = limit <= 0 or stale is None or stale < limit
    return {
        "status": "ok" if ok else "degraded",
        "parcels": db.total(),
        "tracker": {
            "last_run_at": info.get("last_run_at"),
            "last_success_at": info.get("last_success_at"),
            "last_error": info.get("last_error"),
            "stale_hours": None if stale is None else round(stale, 1),
            "threshold_hours": limit,
            # `last_error` bir sonraki basarili turda silinir — bir grup
            # basarisiz olsa da tur genel olarak "basarili" sayilabildigi icin
            # (bkz. Tracker.tick) bu tekil alan izi kaybedebiliyordu. Son 7
            # gunun KALICI hata gecmisi burada: bir tur kismen basarisiz olsa
            # bile operator "1 dakika once tarandi" derken hangi grubun
            # atlandigini gorebilsin (kullanici bildirdi, 2026-08-27).
            "recent_tick_errors": db.recent_tick_errors(limit=20),
        },
    }
