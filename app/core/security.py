"""NX-22 — hashing de tokens de webhook (SHA-256 + WEBHOOK_TOKEN_SALT).

⚠ El salt debe ser estable: cambiarlo invalida todos los hashes almacenados
(habría que regenerar tokens y re-dar de alta las alertas en LuxAlgo).
"""
import hashlib
import hmac


def hash_token(token: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}{token}".encode()).hexdigest()


def verify_token(token: str, salt: str, token_hash: str) -> bool:
    """Comparación en tiempo constante del hash del token presentado."""
    return hmac.compare_digest(hash_token(token, salt), token_hash or "")
