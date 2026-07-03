#!/usr/bin/env python3
"""hash_webhook_tokens — NX-22: hashea in-place los tokens de webhook en claro.

El token NO cambia: la alerta de LuxAlgo sigue enviando el mismo valor y la
validación (dual-read) compara hash(presentado) == hash almacenado. Solo se
elimina el texto en claro de la DB. Sin re-alta.

⚠ Requiere WEBHOOK_TOKEN_SALT estable en el .env del servidor ANTES de correr:
cambiar el salt después invalida los hashes (habría que regenerar tokens y
re-dar de alta las alertas).

Uso (dry-run por defecto):
  python -m scripts.hash_webhook_tokens            # muestra qué haría
  python -m scripts.hash_webhook_tokens --apply    # hashea + audit
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_token
from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.services.audit_service import AuditService


async def hash_existing_tokens(db, salt: str) -> int:
    """Hashea todas las filas con token en claro. Devuelve cuántas convirtió."""
    rows = (await db.execute(
        select(Strategy).where(Strategy.webhook_token.is_not(None))
    )).scalars().all()
    n = 0
    for s in rows:
        s.webhook_token_hash = hash_token(s.webhook_token, salt)
        s.webhook_token = None
        await db.flush()
        await AuditService().log(
            db, actor="hash_webhook_tokens", action="UPDATE",
            object_type="Strategy", object_id=s.strategy_id,
            new_value={"webhook_token": "hashed"},
            reason="NX-22: token hasheado in-place (misma alerta sigue valida)",
        )
        n += 1
    return n


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Strategy.strategy_id).where(Strategy.webhook_token.is_not(None))
        )).scalars().all()
        print(f"=== hash_webhook_tokens [{'APPLY' if args.apply else 'DRY-RUN'}] "
              f"— {len(rows)} token(s) en claro ===")
        for sid in rows:
            print(f"  · {sid}")
        if not args.apply:
            print("\nℹ️  DRY-RUN — nada escrito. Los tokens NO cambian al aplicar; "
                  "las alertas siguen válidas.")
            return
        n = await hash_existing_tokens(db, settings.WEBHOOK_TOKEN_SALT)
        await db.commit()
        print(f"\n✅ {n} token(s) hasheado(s). Verifica una señal real después.")


if __name__ == "__main__":
    asyncio.run(main())
