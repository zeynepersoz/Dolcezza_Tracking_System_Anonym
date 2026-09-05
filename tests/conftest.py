# -*- coding: utf-8 -*-
"""Tum test dosyalari icin ortak mock GLS sunucusu.

Session basina bir kez ayaga kalkar; test_clients.py ve E2E senaryolari paylasir.

Port SABIT DEGILDIR: gelistirme sirasinda `uvicorn gls_api.mock_server:app
--port 8788` zaten calisiyor olabilir (hatta projenin eski bir kopyasindan,
eski semayla). Sabit porta baglanmak testleri sessizce yanlis sunucuya
yonlendirir; bu yuzden bos bir port secilip ortama yazilir ve test modulleri
onu okur.
"""
import os
import socket
import threading
import time

import pytest
import uvicorn

from gls_api.mock_server import app

HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


PORT = int(os.environ.setdefault("GLS_MOCK_PORT", str(_free_port())))
BASE_URL = f"http://{HOST}:{PORT}"


@pytest.fixture(scope="session", autouse=True)
def mock_server():
    cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError(f"Mock sunucu {BASE_URL} adresinde baslatilamadi")
    yield
    server.should_exit = True
