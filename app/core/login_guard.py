"""SEC-1 Tarea 1 — rate-limit + lockout del login (EN MEMORIA, sin deps).

Contador por IP y por usuario en una ventana de 15 min. Tras SOFT fallos →
backoff exponencial (solo se DUERME en producción); tras HARD → lockout 15 min
(429 genérico, sin revelar si el usuario existe). Best-effort en memoria: se
pierde al reiniciar el servicio (reiniciar = reset del lockout).
"""
from __future__ import annotations

import time

from fastapi import Request

WINDOW_S = 15 * 60          # ventana del contador
SOFT = 5                    # a partir de aquí, backoff
HARD = 10                   # a partir de aquí, lockout
LOCK_S = 15 * 60            # duración del lockout
_BACKOFF_CAP_S = 30.0

# {"ip:1.2.3.4" | "user:admin": [timestamps]}
_fails: dict[str, list[float]] = {}


def client_ip(request: Request) -> str:
    """IP real detrás de Cloudflare (CF-Connecting-IP) con fallback a client.host."""
    cf = request.headers.get("cf-connecting-ip")
    if cf and cf.strip():
        return cf.strip()
    return request.client.host if request.client else "unknown"


def _prune(key: str, now: float) -> list[float]:
    xs = [t for t in _fails.get(key, []) if now - t < WINDOW_S]
    if xs:
        _fails[key] = xs
    else:
        _fails.pop(key, None)
    return xs


def _keys(ip: str, user: str) -> tuple[str, str]:
    return f"ip:{ip}", f"user:{(user or '').strip().lower()}"


def lock_remaining(ip: str, user: str) -> float:
    """Segundos de lockout restantes (0 = no bloqueado). Bloqueado si IP o
    usuario acumulan ≥ HARD fallos en la ventana."""
    now = time.time()
    rem = 0.0
    for k in _keys(ip, user):
        xs = sorted(_prune(k, now))
        if len(xs) >= HARD:
            t_hard = xs[HARD - 1]          # momento del fallo nº HARD
            rem = max(rem, LOCK_S - (now - t_hard))
    return max(0.0, rem)


def backoff_seconds(ip: str, user: str) -> float:
    """Delay de backoff exponencial para SOFT ≤ fallos < HARD (2,4,8,…, tope)."""
    now = time.time()
    b = 0.0
    for k in _keys(ip, user):
        c = len(_prune(k, now))
        if SOFT <= c < HARD:
            b = max(b, min(_BACKOFF_CAP_S, 2.0 ** (c - SOFT + 1)))
    return b


def record_failure(ip: str, user: str) -> None:
    now = time.time()
    for k in _keys(ip, user):
        _fails.setdefault(k, []).append(now)


def record_success(ip: str, user: str) -> None:
    for k in _keys(ip, user):
        _fails.pop(k, None)


def reset() -> None:
    """Limpia todo el estado (tests / reinicio)."""
    _fails.clear()
