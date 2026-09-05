# -*- coding: utf-8 -*-
"""/settings uclari.

Router kendi icinde `require_role("admin")` tasidigi icin (tests/test_dispatch_web
desenindeki gibi auth'suz include etmek mumkun degil) `get_current_user`
override edilir — boylece admin/operator ayrimi da test edilebilir.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import i18n
from auth.dependencies import get_current_user
from gls_api import config
from settings.store import SettingsStore
from web import deps
from web.routers import auth as auth_router
from web.routers import settings as settings_router


@pytest.fixture(autouse=True)
def _clean_config():
    yield
    config.clear_overrides()


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SettingsStore(tmp_path / "settings.db")
    monkeypatch.setattr(deps, "_settings_store", s)
    return s


def _client(role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(settings_router.router)
    app.include_router(auth_router.router)

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "test", "role": role}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app)


@pytest.fixture
def client(store):
    return _client()


# ---------------------------------------------------------------- gezinme
def test_root_redirects_to_first_tab(client):
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/settings/mssql"


@pytest.mark.parametrize("slug", ["mssql", "tracking", "gls", "notify", "mail",
                                  "whatsapp", "users"])
def test_every_section_renders(client, slug):
    """Sablon yazim hatasi baska hicbir yerde yakalanmiyor."""
    r = client.get(f"/settings/{slug}")
    assert r.status_code == 200


def test_unknown_section_is_404(client):
    assert client.get("/settings/yokboyle").status_code == 404


# ---------------------------------------------------------------- roller
# Yetki tek kaynaktan gelir: `settings/schema.py:Section.roles`. Varsayilan
# ("admin",) oldugu icin ileride eklenen bir bolum kazara operatore acilmaz.

@pytest.fixture
def op_client(store):
    return _client(role="operator")


@pytest.mark.parametrize("slug", ["mssql", "ui", "tracking", "gls", "mail",
                                  "whatsapp", "users"])
def test_operator_cannot_see_admin_sections(op_client, slug):
    assert op_client.get(f"/settings/{slug}").status_code == 403


@pytest.mark.parametrize("slug", ["notify", "gls_inquiry"])
def test_operator_sees_the_mail_sections(op_client, slug):
    """Otomatik mailler ekip isi: gunluk ozet ve GLS'e POD sorgusu."""
    assert op_client.get(f"/settings/{slug}").status_code == 200


def test_operator_cannot_save_an_admin_section(op_client):
    r = op_client.post("/settings/mssql", data={"MSSQL_SERVER": "192.0.2.30"},
                       follow_redirects=False)
    assert r.status_code == 403


def test_operator_can_save_the_notify_section(op_client):
    r = op_client.post("/settings/notify", data={"NOTIFY_HOURS": "9,18"},
                       follow_redirects=False)
    assert r.status_code == 303


def test_operator_can_save_the_gls_inquiry_section(op_client):
    r = op_client.post("/settings/gls_inquiry", data={"GLS_INQUIRY_WAIT_DAYS": "3"},
                       follow_redirects=False)
    assert r.status_code == 303


def test_operator_lands_on_notify(op_client):
    r = op_client.get("/settings", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/settings/notify")


def test_operator_tab_strip_has_only_the_mail_sections(op_client):
    """Sekme seridi de suzuluyor — operatore acilmayan bir sekmeye link verilmemeli."""
    body = op_client.get("/settings/notify").text
    assert "/settings/notify" in body
    assert "/settings/gls_inquiry" in body
    assert "/settings/mssql" not in body


def test_operator_may_send_the_digest_by_hand(op_client, monkeypatch):
    """Kullanicinin istedigi tek yazma yetkisi bu: "istedikleri zaman mail gonderebilsinler"."""
    r = op_client.post("/settings/notify/test")
    assert r.status_code == 200


@pytest.mark.parametrize("path", ["/settings/mssql/test", "/settings/gls/test",
                                  "/settings/mail/test"])
def test_operator_cannot_run_admin_tests(op_client, path):
    assert op_client.post(path).status_code == 403


# ---------------------------------------------------------------- kaydetme
def test_save_redirects_with_ok(client):
    r = client.post("/settings/mssql", data={"MSSQL_SERVER": "192.0.2.30"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/settings/mssql?ok=")


def test_saved_value_is_live_immediately(client):
    client.post("/settings/mssql", data={"MSSQL_SERVER": "192.0.2.30"})
    assert config.MSSQL_SERVER == "192.0.2.30"


def test_secret_is_never_rendered(client):
    client.post("/settings/mssql", data={"MSSQL_PASSWORD": "cok-gizli"})
    page = client.get("/settings/mssql").text
    assert "cok-gizli" not in page
    assert i18n.t("settings.secret_saved", "tr") in page


def test_empty_secret_keeps_existing(client, store):
    client.post("/settings/mssql", data={"MSSQL_PASSWORD": "gizli"})
    client.post("/settings/mssql", data={"MSSQL_PASSWORD": "", "MSSQL_SERVER": "x"})
    assert store.raw()["MSSQL_PASSWORD"] == "gizli"


def test_non_numeric_is_rejected_with_message(client):
    r = client.post("/settings/mssql", data={"MSSQL_PORT": "abc"},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_zero_interval_is_rejected(client):
    r = client.post("/settings/tracking", data={"TRACKER_INTERVAL_MINUTES": "0"},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_save_to_unknown_section_is_404(client):
    assert client.post("/settings/yokboyle", data={}).status_code == 404


def test_save_resets_provider_cache(client, monkeypatch):
    called = []
    monkeypatch.setattr(settings_router.providers, "reset_clients",
                        lambda: called.append(1))
    client.post("/settings/mssql", data={"MSSQL_SERVER": "x"})
    assert called == [1]


def test_save_does_not_log_secret(client, caplog):
    with caplog.at_level("INFO"):
        client.post("/settings/mssql", data={"MSSQL_PASSWORD": "cok-gizli"})
    assert "cok-gizli" not in caplog.text


def test_gls_save_warns_in_mock_mode(client):
    r = client.post("/settings/gls", data={"NL_USERNAME": "x"}, follow_redirects=False)
    assert "uygulanmad" in r.headers["location"]


# ---------------------------------------------------------------- testler
def test_gls_test_renders_doctor_results(client, monkeypatch):
    monkeypatch.setattr(settings_router.providers, "doctor", lambda: [
        {"name": "nl", "title": "GLS NL", "configured": True, "ok": True,
         "detail": "baglanti basarili"},
    ])
    r = client.post("/settings/gls/test")
    assert r.status_code == 200
    assert "baglanti basarili" in r.text


def test_mssql_test_reports_error_in_turkish(client, monkeypatch):
    """ERPError 500 olarak degil, okunabilir bir satir olarak donmeli."""
    from erp.client import ERPError

    def boom(*a, **kw):
        raise ERPError("MSSQL baglantisi kurulamadi")

    config.apply_overrides({
        "MSSQL_SERVER": "s", "MSSQL_DATABASE": "d", "MSSQL_USERNAME": "u",
        "MSSQL_PASSWORD": "p", "MSSQL_SEASON_VIEWS": "Vision_FA26",
    })
    monkeypatch.setattr(settings_router.ERPClient, "query", boom)
    r = client.post("/settings/mssql/test")
    assert r.status_code == 200
    assert "kurulamadi" in r.text


def test_unconfigured_mssql_test_does_not_touch_network(client, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("aga cikilmamaliydi")

    config.apply_overrides({"MSSQL_SERVER": ""})
    monkeypatch.setattr(settings_router.ERPClient, "query", boom)
    r = client.post("/settings/mssql/test")
    assert i18n.t("check.fields_missing", "tr") in r.text


def test_mail_test_without_credentials_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings_router.mail, "check_credentials",
                        lambda: pytest.fail("kimlik yokken cagrilmamaliydi"))
    r = client.post("/settings/mail/test")
    assert i18n.t("check.mail_missing", "tr") in r.text


# ---------------------------------------------------------------- kullanicilar
def test_users_section_lists_accounts(client, monkeypatch):
    monkeypatch.setattr(deps, "get_auth_db", lambda: type("D", (), {
        "list_users": staticmethod(lambda: [
            {"id": 1, "username": "admin", "role": "admin",
             "is_active": 1, "created_at": "2026-01-01"},
        ])
    })())
    r = client.get("/settings/users")
    assert "admin" in r.text


def test_old_users_url_redirects(client):
    r = client.get("/auth/users", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/settings/users"
