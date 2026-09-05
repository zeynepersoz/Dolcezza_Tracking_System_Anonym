# -*- coding: utf-8 -*-
"""Kimlik uclari icin kaba kuvvet freni — YALNIZCA giris/sifirlama.

Panelin normal trafigi (HTMX ile saniyede birkac istek atan takip tablosu)
BILEREK disarida: orada sinir kullaniciyi cezalandirirdi, saldirgani degil.

Sayac BELLEKTE tutulur ve bu gecerlidir: `Dockerfile` CMD'sinde `--workers`
yok, `docker-compose.yml`de tek `web` servisi var — tek surec, tek sozluk.
Cok surecli/coklu instance bir kuruluma gecilirse burasi Redis'e tasinmali.

Yeniden baslatma sayaci sifirlar; kabul: yeniden baslatma nadir, saldirgan onu
tetikleyemiyor.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    limit: int      # pencere icinde kac deneme
    window: int     # saniye — bundan eski denemeler unutulur
    lockout: int    # saniye — sinir asildiktan sonra ne kadar kapali kalir


# Hedefli saldiri (tek hesabin sifresini kirmak) bu kurala takilir. Degerler
# eski `_FAILED_LOGINS` bloguyla BIREBIR ayni — davranis degismesin.
LOGIN_USER = Rule(limit=5, window=15 * 60, lockout=5 * 60)

# Dagitik saldiri (500 farkli kullanici adi denemek) yalnizca IP'den gorulur.
# TAVAN BILEREK YUKSEK: canli kurulumda internet trafigi bir ters vekilden
# geliyor (dogrulandi: tum dis istekler 192.0.2.20 gorunuyor) ve `client_ip()`
# `X-Forwarded-For`u bilerek okumuyor. Yani UZAKTAN giren HERKES ayni kovayi
# paylasiyor; dar bir tavan tek bir botun butun ofisi kilitlemesi demekti.
# 60 hatali giris/15 dk bir insan toplulugu icin ulasilmaz, bir bot icin saniye
# meselesi — fren yine is goruyor.
LOGIN_IP = Rule(limit=60, window=15 * 60, lockout=15 * 60)

# Sifirlama maili bombasi: bir adrese arka arkaya yuzlerce mail atilmasin.
# Asil koruma HEDEF BASINA olan (`forgot:id:`); IP kurali yine vekil yuzunden
# paylasimli oldugu icin gevsek.
FORGOT_IP = Rule(limit=30, window=60 * 60, lockout=60 * 60)
FORGOT_ID = Rule(limit=5, window=60 * 60, lockout=60 * 60)

# Sifirlama formuna gecerli belirtec tahmin ederek girme denemesi.
RESET_IP = Rule(limit=20, window=15 * 60, lockout=15 * 60)

# `time.monotonic()` — `time.time()` NTP duzeltmesiyle geri gidebilir ve kilidi
# erken acardi.
_HITS: dict[str, list[float]] = defaultdict(list)


def _recent(bucket: str, window: int) -> list[float]:
    now = time.monotonic()
    kept = [t for t in _HITS[bucket] if now - t < window]
    _HITS[bucket] = kept
    return kept


def locked(bucket: str, rule: Rule) -> bool:
    hits = _recent(bucket, rule.window)
    return len(hits) >= rule.limit and (time.monotonic() - hits[-1]) < rule.lockout


def hit(bucket: str) -> None:
    _HITS[bucket].append(time.monotonic())


def clear(bucket: str) -> None:
    _HITS.pop(bucket, None)


def reset_all() -> None:
    """Yalnizca testler icin — sayac surec omurlu, testler birbirini kirletmesin."""
    _HITS.clear()


def login_user(username: str) -> str:
    return f"login:user:{username}"


def login_ip(ip: str) -> str:
    return f"login:ip:{ip}"


def forgot_ip(ip: str) -> str:
    return f"forgot:ip:{ip}"


def forgot_id(identifier: str) -> str:
    return f"forgot:id:{identifier.strip().lower()}"


def reset_ip(ip: str) -> str:
    return f"reset:ip:{ip}"
