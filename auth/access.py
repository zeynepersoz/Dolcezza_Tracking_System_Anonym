# -*- coding: utf-8 -*-
"""Sayfa -> rol haritasi: yetkinin TEK dogruluk kaynagi.

Menuyu gizleyen kural ile 403 veren kural ayni sozlukten okunur. Iki ayri liste
tutuldugunda klasik hata cikar: baglanti menude gorunmez ama URL elle yazilinca
calisir. `web/routers/settings.py:_roles_for` ayni deseni bolum bazinda zaten
uyguluyordu; burasi onun panel genelindeki hali.

Kullanim:
    web/main.py       -> `Depends(require_role(*access.roles_for("/labels")))`
    web/templating.py -> Jinja geneli `can(role, path)`
"""
from __future__ import annotations

from auth.db import ROLES

# Yazma/tetikleme yetkisi olan roller. `viewer` bilerek disarida.
WRITERS = ("admin", "operator")

PAGE_ROLES: dict[str, tuple[str, ...]] = {
    "/": ROLES,
    "/tracking": ROLES,
    "/pod": ROLES,
    "/problems": ROLES,
    "/archive": ROLES,
    "/reports": ROLES,
    "/reports/delivery-times": ROLES,
    "/reports/parcel-shop": ROLES,
    "/reports/completion": ROLES,
    "/labels": WRITERS,
    "/dispatch": WRITERS,
    "/settings": WRITERS,
}


def roles_for(path: str) -> tuple[str, ...]:
    """Tanimsiz sayfa KAPALI sayilir — yeni bir sayfa yanlislikla acilmasin."""
    return PAGE_ROLES.get(path, WRITERS)


def can(role: str, path: str) -> bool:
    return role in roles_for(path)
