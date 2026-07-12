"""SEC-1b (Tarea 5) — store efímero de un solo uso para el token del webhook.

El token en claro NO viaja en el query string del redirect (quedaba en logs de
Cloudflare/proxy e historial del navegador). Patrón: se guarda aquí con un id
aleatorio y TTL corto; el redirect lleva SOLO el id; la página destino lo pide
por fetch (sesión autenticada) y lo muestra UNA vez. Leerlo lo DESTRUYE; expirar
lo destruye. En memoria, best-effort (se pierde al reiniciar — que es lo
correcto para un secreto efímero).
"""
from __future__ import annotations

import secrets
import time

TTL_S = 60.0

# {id: (token, expira_epoch)}
_store: dict[str, tuple[str, float]] = {}


def _prune(now: float) -> None:
    for k in [k for k, (_, exp) in _store.items() if now > exp]:
        _store.pop(k, None)


def put(token: str) -> str:
    """Guarda `token` y devuelve un id aleatorio (no secreto) para el redirect."""
    now = time.time()
    _prune(now)
    tid = secrets.token_urlsafe(12)
    _store[tid] = (token, now + TTL_S)
    return tid


def take(tid: str) -> str | None:
    """Devuelve el token UNA vez (lo destruye) o None si no existe/expiró."""
    now = time.time()
    _prune(now)
    item = _store.pop(tid, None)          # pop = un solo read
    if item is None:
        return None
    token, exp = item
    return token if now <= exp else None


def reset() -> None:
    """Solo para tests."""
    _store.clear()
