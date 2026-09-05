# -*- coding: utf-8 -*-
"""/settings/notify sekmesi ve "Özeti şimdi gönder" dugmesi.

`tests/test_settings_web.py` deseni: router kendi `require_role("admin")`ini
tasidigi icin `get_current_user` override edilir.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import i18n
from auth.dependencies import get_current_user
from gls_api import config
from settings.store import SettingsStore
from web import deps
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


class _FakeDigest:
    """Gercek `DailyDigest` yerine: testler APScheduler'a ihtiyac duymasin."""
    def __init__(self, result=(True, "Gönderildi (1 alıcı)")):
        self.result = result
        self.calls = 0
        self.config: dict = {}
        self.last_run_at = None
        self.last_error = None
        self.last_detail = None

    def send_now(self):
        self.calls += 1
        return self.result

    def reconfigure(self, **kw):
        self.config = kw


@pytest.fixture
def digest():
    return _FakeDigest()


@pytest.fixture
def client(store, digest):
    app = FastAPI()
    app.include_router(settings_router.router)
    app.state.digest = digest

    async def fake_user(request: Request):
        request.state.user = {"uid": 1, "username": "test", "role": "admin"}
        return request.state.user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app)


# ----------------------------------------------------------------- sayfa
def test_notify_section_renders(client):
    assert client.get("/settings/notify").status_code == 200


def test_missing_mail_credentials_are_warned(client):
    assert i18n.t("settings.mail_missing", "tr")[:20] in client.get("/settings/notify").text


def test_last_run_is_shown(client, digest):
    digest.last_run_at = "2026-08-05T08:00:00"
    digest.last_detail = "Gönderildi (2 alıcı)"
    page = client.get("/settings/notify").text
    # Depolanan damga UTC; ekranda yerel saat ve okunakli bicim (bkz. timez).
    assert "05.08.2026 11:00" in page and "2 alıcı" in page


# --------------------------------------------------------------- kaydetme
def test_recipients_are_stored_as_csv(client, store):
    client.post("/settings/notify",
                data={"NOTIFY_RECIPIENTS": "a@x.com, b@x.com"})
    assert store.raw()["NOTIFY_RECIPIENTS"] == "a@x.com,b@x.com"
    assert config.notify_recipients() == ["a@x.com", "b@x.com"]


def test_invalid_hour_is_rejected(client):
    r = client.post("/settings/notify", data={"NOTIFY_HOURS": "25"},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_one_bad_hour_rejects_the_whole_list(client, store):
    """Sessizce dusseydi ayar kaydedilmis gorunur, o saat hic tetiklenmezdi."""
    r = client.post("/settings/notify", data={"NOTIFY_HOURS": "9, 25"},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert "NOTIFY_HOURS" not in store.raw()


def test_save_reconfigures_the_scheduler(client, digest):
    client.post("/settings/notify",
                data={"NOTIFY_ENABLED": "1", "NOTIFY_HOURS": "9, 18",
                      "NOTIFY_RECIPIENTS": "a@x.com"})
    assert digest.config["enabled"] is True
    assert digest.config["hours"] == [9, 18]
    assert digest.config["recipients"] == ["a@x.com"]


# ------------------------------------------------------------- test dugmesi
def test_button_sends_the_digest(client, digest):
    r = client.post("/settings/notify/test")
    assert r.status_code == 200 and digest.calls == 1
    assert "Gönderildi" in r.text


def test_missing_recipients_answer_in_turkish(client, digest):
    """500 degil, okunabilir bir satir donmeli."""
    digest.result = (False, "Alıcı listesi boş")
    r = client.post("/settings/notify/test")
    assert r.status_code == 200 and "Alıcı listesi boş" in r.text
