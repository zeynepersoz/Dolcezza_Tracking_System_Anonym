"""Sayfa penceresi: `?page=` okumasi + kirpma, tek yerde.

Takip listesi icin yazilmis mantigin AYNISI; giris kaydi sayfalanirken ikinci
kez elle yazilsaydi buradaki iki koruma orada unutulurdu:

* `?page=abc` ve `?page=-4` ILK SAYFAYA duser — ciplak `int()` 500 dondururdu,
  eksi bir sayfa da SQL'e negatif `OFFSET` gecirirdi.
* Suzgec daralinca kullanici sondan tasan bir sayfada kalabilir; bos tablo
  yerine son DOLU sayfaya cekilir.
"""
from __future__ import annotations

from fastapi import Request


def window(request: Request, total: int, size: int) -> dict:
    """`page`, `pages`, `offset`, `page_from`, `page_to`.

    `page_from`/`page_to` 1 tabanli ve KAPALI aralik ("1-100 / 1243"); kayit
    yoksa ikisi de 0 olur, sablon "0-0" yazmasin diye satiri gizleyebilir.
    """
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    pages = max(1, -(-total // size))
    page = min(page, pages)
    offset = (page - 1) * size
    shown = max(0, min(size, total - offset))
    return {
        "page": page,
        "pages": pages,
        "offset": offset,
        "page_from": offset + 1 if shown else 0,
        "page_to": offset + shown,
    }
