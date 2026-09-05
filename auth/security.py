# -*- coding: utf-8 -*-
"""
Sifre hashleme (PBKDF2-HMAC-SHA256, stdlib) + imzali oturum cookie'si (itsdangerous)
+ refresh token uretimi/dogrulamasi.

Neden PBKDF2 (bcrypt/argon2 degil): ek bagimlilik gerektirmiyor, stdlib'de hazir ve
yeterince yavas (200k iterasyon) -> brute-force'u pahaliya mal ediyor.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from gls_api import config

PBKDF2_ITERATIONS = 200_000
_SESSION_SALT = "gls-session-cookie"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


# Olmayan bir kullanici adiyla giris denendiginde `verify_password` HIC
# CAGRILMAZSA (kisa devre), yanit var-olan bir hesaba gore ANLIK doner —
# PBKDF2 200k adimin suresi olcumle ayirt edilebilir, zamanlama farkindan
# "bu kullanici adi sistemde var mi" sorusuna cevap sizar. Kullanici
# bulunamadiginda gercek hash yerine bu SABIT hash'e karsi dogrulanir; is
# HER durumda ayni PBKDF2 isini yapar, sure farki ortadan kalkar.
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algo, iterations, salt, hex_digest = encoded_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), hex_digest)
    except (ValueError, AttributeError):
        return False


def _serializer() -> URLSafeTimedSerializer:
    # itsdangerous varsayilani HMAC-SHA1 imzalar; SHA256'ya yukseltiyoruz.
    return URLSafeTimedSerializer(
        config.AUTH_SECRET_KEY, salt=_SESSION_SALT,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def sign_session(payload: dict) -> str:
    """Kullanici bilgisini imzali+zaman damgali bir token'a doner (cookie degeri)."""
    return _serializer().dumps(payload)


def verify_session(token: str) -> dict | None:
    """Imza + max_age gecerliyse payload'i doner, degilse None (sessizce reddet)."""
    try:
        return _serializer().loads(token, max_age=config.AUTH_SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def new_refresh_token() -> tuple[str, str, str]:
    """(ham_token, sha256_hash, expires_at_iso) doner. Ham token sadece cookie'ye yazilir, DB'ye hash'i gider."""
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    expires_at = (datetime.utcnow() + timedelta(days=config.AUTH_REFRESH_MAX_AGE_DAYS)).isoformat(timespec="seconds")
    return raw, token_hash, expires_at


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def refresh_token_expired(expires_at_iso: str) -> bool:
    return datetime.utcnow().isoformat(timespec="seconds") > expires_at_iso


# Sifre sifirlama belirteci refresh token'in AYNI deseni ama AYRI fonksiyonlar:
# omurleri (30 dakika / 30 gun) ve amaclari farkli. Tek fonksiyona parametre
# eklemek iki akisi birbirine baglar; birinin suresini degistirmek digerini de
# etkilerdi.
PASSWORD_RESET_MINUTES = 30


def new_reset_token() -> tuple[str, str, str]:
    """(ham_token, sha256_hash, expires_at_iso). Ham deger yalnizca maildeki
    baglantiya girer, DB'ye hash'i yazilir."""
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    expires_at = (datetime.utcnow() + timedelta(minutes=PASSWORD_RESET_MINUTES)) \
        .isoformat(timespec="seconds")
    return raw, token_hash, expires_at


def hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def reset_token_expired(expires_at_iso: str) -> bool:
    return datetime.utcnow().isoformat(timespec="seconds") > expires_at_iso
