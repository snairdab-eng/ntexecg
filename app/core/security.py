import hashlib


def hash_token(token: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}{token}".encode()).hexdigest()


def verify_token(token: str, salt: str, token_hash: str) -> bool:
    return hash_token(token, salt) == token_hash
