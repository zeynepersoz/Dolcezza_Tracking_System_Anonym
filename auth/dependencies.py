# -*- coding: utf-8 -*-
"""
FastAPI auth bagimliligi.

Akis:
  1. 'session' cookie'si (imzali, kisa omurlu access token) gecerliyse dogrudan kabul.
  2. Degilse 'refresh_token' cookie'si DB'de aranir; gecerliyse (suresi gecmemis,
     iptal edilmemis, kullanici aktif) yeni bir session + rotasyonlu yeni refresh
     token uretilir ve yanit cookie'lerine yazilir (sessiz yenileme).
  3. Ikisi de yoksa/gecersizse NotAuthenticated firlatilir -> login sayfasina yonlendirilir.

Yetki (role) tamamen session cookie'sindeki degerden okunur; DB'ye sadece
is_active kontrolu icin (anlik yetki iptali) ve refresh rotasyonunda gidilir.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Response

from web import deps

from . import security

SESSION_COOKIE = "session"
REFRESH_COOKIE = "refresh_token"


class NotAuthenticated(Exception):
    pass


def client_ip(request: Request) -> str:
    """Baglantinin GERCEK kaynagi — `X-Forwarded-For` BILEREK okunmaz.

    Uygulama dogrudan yayinlanmis bir portta duruyor; guvenilir bir ters vekil
    yok. Basligi okusaydik herkes kendi IP'sini yazabilir: hem giris engelini
    asar, hem de baskasinin IP'sini engelletirdi. Onune bir vekil konursa
    BURASI (ve yalnizca burasi) guncellenmeli.
    """
    return request.client.host if request.client else ""


def set_auth_cookies(response: Response, *, session_token: str, refresh_raw: str) -> None:
    from gls_api import config

    response.set_cookie(
        SESSION_COOKIE, session_token,
        max_age=config.AUTH_SESSION_MAX_AGE, httponly=True,
        samesite="lax", secure=config.AUTH_COOKIE_SECURE, path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh_raw,
        max_age=config.AUTH_REFRESH_MAX_AGE_DAYS * 86400, httponly=True,
        samesite="lax", secure=config.AUTH_COOKIE_SECURE, path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


def _session_payload(user: dict) -> dict:
    # `full_name` ekranda gosterilir; eski cerezlerde yok, sablon username'e duser.
    return {"uid": user["id"], "username": user["username"],
            "full_name": user["full_name"] or "", "role": user["role"]}


def issue_session_for_user(user: dict) -> str:
    return security.sign_session(_session_payload(user))


def _try_refresh(request: Request, response: Response) -> dict | None:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        return None

    db = deps.get_auth_db()
    token_hash = security.hash_refresh_token(raw)
    record = db.get_refresh_token(token_hash)
    if not record or record["revoked"] or security.refresh_token_expired(record["expires_at"]):
        return None

    user = db.get_user(record["user_id"])
    if not user or not user["is_active"]:
        return None

    # Rotasyon: eski token'i iptal et, yenisini ver (calinti token tekrar kullanilamaz)
    db.revoke_refresh_token(token_hash)
    new_raw, new_hash, expires_at = security.new_refresh_token()
    db.store_refresh_token(user["id"], new_hash, expires_at)

    session_token = issue_session_for_user(user)
    set_auth_cookies(response, session_token=session_token, refresh_raw=new_raw)

    return _session_payload(user)


async def get_current_user(request: Request, response: Response) -> dict:
    session_cookie = request.cookies.get(SESSION_COOKIE)
    if session_cookie:
        payload = security.verify_session(session_cookie)
        if payload is not None:
            request.state.user = payload
            return payload

    refreshed = _try_refresh(request, response)
    if refreshed is not None:
        request.state.user = refreshed
        return refreshed

    clear_auth_cookies(response)
    raise NotAuthenticated()


def require_role(*roles: str):
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, "Bu islem icin yetkiniz yok.")
        return user
    return _dep
