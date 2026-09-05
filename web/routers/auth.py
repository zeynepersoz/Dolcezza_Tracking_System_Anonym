# -*- coding: utf-8 -*-
"""Giris/cikis, sessiz token yenileme ve (admin) kullanici yonetimi."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

import i18n
from auth import ratelimit, security
from auth.db import ROLES
from auth.dependencies import (
    clear_auth_cookies,
    client_ip,
    get_current_user,
    issue_session_for_user,
    require_role,
    set_auth_cookies,
)
from gls_api import config
from notify import password_reset
from web.templating import templates
from web import deps

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _log_login(request: Request, username: str, *, success: bool, reason: str = "") -> None:
    """Giris denemesini yazar — basarisiz olanlar dahil.

    Kayit HICBIR ZAMAN girisi engellemez: denetim izi ugruna kimse kapida
    kalmasin (disk dolu, DB kilitli). Hata yalnizca gunluge dusulur.
    """
    try:
        deps.get_auth_db().record_login(
            username, ip=client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
            success=success, reason=reason)
    except Exception:                       # noqa: BLE001 — denetim izi girisi bloklamaz
        log.exception("Giris kaydi yazilamadi (%s)", username)


def _safe_next(raw: str) -> str:
    """Yalnizca kendi sitemizdeki bir yola izin verir.

    Kullanicidan gelen adrese kosulsuz yonlendirmek giris sayfamizi kimlik
    avinda basamak yapar: "//kotu.site" ve "https://..." tarayici tarafindan
    DIS adres sayilir. Ayni kural dil secicide de var (bkz. routers/lang.py).
    """
    return raw if raw.startswith("/") and not raw.startswith("//") else "/"


def _login_page(request: Request, error: str, next_url: str, status: int = 200):
    """Giris ekranini hatayla birlikte doner.

    Rate limit ve engel yanitlari `HTTPException` DEGIL bu sablonla verilir:
    ciplak bir JSON hatasi kullanici tarayicida panelden atilmis gibi hisseder,
    ustelik "ne yapmali" bilgisi gitmez.
    """
    return templates.TemplateResponse(
        request, "login.html",
        {"request": request, "error": error, "next_url": next_url},
        status_code=status,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = "", next: str = "/"):
    return _login_page(request, error, _safe_next(next))


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    generic_error = i18n.t("auth.bad_credentials")
    next_url = _safe_next(next)
    ip = client_ip(request)

    db = deps.get_auth_db()

    # Elle engellenen IP. Sifre HIC dogrulanmaz — engelin amaci budur.
    if db.is_ip_blocked(ip):
        _log_login(request, username, success=False, reason="ip_blocked")
        return _login_page(request, i18n.t("auth.ip_blocked"), next_url, status=403)

    if ratelimit.locked(ratelimit.login_user(username), ratelimit.LOGIN_USER):
        _log_login(request, username, success=False, reason="locked_out")
        return _login_page(request, i18n.t("auth.locked_out"), next_url, status=429)

    if ratelimit.locked(ratelimit.login_ip(ip), ratelimit.LOGIN_IP):
        _log_login(request, username, success=False, reason="ip_rate_limited")
        return _login_page(request, i18n.t("auth.rate_limited"), next_url, status=429)

    user = db.get_user_by_username(username)
    # Kullanici yoksa da SABIT bir hash'e karsi dogrulama YAPILIR (kisa devre
    # edilmez) — yoksa PBKDF2'nin atlanmasi zamanlama farkindan "bu kullanici
    # adi var mi" bilgisini sizdirir (bkz. auth/security.py:DUMMY_HASH).
    password_hash = user["password_hash"] if user else security.DUMMY_HASH
    password_ok = security.verify_password(password, password_hash)
    if not user or not user["is_active"] or not password_ok:
        ratelimit.hit(ratelimit.login_user(username))
        ratelimit.hit(ratelimit.login_ip(ip))
        # Sebep AYRI tutulur: pasif hesabin surekli denenmesi ile yanlis sifre
        # ayni sey degil, ama kullaniciya ikisinde de ayni genel mesaj gider.
        _log_login(request, username, success=False,
                   reason="inactive" if user and not user["is_active"] else "bad_credentials")
        return _login_page(request, generic_error, next_url, status=401)

    # YALNIZCA kullanici kovasi temizlenir; IP kovasi kalir. Yoksa saldirgan
    # aradaki tek bir dogru girisle (kendi hesabiyla) sayaci sifirlardi.
    ratelimit.clear(ratelimit.login_user(username))
    _log_login(request, username, success=True)

    session_token = issue_session_for_user(user)
    raw_refresh, refresh_hash, expires_at = security.new_refresh_token()
    db.store_refresh_token(user["id"], refresh_hash, expires_at)

    redirect = RedirectResponse(next_url, status_code=303)
    set_auth_cookies(redirect, session_token=session_token, refresh_raw=raw_refresh)
    return redirect


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user: dict = Depends(get_current_user), error: str = "", ok: str = ""):
    ctx = {
        "request": request,
        "user": user,
        "error": error,
        "ok": ok,
        **deps.template_context(),
    }
    return templates.TemplateResponse(request, "profile.html", ctx)


@router.post("/profile/password")
async def change_password(
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
):
    db = deps.get_auth_db()
    account = db.get_user(user["uid"])

    if not account or not security.verify_password(current_password, account["password_hash"]):
        return templates.TemplateResponse(
            request, "profile.html",
            {"request": request, "user": user, "error": i18n.t("auth.current_password_wrong"), "ok": "", **deps.template_context()},
            status_code=401,
        )
    if new_password != new_password_confirm:
        return templates.TemplateResponse(
            request, "profile.html",
            {"request": request, "user": user, "error": i18n.t("auth.passwords_differ"), "ok": "", **deps.template_context()},
            status_code=422,
        )
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request, "profile.html",
            {"request": request, "user": user, "error": i18n.t("auth.password_too_short"), "ok": "", **deps.template_context()},
            status_code=422,
        )

    db.update_password(account["id"], security.hash_password(new_password))
    # Sifre degisince tum acik oturumlar (bu tarayici dahil) dusurulur;
    # kullanici yeni sifresiyle tekrar giris yapar.
    db.revoke_all_for_user(account["id"])
    redirect = RedirectResponse("/auth/login", status_code=303)
    clear_auth_cookies(redirect)
    return redirect


@router.post("/logout")
async def logout(request: Request):
    from auth.dependencies import REFRESH_COOKIE

    db = deps.get_auth_db()
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        db.revoke_refresh_token(security.hash_refresh_token(raw))

    redirect = RedirectResponse("/auth/login", status_code=303)
    clear_auth_cookies(redirect)
    return redirect


# ---------------------------------------------------------------- sifre sifirlama (acik)

def _reset_link(raw_token: str) -> str:
    """Maildeki baglantiyi kurar — adres YALNIZCA `NOTIFY_PANEL_URL`den gelir.

    `request.base_url` KULLANILMAZ: Host basligi kullanici girdisidir. Saldirgan
    kendi adresini yazip sifirlama baglantisini kendi sunucusuna yonlendirir,
    kurbanin tiklamasiyla belirteci ele gecirirdi.
    """
    root = (config.NOTIFY_PANEL_URL or "").strip().rstrip("/")
    return f"{root}/auth/reset?token={raw_token}" if root else ""


@router.get("/forgot", response_class=HTMLResponse)
async def forgot_page(request: Request, error: str = "", ok: str = ""):
    return templates.TemplateResponse(
        request, "forgot.html", {"request": request, "error": error, "ok": ok})


@router.post("/forgot", response_class=HTMLResponse)
async def forgot(request: Request, identifier: str = Form(...)):
    """Sifirlama baglantisi ister. Kullanici adi VEYA e-posta kabul eder.

    Hesap bulunsun bulunmasin AYNI mesaj doner: farkli yanit vermek "bu
    kullanici adi sistemde var" demek olurdu (kullanici sayimi).
    """
    ident = identifier.strip()
    ip = client_ip(request)
    sent_message = i18n.t("auth.reset_sent")

    if ratelimit.locked(ratelimit.forgot_ip(ip), ratelimit.FORGOT_IP) or \
            ratelimit.locked(ratelimit.forgot_id(ident), ratelimit.FORGOT_ID):
        return templates.TemplateResponse(
            request, "forgot.html",
            {"request": request, "error": i18n.t("auth.rate_limited"), "ok": ""},
            status_code=429,
        )
    ratelimit.hit(ratelimit.forgot_ip(ip))
    ratelimit.hit(ratelimit.forgot_id(ident))

    db = deps.get_auth_db()
    _log_login(request, ident or "?", success=False, reason="password_reset_requested")

    # Ayni adres birden fazla aktif hesaba bagli olabilir (ortak posta kutusu);
    # her birine kendi baglantisi gider, yoksa hangisinin sifirlandigi belirsizdi.
    by_name = db.get_user_by_username(ident)
    targets = [by_name] if by_name and by_name["is_active"] else []
    targets += [u for u in db.users_by_email(ident) if u["id"] not in {t["id"] for t in targets}]

    for user in targets:
        if not (user["email"] or "").strip():
            # E-postasi girilmemis hesap — mail atacak adres yok. Yoneticinin
            # /settings/users ekranindan sifre atamasi gerekir.
            log.warning("Şifre sıfırlama istendi ama hesabın e-postası yok: %s",
                        user["username"])
            continue
        raw, token_hash, expires_at = security.new_reset_token()
        link = _reset_link(raw)
        if not link:
            log.error("NOTIFY_PANEL_URL bos — şifre sıfırlama bağlantısı üretilemedi")
            break
        db.create_password_reset(user["id"], token_hash, expires_at, ip=ip)
        password_reset.send(user["email"], user["username"], link)

    return templates.TemplateResponse(
        request, "forgot.html", {"request": request, "error": "", "ok": sent_message})


def _valid_reset(token: str) -> tuple[dict | None, str]:
    """(kayit, hata_mesaji). Gecerliyse hata bos."""
    if not token:
        return None, i18n.t("auth.reset_invalid")
    record = deps.get_auth_db().get_password_reset(security.hash_reset_token(token))
    if not record:
        return None, i18n.t("auth.reset_invalid")
    if record["used_at"]:
        return None, i18n.t("auth.reset_used")
    if security.reset_token_expired(record["expires_at"]):
        return None, i18n.t("auth.reset_expired")
    return record, ""


@router.get("/reset", response_class=HTMLResponse)
async def reset_page(request: Request, token: str = ""):
    _, error = _valid_reset(token)
    return templates.TemplateResponse(
        request, "reset.html",
        {"request": request, "token": token, "error": error, "done": False},
        status_code=200 if not error else 400,
    )


@router.post("/reset", response_class=HTMLResponse)
async def reset(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
):
    ip = client_ip(request)

    def page(error: str, status: int, done: bool = False):
        return templates.TemplateResponse(
            request, "reset.html",
            {"request": request, "token": token, "error": error, "done": done},
            status_code=status,
        )

    if ratelimit.locked(ratelimit.reset_ip(ip), ratelimit.RESET_IP):
        return page(i18n.t("auth.rate_limited"), 429)
    ratelimit.hit(ratelimit.reset_ip(ip))

    record, error = _valid_reset(token)
    if error:
        return page(error, 400)

    if new_password != new_password_confirm:
        return page(i18n.t("auth.passwords_differ"), 422)
    if len(new_password) < 8:
        return page(i18n.t("auth.password_too_short"), 422)

    db = deps.get_auth_db()
    user = db.get_user(record["user_id"])
    if not user or not user["is_active"]:
        return page(i18n.t("auth.reset_invalid"), 400)

    db.update_password(user["id"], security.hash_password(new_password))
    db.use_password_reset(record["token_hash"])
    # Bekleyen diger baglantilar ve TUM acik oturumlar dusurulur: sifirlamanin
    # sebebi hesabin ele gecmis olmasi olabilir.
    db.invalidate_password_resets(user["id"])
    db.revoke_all_for_user(user["id"])
    # Kilitli kullanici yeni sifresiyle HEMEN girebilsin — sifirlama zaten
    # kimligini kanitladi, 5 dakika daha kapida bekletmenin anlami yok.
    ratelimit.clear(ratelimit.login_user(user["username"]))
    _log_login(request, user["username"], success=False, reason="password_reset_done")

    return page("", 200, done=True)


# ---------------------------------------------------------------- kullanici yonetimi (admin)

@router.get("/users")
async def users_page():
    """Sayfa Ayarlar altina tasindi; eski yer imleri 404 olmasin."""
    return RedirectResponse("/settings/users", status_code=303)


def _clean_email(raw: str) -> str:
    """Bos birakilabilir; girilmisse kabaca bir adres olmali.

    Tam RFC dogrulamasi yapilmaz (mumkun de degil) — amac parmak hatasini
    yakalamak: adresi yanlis girilen kullaniciya sifirlama maili hic ulasmaz
    ve bunu ancak sifresini unuttugunda anlar.
    """
    email = raw.strip().lower()
    if email and ("@" not in email or email.startswith("@") or email.endswith("@")
                  or " " in email):
        raise HTTPException(400, i18n.t("err.invalid_email"))
    return email


@router.post("/users/create")
async def create_user(
    request: Request,
    admin: dict = Depends(require_role("admin")),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("operator"),
    full_name: str = Form(""),
    email: str = Form(""),
):
    # Gecerli roller `auth/db.py:ROLES`ten: elle yazilmis bir liste burada
    # unutuldugunda ekranda secilebilen rol kayit sirasinda reddediliyor.
    if role not in ROLES:
        raise HTTPException(400, i18n.t("err.invalid_role"))
    if len(password) < 8:
        raise HTTPException(400, i18n.t("auth.password_too_short"))

    db = deps.get_auth_db()
    if db.get_user_by_username(username):
        raise HTTPException(400, i18n.t("err.username_taken"))

    db.create_user(username, security.hash_password(password), role=role,
                   full_name=full_name.strip(), email=_clean_email(email))
    return RedirectResponse("/settings/users", status_code=303)


@router.post("/users/{user_id}/toggle")
async def toggle_user(user_id: int, admin: dict = Depends(require_role("admin"))):
    if user_id == admin["uid"]:
        raise HTTPException(400, i18n.t("err.cannot_disable_self"))

    db = deps.get_auth_db()
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, i18n.t("err.user_not_found"))

    # Son aktif yoneticiyi kapatmak paneli yonetilemez birakir.
    if target["is_active"] and target["role"] == "admin" and db.count_active_admins() <= 1:
        raise HTTPException(400, i18n.t("err.last_admin"))

    db.set_active(user_id, not target["is_active"])
    if target["is_active"]:  # simdi kapatildi -> tum oturumlarini duser
        db.revoke_all_for_user(user_id)
    return RedirectResponse("/settings/users", status_code=303)


@router.post("/users/{user_id}/update")
async def update_user(
    user_id: int,
    admin: dict = Depends(require_role("admin")),
    full_name: str = Form(""),
    email: str = Form(""),
    role: str = Form(...),
):
    if role not in ROLES:
        raise HTTPException(400, i18n.t("err.invalid_role"))

    db = deps.get_auth_db()
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, i18n.t("err.user_not_found"))

    if role != target["role"]:
        # Kendi rolunu dusuren yonetici kendi kapisini kilitler; geri almak icin
        # baska bir admin gerekir. Bilerek reddedilir.
        if user_id == admin["uid"]:
            raise HTTPException(400, i18n.t("err.cannot_change_own_role"))
        if target["role"] == "admin" and target["is_active"] and db.count_active_admins() <= 1:
            raise HTTPException(400, i18n.t("err.last_admin"))

    db.update_user(user_id, full_name=full_name.strip(), email=_clean_email(email), role=role)
    if role != target["role"]:
        # Yeni yetki eski oturumun imzali cerezinde YOK; oturumu dusurmezsek
        # kullanici en fazla `AUTH_SESSION_MINUTES` boyunca eski roluyle gezer.
        db.revoke_all_for_user(user_id)
    return RedirectResponse("/settings/users?ok=1", status_code=303)


@router.post("/users/{user_id}/password")
async def set_user_password(
    user_id: int,
    admin: dict = Depends(require_role("admin")),
    new_password: str = Form(...),
):
    """Yoneticinin dogrudan sifre atamasi — mail yolu calismadiginda tek kurtaris.

    Mevcut sifre SORULMAZ (zaten unutuldugu icin buradayiz); yetki `admin`
    olmasindan geliyor.
    """
    if len(new_password) < 8:
        raise HTTPException(400, i18n.t("auth.password_too_short"))

    db = deps.get_auth_db()
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, i18n.t("err.user_not_found"))

    db.update_password(user_id, security.hash_password(new_password))
    db.invalidate_password_resets(user_id)
    db.revoke_all_for_user(user_id)
    log.warning("Şifre yönetici tarafından atandı: %s (%s)",
                target["username"], admin["username"])
    return RedirectResponse("/settings/users?ok=1", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user(user_id: int, admin: dict = Depends(require_role("admin"))):
    if user_id == admin["uid"]:
        raise HTTPException(400, i18n.t("err.cannot_delete_self"))

    db = deps.get_auth_db()
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, i18n.t("err.user_not_found"))
    if target["role"] == "admin" and target["is_active"] and db.count_active_admins() <= 1:
        raise HTTPException(400, i18n.t("err.last_admin"))

    db.delete_user(user_id)
    log.warning("Kullanıcı silindi: %s (%s)", target["username"], admin["username"])
    return RedirectResponse("/settings/users?ok=1", status_code=303)


# ---------------------------------------------------------------- IP engelleme (admin)

@router.post("/ip/block")
async def block_ip(
    request: Request,
    admin: dict = Depends(require_role("admin")),
    ip: str = Form(...),
    note: str = Form(""),
):
    """Bir IP'den giris yapilmasini yasaklar.

    Engel YALNIZCA giris ekranina uygulanir, tum istegi kesmez: yoneticinin
    kendi agini yanlislikla engellemesi durumunda acik oturumuyla listeye girip
    geri alabilsin. Kendi IP'sini engellemek zaten asagida reddedilir.
    """
    ip = ip.strip()
    if not ip:
        raise HTTPException(400, i18n.t("security.err_no_ip"))
    if ip == client_ip(request):
        raise HTTPException(400, i18n.t("security.err_own_ip"))

    deps.get_auth_db().block_ip(ip, note=note.strip(), blocked_by=admin["username"])
    log.warning("IP engellendi: %s (%s) — %s", ip, admin["username"], note.strip())
    return RedirectResponse("/settings/security", status_code=303)


@router.post("/ip/unblock")
async def unblock_ip(
    admin: dict = Depends(require_role("admin")),
    ip: str = Form(...),
):
    deps.get_auth_db().unblock_ip(ip.strip())
    log.warning("IP engeli kaldirildi: %s (%s)", ip.strip(), admin["username"])
    return RedirectResponse("/settings/security", status_code=303)
