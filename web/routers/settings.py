# -*- coding: utf-8 -*-
"""Ayarlar ekrani — panelden duzenlenen, settings.db'de duran yapilandirma.

Sekmeler sunucu tarafindadir (`/settings/mssql`, `/settings/gls`, ...): derin
baglanti ve geri tusu calisir, bir bolumdeki dogrulama hatasi digerlerini
bloklamaz, HTMX hedefleri cakismaz.

**Test dugmeleri KAYDEDILMIS degerlerle calisir**, formdaki yaziyla degil:
`providers.doctor()` ve `ERPClient()` degerleri `config`ten okur; onlara form
verisi gecirmek her yapiciyi cogaltmak demekti. Bu yuzden once "Kaydet".
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import i18n
from auth import access
from auth.db import ROLES
from auth.dependencies import client_ip, require_role
from erp.client import ERPClient, ERPError, safe_identifier
from gls_api import config, providers
from settings import apply as settings_apply
from settings import mail, whatsapp
from settings.schema import BY_SLUG, TABS
from web.templating import templates
from web import deps, paging

log = logging.getLogger(__name__)

# Giris kaydinda tek sayfa. Once son 200 deneme tek seferde ciziliyordu; kayit
# 90 gun saklandigi icin liste zamanla binlerce satira ciktiginda hem eskisi
# gorunmez oluyor hem sayfa sisiyordu.
LOG_PAGE_SIZE = 100

# Router seviyesinde oturum kontrolu: yeni bir uc yanlislikla korumasiz eklenemesin.
# Bolum bazli yetki asagida `_guard()` ile, tek kaynaktan (`Section.roles`).
router = APIRouter(prefix="/settings", tags=["settings"],
                   dependencies=[Depends(require_role(*access.WRITERS))])

# Kullanicilar ve Guvenlik sekmeleri semada degil (ayar degil, kayit yonetimi)
# — yetkileri burada.
ADMIN_TABS = {"users", "security"}
ADMIN_ROLES = ("admin",)


def _roles_for(slug: str) -> tuple[str, ...]:
    return ADMIN_ROLES if slug in ADMIN_TABS else BY_SLUG[slug].roles


def _guard(request: Request, section: str) -> None:
    """Rolu yetmeyeni 403 ile durdurur. `section_page`/`save_section` basinda."""
    role = (getattr(request.state, "user", None) or {}).get("role", "")
    if role not in _roles_for(section):
        raise HTTPException(403, i18n.t("err.forbidden"))


def _visible_tabs(request: Request) -> list[tuple[str, str]]:
    role = (getattr(request.state, "user", None) or {}).get("role", "")
    return [(slug, i18n.t_or(f"settings.{slug}.title", title))
            for slug, title in TABS if role in _roles_for(slug)]


def _base_ctx(request: Request, section: str) -> dict:
    return {
        "request": request,
        "tabs": _visible_tabs(request),
        "section": section,
        "ok": request.query_params.get("ok", ""),
        "error": request.query_params.get("error", ""),
        **deps.template_context(),
    }


@router.get("")
@router.get("/")
async def settings_root(request: Request):
    tabs = _visible_tabs(request)
    if not tabs:
        raise HTTPException(403, i18n.t("err.forbidden"))
    return RedirectResponse(f"/settings/{tabs[0][0]}", status_code=303)


@router.get("/users", response_class=HTMLResponse)
async def users_section(request: Request):
    """Kullanici yonetimi — eskiden /auth/users. POST uclari auth router'da kaldi."""
    _guard(request, "users")
    ctx = _base_ctx(request, "users")
    # Roller `auth/db.py`den: yeni bir rol eklendiginde bu ekran kendiliginden
    # gorsun (elle yazilan liste unutulup rol olusturulamaz kalirdi).
    # Sifre sifirlama maili IKI sarta bagli. Ikisi de burada denetlenir ki
    # yonetici eksigi "sifirlama calismiyor" diye geri donuldugunde degil,
    # kullaniciyi olustururken gorsun.
    ctx.update(users=deps.get_auth_db().list_users(), roles=ROLES,
               mail_ready=mail.configured(),
               panel_url=(config.NOTIFY_PANEL_URL or "").strip())
    return templates.TemplateResponse(request, "settings/users.html", ctx)


@router.get("/security", response_class=HTMLResponse)
async def security_section(request: Request):
    """Giris kaydi + elle IP engelleme. POST uclari auth router'da."""
    _guard(request, "security")
    db = deps.get_auth_db()
    ctx = _base_ctx(request, "security")
    only_failed = bool(request.query_params.get("failed"))
    # `limit=200` tavani kalkti: kayitlar zaten 90 gun saklaniyor, tavan orada.
    # Sayfa penceresi takip listesiyle AYNI yardimcidan (`web/paging.py`).
    total = db.count_login_events(only_failed=only_failed)
    win = paging.window(request, total, LOG_PAGE_SIZE)
    ctx.update(
        events=db.login_events(limit=LOG_PAGE_SIZE, offset=win["offset"],
                               only_failed=only_failed),
        events_total=total,
        ip_summary=db.login_ip_summary(),
        blocked=db.blocked_ips(),
        only_failed=only_failed,
        # Yoneticinin kendi IP'si isaretlenir: yanlislikla kendini engellemesin.
        my_ip=client_ip(request),
        **win,
    )
    return templates.TemplateResponse(request, "settings/security.html", ctx)


@router.get("/{section}", response_class=HTMLResponse)
async def section_page(request: Request, section: str):
    if section not in BY_SLUG:
        raise HTTPException(404, i18n.t("settings.unknown_section", section=section))
    _guard(request, section)

    store = deps.get_settings_store()
    ctx = _base_ctx(request, section)
    ctx.update(
        title=i18n.t_or(f"settings.{section}.title", BY_SLUG[section].title),
        note=i18n.t_or(f"settings.{section}.note", BY_SLUG[section].note),
        fields=store.for_display(section),
        # Mock modda GLS kimlikleri saklanir ama UYGULANMAZ — kullaniciya soylenir.
        mock_blocked=(section == "gls" and config.IS_MOCK),
        readiness=config.readiness_check() if section == "gls" else [],
        wa_status=whatsapp.status() if section == "whatsapp" else None,
        # Bildirimler sekmesi son calisma bilgisini ve mail kimligi uyarisini gosterir.
        digest=getattr(request.app.state, "digest", None) if section == "notify" else None,
        mail_ready=mail.configured() if section == "notify" else True,
    )
    return templates.TemplateResponse(request, f"settings/{section}.html", ctx)


@router.post("/{section}")
async def save_section(request: Request, section: str):
    if section not in BY_SLUG:
        raise HTTPException(404, i18n.t("settings.unknown_section", section=section))
    _guard(request, section)

    form = dict(await request.form())
    store = deps.get_settings_store()
    actor = getattr(request.state, "user", None) or {}
    errors = store.save_section(section, form, actor=actor.get("username"))
    if errors:
        return RedirectResponse(f"/settings/{section}?error={'; '.join(errors)}",
                                status_code=303)

    settings_apply.apply(store,
                         getattr(request.app.state, "tracker", None),
                         getattr(request.app.state, "digest", None))
    # DEGER LOGLANMAZ — aralarinda sifreler var.
    log.info("Ayar bolumu kaydedildi: %s (%d alan)", section, len(form))

    message = i18n.t("settings.saved")
    if section == "gls" and config.IS_MOCK:
        message = i18n.t("settings.saved_mock")
    return RedirectResponse(f"/settings/{section}?ok={message}", status_code=303)


# ---------------------------------------------------------------------------
# Baglanti testleri (HTMX) — hepsi ayni partial'i doner
# ---------------------------------------------------------------------------
def _result(request: Request, rows: list[dict]) -> HTMLResponse:
    return templates.TemplateResponse(request, "_partials/settings_test.html",
                                      {"request": request, "results": rows})


def _check_mssql() -> dict:
    """Baglanti + her sezon gorunumunun gercekten var oldugu.

    Yalnizca baglanmak yetmez: gorunum adi sezon basinda degisiyor ve yanlis ad
    ancak ilk senkronda, saatler sonra fark ediliyor.
    """
    if not config.mssql_configured():
        return {"name": "mssql", "title": "MSSQL (Sentez/BlueCherry)",
                "configured": False, "ok": None,
                "detail": i18n.t("check.fields_missing")}

    client = ERPClient()
    views = config.season_views()
    try:
        for view in views:
            client.query(f"SELECT TOP 1 1 AS ok FROM [{safe_identifier(view)}]")
    except ERPError as exc:
        return {"name": "mssql", "title": "MSSQL (Sentez/BlueCherry)",
                "configured": True, "ok": False, "detail": str(exc)}
    return {"name": "mssql", "title": "MSSQL (Sentez/BlueCherry)",
            "configured": True, "ok": True,
            "detail": i18n.t("check.mssql_ok", count=len(views),
                              views=", ".join(views))}


@router.post("/mssql/test", response_class=HTMLResponse,
             dependencies=[Depends(require_role("admin"))])
async def test_mssql(request: Request):
    return _result(request, [await asyncio.to_thread(_check_mssql)])


@router.post("/gls/test", response_class=HTMLResponse,
             dependencies=[Depends(require_role("admin"))])
async def test_gls(request: Request):
    return _result(request, await asyncio.to_thread(providers.doctor))


@router.post("/mail/test", response_class=HTMLResponse,
             dependencies=[Depends(require_role("admin"))])
async def test_mail(request: Request):
    """Yalnizca KIMLIK DOGRULAR — mail GONDERMEZ (bkz. settings/mail.py)."""
    if not mail.configured():
        return _result(request, [{"name": "mail", "title": "Microsoft Graph",
                                  "configured": False, "ok": None,
                                  "detail": i18n.t("check.mail_missing")}])
    ok, detail = await asyncio.to_thread(mail.check_credentials)
    return _result(request, [{"name": "mail", "title": "Microsoft Graph",
                              "configured": True, "ok": ok, "detail": detail}])


@router.post("/notify/test", response_class=HTMLResponse)
async def test_notify(request: Request):
    """Diger sekmelerin aksine bu dugme GERCEKTEN gonderir — zamanlayicinin
    calistirdigi yolun ta kendisi. Ertesi sabahi beklemeden dogrulanabilsin diye."""
    digest = getattr(request.app.state, "digest", None)
    if digest is None:
        return _result(request, [{"name": "notify", "title": i18n.t("check.digest_title"),
                                  "configured": False, "ok": None,
                                  "detail": i18n.t("check.digest_off")}])
    ok, detail = await asyncio.to_thread(digest.send_now)
    return _result(request, [{"name": "notify", "title": i18n.t("check.digest_title"),
                              "configured": True, "ok": ok, "detail": detail}])
