# -*- coding: utf-8 -*-
"""Ilk admin kullanicisini olusturur (users tablosu bossa)."""
from __future__ import annotations

import logging
import secrets

from gls_api import config

from .db import AuthDB
from .security import hash_password

log = logging.getLogger("auth")


def ensure_admin(db: AuthDB) -> None:
    if db.count_users() > 0:
        return

    password = config.AUTH_ADMIN_PASSWORD or secrets.token_urlsafe(12)
    db.create_user(config.AUTH_ADMIN_USERNAME, hash_password(password), role="admin")

    if config.AUTH_ADMIN_PASSWORD:
        log.info("Ilk admin kullanicisi olusturuldu: %s (.env'deki sifreyle)", config.AUTH_ADMIN_USERNAME)
    else:
        log.warning(
            "Ilk admin kullanicisi olusturuldu -> kullanici adi: %s | sifre: %s "
            "(AUTH_ADMIN_PASSWORD .env'de tanimli degildi, rastgele uretildi — "
            "giris yaptiktan sonra degistirin)",
            config.AUTH_ADMIN_USERNAME, password,
        )
