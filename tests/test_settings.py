# -*- coding: utf-8 -*-
"""Ayar deposu ve canli config katmani.

Bu katmanin tek varlik sebebi su: panelden kaydedilen bir deger, konteyner
yeniden baslamadan `config.X` uzerinden gorunmeli. Testlerin cogu bunu
`importlib.reload` OLMADAN dogruluyor — reload ile gecen bir test hicbir sey
kanitlamazdi.
"""
import os
import stat

import pytest

from gls_api import config, providers
from settings import apply as settings_apply
from settings.schema import ALL_FIELDS, BY_SLUG, TABS
from settings.store import SettingsStore


@pytest.fixture
def store(tmp_path):
    return SettingsStore(tmp_path / "settings.db")


@pytest.fixture(autouse=True)
def _clean_config():
    yield
    config.clear_overrides()
    providers.reset_clients()


# ---------------------------------------------------------------- depo
def test_roundtrip(store):
    assert store.save_section("mssql", {"MSSQL_SERVER": "192.0.2.30"}) == []
    assert store.raw()["MSSQL_SERVER"] == "192.0.2.30"


def test_unknown_key_is_ignored(store):
    """Duzmece bir POST beyaz listeyi asamaz — AUTH_SECRET_KEY enjekte edilemez."""
    store.save_section("mssql", {"MSSQL_SERVER": "x", "AUTH_SECRET_KEY": "kotu"})
    assert "AUTH_SECRET_KEY" not in store.raw()


def test_int_field_is_typed(store):
    store.save_section("mssql", {"MSSQL_PORT": "1434"})
    assert store.typed()["MSSQL_PORT"] == 1434


def test_non_numeric_int_is_rejected(store):
    errors = store.save_section("mssql", {"MSSQL_PORT": "abc"})
    assert errors and "sayı olmalı" in errors[0]
    assert store.raw() == {}          # hata varsa HICBIR SEY yazilmaz


def test_below_minimum_is_rejected(store):
    """0 dakika aralik GLS'in saatlik sinirini doverdi."""
    errors = store.save_section("tracking", {"TRACKER_INTERVAL_MINUTES": "0"})
    assert errors and "en az 1" in errors[0]


def test_empty_secret_keeps_existing(store):
    store.save_section("mssql", {"MSSQL_PASSWORD": "gizli"})
    store.save_section("mssql", {"MSSQL_PASSWORD": "", "MSSQL_SERVER": "x"})
    assert store.raw()["MSSQL_PASSWORD"] == "gizli"


def test_explicit_clear_removes_secret(store):
    store.save_section("mssql", {"MSSQL_PASSWORD": "gizli"})
    store.save_section("mssql", {"clear_MSSQL_PASSWORD": "1"})
    assert "MSSQL_PASSWORD" not in store.raw()


def test_for_display_never_returns_secret_value(store):
    store.save_section("mssql", {"MSSQL_PASSWORD": "gizli", "MSSQL_SERVER": "192.0.2.30"})
    rows = {r["key"]: r for r in store.for_display("mssql")}
    assert rows["MSSQL_PASSWORD"]["value"] == ""
    assert rows["MSSQL_PASSWORD"]["has_value"] is True
    assert rows["MSSQL_SERVER"]["value"] == "192.0.2.30"   # sir olmayan gorunur


def test_audit_row_has_no_value(store):
    store.save_section("mssql", {"MSSQL_PASSWORD": "gizli"})
    rows = store.conn.execute("SELECT * FROM settings_audit").fetchall()
    assert [dict(r)["key"] for r in rows] == ["MSSQL_PASSWORD"]
    assert "gizli" not in str([dict(r) for r in rows])


def test_db_file_is_owner_only(tmp_path):
    path = tmp_path / "settings.db"
    SettingsStore(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_csv_field_is_normalised(store):
    store.save_section("mssql", {"MSSQL_SEASON_VIEWS": " a , ,b "})
    assert store.raw()["MSSQL_SEASON_VIEWS"] == "a,b"


def test_invalid_select_choice_is_rejected(store):
    errors = store.save_section("whatsapp", {"WA_PROVIDER": "yokboyle"})
    assert errors


# ---------------------------------------------------------------- canli config
def test_override_changes_config_without_reload():
    """Tasarimin varlik sebebi: reload YOK, yine de yeni deger gorunuyor."""
    before = config.MSSQL_SERVER
    config.apply_overrides({"MSSQL_SERVER": "1.2.3.4"})
    assert config.MSSQL_SERVER == "1.2.3.4"
    config.clear_overrides()
    assert config.MSSQL_SERVER == before


def test_predicate_reacts_to_override():
    """Yuklem canli olmasa `web/deps.py:mssql_ready` eski cevabi verirdi."""
    config.apply_overrides({"MSSQL_SERVER": ""})
    assert config.mssql_configured() is False
    config.apply_overrides({
        "MSSQL_SERVER": "s", "MSSQL_DATABASE": "d", "MSSQL_USERNAME": "u",
        "MSSQL_PASSWORD": "p", "MSSQL_SEASON_VIEWS": "Vision_FA26",
    })
    assert config.mssql_configured() is True


def test_empty_store_keeps_env_behaviour(store):
    before = config.MSSQL_SERVER
    settings_apply.apply(store)
    assert config.MSSQL_SERVER == before


def test_mock_mode_drops_gls_overrides():
    """Bir gelistirme makinesi asla canli api.gls.nl'e istek atmamali."""
    assert config.IS_MOCK, "bu test GLS_MODE=mock ile calisir"
    before = config.NL_USERNAME
    config.apply_overrides({"NL_USERNAME": "canli", "MSSQL_SERVER": "1.2.3.4"})
    assert config.NL_USERNAME == before          # dusuruldu
    assert config.MSSQL_SERVER == "1.2.3.4"      # GLS disi uygulandi
    assert config.ignored_override_keys({"NL_USERNAME": "canli"}) == ["NL_USERNAME"]


def test_unknown_attribute_still_raises():
    with pytest.raises(AttributeError):
        config.BOYLE_BIR_AYAR_YOK


def test_readiness_check_unaffected_by_unrelated_override():
    before = config.readiness_check()
    config.apply_overrides({"MAIL_SENDER": "a@b.c"})
    assert config.readiness_check() == before


def test_schema_keys_match_config_defaults():
    """`__getattr__`in tek zayif noktasi: sema ile config'in ayrisması.

    Ayrisirsa panel var olmayan bir ayari kaydeder ve sessizce hicbir sey olmaz.
    """
    missing = sorted(k for k in ALL_FIELDS if k not in config._ENV_DEFAULTS)
    assert missing == []


def test_every_tab_has_a_template():
    from pathlib import Path
    for slug, _ in TABS:
        assert Path(f"web/templates/settings/{slug}.html").exists(), slug


def test_sections_and_tabs_agree():
    assert [s for s, _ in TABS if s in BY_SLUG] == list(BY_SLUG)


# ---------------------------------------------------------------- onbellek dusurme
def test_reset_clients_drops_cache():
    """Kimlik degistiginde bu cagrilmazsa kullanici 'kaydettim ama degismedi' der."""
    first = providers.get_shipit()
    assert providers.get_shipit() is first
    providers.reset_clients()
    assert providers.get_shipit() is not first


def test_apply_resets_provider_cache(store):
    first = providers.get_shipit()
    settings_apply.apply(store)
    assert providers.get_shipit() is not first


def test_apply_does_not_log_values(store, caplog):
    store.save_section("mssql", {"MSSQL_PASSWORD": "cok-gizli"})
    with caplog.at_level("INFO"):
        settings_apply.apply(store)
    assert "cok-gizli" not in caplog.text


# ---------------------------------------------------------------- tracker
class _FakeScheduler:
    running = True

    def __init__(self):
        self.calls = []

    def reschedule_job(self, job_id, **kwargs):
        self.calls.append((job_id, kwargs))


def _tracker():
    from tracking.scheduler import Tracker
    t = Tracker(db=None, interval_minutes=15)
    t.scheduler = _FakeScheduler()
    return t


def test_reconfigure_updates_limits_and_interval():
    t = _tracker()
    t.reconfigure(interval_minutes=5, max_per_tick=100, max_pod_per_tick=10)
    assert (t.interval_minutes, t.max_per_tick, t.max_pod_per_tick) == (5, 100, 10)
    assert t.scheduler.calls == [("tracker_tick", {"trigger": "interval", "minutes": 5})]


def test_reconfigure_same_interval_does_not_reschedule():
    t = _tracker()
    t.reconfigure(interval_minutes=15, max_per_tick=100)
    assert t.scheduler.calls == []
    assert t.max_per_tick == 100


def test_reconfigure_safe_before_scheduler_starts():
    t = _tracker()
    t.scheduler.running = False
    t.reconfigure(interval_minutes=5)          # patlamamali
    assert t.interval_minutes == 5
    assert t.scheduler.calls == []
