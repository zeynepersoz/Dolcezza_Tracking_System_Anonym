# -*- coding: utf-8 -*-
"""Yukleme boyut siniri: `web.uploads.read_limited`.

`UploadFile.read()` sinirsizdir — kotu niyetli/yanlislikla secilen buyuk bir
dosya sunucuyu bellek/disk tuketerek durdurabilir (bkz. guvenlik denetimi,
2026-08-24)."""
import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from web.uploads import read_limited


def _upload(data: bytes) -> UploadFile:
    return UploadFile(filename="f.bin", file=io.BytesIO(data))


def test_reads_content_under_the_limit():
    content = asyncio.run(read_limited(_upload(b"abc" * 10), max_bytes=1000))
    assert content == b"abc" * 10


def test_rejects_content_over_the_limit():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_limited(_upload(b"x" * 2_000_000), max_bytes=1_000_000))
    assert exc.value.status_code == 413


def test_exactly_at_the_limit_is_accepted():
    content = asyncio.run(read_limited(_upload(b"y" * 1000), max_bytes=1000))
    assert len(content) == 1000
