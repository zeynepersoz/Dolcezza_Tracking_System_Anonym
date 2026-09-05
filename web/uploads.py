# -*- coding: utf-8 -*-
"""Dosya yukleme ucları arası ortak sinir.

`UploadFile.read()` sinirsizdir: kotu niyetli (ya da kazayla secilen buyuk)
bir dosya sunucuyu bellek/disk tuketerek durdurabilir. `pod.py` ve
`tracking.py`nin ikisi de dosya kabul ettigi icin tek yerde tutuluyor —
kopyalanirsa biri guncellenip digeri unutulur.
"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile

import i18n


async def read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """`file.read()` yerine: sinira ulasilinca PARCA PARCA okumayi keser.

    Tek seferde `read()` limiti asilsa bile once TUMUNU belleğe alir, kontrol
    is isten gectikten sonra gelir. 1 MB'lik parcalarla okunur, toplam limiti
    gecen ilk parcada durulur — buyuk dosya sunucuyu asla tam belleğe almaz.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, i18n.t("err.file_too_large", limit_mb=max_bytes // (1024 * 1024)))
        chunks.append(chunk)
    return b"".join(chunks)
