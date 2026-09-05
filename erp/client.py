"""Sentez / BlueCherry MSSQL'ine SALT OKUNUR baglanti.

Kullanici acikca "dbde degisiklik olmasin, orada birseyleri silmeyelim" dedi.
Bu bir yorum satiri olarak birakilmadi: `query()` SELECT/WITH disindaki her ifadeyi
SUNUCUYA GONDERMEDEN reddeder. Boylece ilerideki bir degisiklik yanlislikla yazma
yapamaz.

Gorunum adlari SQL'e parametre olarak gecirilemez (T-SQL tanimlayicilari
parametrelestirmez), bu yuzden `safe_identifier()` sunucuya gitmeden once adi
dogrular — enjeksiyon kapisi burada kapanir.

Surucu `pymssql`: ODBC/FreeTDS kurulumu gerektirmez, cp312 manylinux tekerlegi
hazir gelir (Dockerfile'daki python:3.12-slim icin onemli).
"""
from __future__ import annotations

import re
from contextlib import contextmanager

from gls_api import config

import i18n


class ERPError(RuntimeError):
    """MSSQL tarafindan gelen her hata — cagiran tek tip yakalar."""


# Tanimlayicilar (gorunum/tablo adi) SQL metnine gomuluyor: harf, rakam, alt cizgi.
_SAFE_IDENT = re.compile(r"^[A-Za-z0-9_]{1,128}$")
# Yalnizca okuma. WITH, CTE ile baslayan SELECT'ler icin.
_READ_ONLY = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def safe_identifier(name: str) -> str:
    """Gorunum/tablo adini dogrular. Gecersizse sorgu hic kurulmaz."""
    name = (name or "").strip()
    if not _SAFE_IDENT.match(name):
        raise ERPError(i18n.t("erp.bad_identifier", name=repr(name)))
    return name


class ERPClient:
    """Tek islik icin MSSQL okuyucusu. Her `query()` kendi baglantisini acar/kapatir."""

    def __init__(self, server: str = "", port: int = 0, database: str = "",
                 username: str = "", password: str = "", timeout: int = 60):
        self.server = server or config.MSSQL_SERVER
        self.port = port or config.MSSQL_PORT
        self.database = database or config.MSSQL_DATABASE
        self.username = username or config.MSSQL_USERNAME
        self.password = password or config.MSSQL_PASSWORD
        self.timeout = timeout

    @contextmanager
    def _connect(self):
        try:
            import pymssql
        except ImportError as exc:  # pragma: no cover - kurulum sorunu
            raise ERPError(i18n.t("erp.no_pymssql")) from exc

        if not all((self.server, self.database, self.username, self.password)):
            raise ERPError(i18n.t("erp.not_configured"))

        try:
            conn = pymssql.connect(
                server=self.server, port=str(self.port), database=self.database,
                user=self.username, password=self.password,
                timeout=self.timeout, login_timeout=15,
            )
        except Exception as exc:
            raise ERPError(i18n.t("erp.no_connection", server=self.server,
                                  port=self.port, detail=exc)) from exc
        try:
            yield conn
        finally:
            conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Salt okunur sorgu -> sozluk listesi.

        `as_dict=True` KULLANILMAZ: pymssql isimsiz kolonda (or. `COUNT(*)`)
        ColumnsWithoutNamesError atiyor. Bunun yerine `cursor.description`
        zip'lenir — takma ad unutulsa bile sorgu calisir.
        """
        if not _READ_ONLY.match(sql or ""):
            raise ERPError(i18n.t("erp.read_only"))

        with self._connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, params)
                columns = [d[0] for d in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
            except ERPError:
                raise
            except Exception as exc:
                raise ERPError(i18n.t("erp.query_failed", detail=exc)) from exc
            finally:
                cursor.close()
