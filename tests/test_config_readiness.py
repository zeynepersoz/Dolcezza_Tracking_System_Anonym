# -*- coding: utf-8 -*-
"""config.readiness_check() / configured_providers() testleri.

GLS tek bir API vermez; ShipIT, Track&Trace ve GLS Netherlands birbirinden
BAGIMSIZ ucu kanaldir. Bu yuzden "hazir" olmak = ucunun de dolu olmasi degil,
**en az birinin** dolu ve tutarli olmasidir. Testler bunu dogrular.

config.py modul yuklenirken os.environ'dan okudugu icin her test kendi ortam
degiskenlerini ayarlayip modulu yeniden yukluyor, sonda gercek (mock) ortama
donuluyor.
"""
import importlib
from pathlib import Path

import pytest

from gls_api import config as config_module


def _raise_readonly(*args, **kwargs):
    raise OSError("read-only file system")

# Yeniden yukleme oncesi temizlenecek anahtarlar. Not: config._load_dotenv()
# setdefault kullanir, yani silinen bir anahtar .env'deki (mock) degeriyle geri
# gelir — testlerde onemli olan degerleri bu yuzden acikca setenv ile veriyoruz.
_KEYS = (
    "GLS_MODE",
    "SHIPIT_BASE_URL", "SHIPIT_USERNAME", "SHIPIT_PASSWORD", "SHIPIT_CONTACT_ID",
    "TT_ENDPOINT", "TT_USERNAME", "TT_PASSWORD",
    "NL_BASE_URL", "NL_USERNAME", "NL_PASSWORD", "NL_CUSTOMER_NO",
    "FEDEX_BASE_URL", "FEDEX_API_KEY", "FEDEX_API_SECRET", "FEDEX_ACCOUNT_NUMBER",
    "AUTH_SECRET_KEY", "AUTH_COOKIE_SECURE",
)

SHIPIT_REAL = {
    "SHIPIT_BASE_URL": "https://customer.gls-group.eu:8443/backend",
    "SHIPIT_USERNAME": "real-user",
    "SHIPIT_PASSWORD": "real-pass",
}
TT_REAL = {"TT_USERNAME": "real-user", "TT_PASSWORD": "real-pass"}
NL_REAL = {
    "NL_BASE_URL": "https://api.gls.nl/test/v1/api",
    "NL_USERNAME": "real-user",
    "NL_PASSWORD": "real-pass",
}


def _reload_with_env(monkeypatch, env: dict):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    # Bu ucu `delenv` YETMEZ: gercek varsayilanlari BOS DEGIL (TT_ENDPOINT,
    # FEDEX_BASE_URL sabit gercek URL'ye duser) ama gizli anahtarlarinin
    # varsayilani BOS'tur — silinince `config._load_dotenv()` reload'da
    # YENIDEN calisip `setdefault` ile gelistiricinin yerel `.env`sindeki
    # GERCEK degeri geri koyar (canli olcum: `.env`e FEDEX_API_KEY eklenince
    # bu dosyadaki testler sessizce kirildi). Bos STRING'le setenv edilirse
    # anahtar VAR ama bos kalir, `setdefault` bunu atlar.
    for key in ("FEDEX_API_KEY", "FEDEX_API_SECRET", "FEDEX_ACCOUNT_NUMBER"):
        monkeypatch.setenv(key, "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config_module)


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    # Panelden gelen ayarlar (settings/) config'e itiliyor; sizan bir override
    # buradaki testleri sebepsiz kirilgan yapar.
    config_module.clear_overrides()
    importlib.reload(config_module)  # gercek (mock) ortama geri don


# ---------------------------------------------------------------- mock
def test_mock_mode_always_ready(monkeypatch):
    cfg = _reload_with_env(monkeypatch, {"GLS_MODE": "mock"})
    assert cfg.readiness_check() == []
    assert cfg.configured_providers() == ["shipit", "tt", "nl", "fedex"]


# ---------------------------------------------------------------- test modu
def test_test_mode_with_untouched_mock_values_is_not_ready(monkeypatch):
    cfg = _reload_with_env(monkeypatch, {"GLS_MODE": "test"})
    issues = cfg.readiness_check()
    assert cfg.configured_providers() == []
    assert any("Hicbir GLS saglayicisi" in i for i in issues)


def test_single_provider_is_enough(monkeypatch):
    """Sadece ShipIT verilmisse sistem hazirdir — digerleri gerekmez."""
    cfg = _reload_with_env(monkeypatch, {"GLS_MODE": "test", **SHIPIT_REAL})
    assert cfg.configured_providers() == ["shipit"]
    assert cfg.readiness_check() == []


def test_only_track_and_trace_is_enough(monkeypatch):
    cfg = _reload_with_env(monkeypatch, {"GLS_MODE": "test", **TT_REAL})
    assert cfg.configured_providers() == ["tt"]
    assert cfg.readiness_check() == []


def test_only_nl_is_enough(monkeypatch):
    cfg = _reload_with_env(monkeypatch, {"GLS_MODE": "test", **NL_REAL})
    assert cfg.configured_providers() == ["nl"]
    assert cfg.readiness_check() == []


# ---------------------------------------------------------------- kismi/yanlis
def test_partial_shipit_config_is_reported(monkeypatch):
    """URL girilmis ama kimlik yok — sessiz kalirsa saatlerce aranir."""
    cfg = _reload_with_env(monkeypatch, {
        "GLS_MODE": "test", **TT_REAL,
        "SHIPIT_BASE_URL": "https://customer.gls-group.eu:8443/backend",
    })
    assert any("SHIPIT_USERNAME" in i for i in cfg.readiness_check())


def test_partial_tt_config_is_reported(monkeypatch):
    cfg = _reload_with_env(monkeypatch, {
        "GLS_MODE": "test", **SHIPIT_REAL, "TT_USERNAME": "real-user",
    })
    assert any("TT_USERNAME/TT_PASSWORD" in i for i in cfg.readiness_check())


def test_nl_password_length_limit(monkeypatch):
    """GLS NL sifre alani en fazla 20 karakter — canli testle dogrulandi."""
    cfg = _reload_with_env(monkeypatch, {
        "GLS_MODE": "test", **NL_REAL, "NL_PASSWORD": "x" * 21,
    })
    assert any("20 karakterden uzun" in i for i in cfg.readiness_check())


# ---------------------------------------------------------------- oturum anahtari
def test_users_stay_logged_in_across_a_restart(monkeypatch, tmp_path):
    """Anahtar .env'de verilmese bile iki acilista AYNI olmali.

    Eskiden her acilista rastgele uretiliyordu: her aktarim herkesi disari
    atiyordu. Kullanicilar sebebini bilmeden yeniden giris yapiyordu.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    first = _reload_with_env(monkeypatch, {"GLS_MODE": "prod", **SHIPIT_REAL}).AUTH_SECRET_KEY
    second = _reload_with_env(monkeypatch, {"GLS_MODE": "prod", **SHIPIT_REAL}).AUTH_SECRET_KEY

    assert first == second


def test_the_session_key_is_not_readable_by_others(monkeypatch, tmp_path):
    """Anahtari ele geciren biri istedigi kullanici adina oturum imzalayabilir."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _reload_with_env(monkeypatch, {"GLS_MODE": "prod", **SHIPIT_REAL})

    mode = (tmp_path / ".gls_pod" / "session_key").stat().st_mode
    assert mode & 0o077 == 0


def test_prod_warns_when_the_key_cannot_be_stored(monkeypatch, tmp_path):
    """Yazilamiyorsa eski davranisa duser — bu SESSIZ kalmamali."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "yok")
    monkeypatch.setattr(Path, "mkdir", _raise_readonly)

    cfg = _reload_with_env(monkeypatch, {
        "GLS_MODE": "prod", **SHIPIT_REAL, "AUTH_COOKIE_SECURE": "true",
    })

    assert any("Oturum anahtari" in i for i in cfg.readiness_check())


def test_prod_mode_requires_secure_cookies(monkeypatch):
    cfg = _reload_with_env(monkeypatch, {
        "GLS_MODE": "prod", **SHIPIT_REAL,
        "AUTH_SECRET_KEY": "a-fixed-secret", "AUTH_COOKIE_SECURE": "false",
    })
    assert any("AUTH_COOKIE_SECURE" in i for i in cfg.readiness_check())


def test_prod_mode_ready_when_everything_set(monkeypatch):
    cfg = _reload_with_env(monkeypatch, {
        "GLS_MODE": "prod", **SHIPIT_REAL, **TT_REAL, **NL_REAL,
        "AUTH_SECRET_KEY": "a-fixed-secret", "AUTH_COOKIE_SECURE": "true",
    })
    assert cfg.configured_providers() == ["shipit", "tt", "nl"]
    assert cfg.readiness_check() == []
