"""SEC-1 Tarea 2 — TOTP (RFC 6238) en Python PURO, cero dependencias.

2FA opcional para el login: `settings.UI_TOTP_SECRET` vacío = apagado
(comportamiento actual intacto). Con secreto, `authenticate` exige el código de
6 dígitos (ventana ±1 periodo de 30s). Provisioning: `scripts/setup_totp.py`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def random_secret() -> str:
    """Secreto base32 (160 bits) para una app de autenticación."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def verify_totp(secret_b32: str, code: str, *, window: int = 1,
                step: int = 30, t: float | None = None) -> bool:
    """Valida `code` contra `secret_b32` en la ventana ±`window` periodos.
    Comparación en tiempo constante. Cualquier entrada mal formada → False."""
    if not secret_b32 or not code:
        return False
    code = str(code).strip()
    if not (code.isdigit() and len(code) == 6):
        return False
    counter = int((t if t is not None else time.time()) // step)
    for w in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret_b32, counter + w), code):
            return True
    return False


def provisioning_uri(secret_b32: str, name: str,
                     issuer: str = "NTEXECG") -> str:
    """URI otpauth:// para el QR/alta manual en la app de autenticación."""
    return (f"otpauth://totp/{quote(issuer)}:{quote(name)}"
            f"?secret={secret_b32}&issuer={quote(issuer)}&period=30&digits=6")
