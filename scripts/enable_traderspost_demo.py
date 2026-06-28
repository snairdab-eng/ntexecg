#!/usr/bin/env python3
"""enable_traderspost_demo — activa el dispatch a TradersPost (cuenta DEMO) en las
estrategias YA validadas que tengan webhook configurado.

Por cada StrategyProfile elegible pone: traderspost_enabled=True, dry_run=False.
NO toca status, SL/TP, ventana, ni la calibración. SOLO habilita el envío.

Gates de seguridad:
  - Salta estrategias SIN traderspost_webhook_url (no se puede despachar).
  - Salta retired/quarantined.
  - Reporta el estado del GlobalProfile (si global dry_run=True o traderspost=False,
    el flip NO surte efecto: traderspost = global AND estrategia; dry_run = global OR estrategia).
  - dry-run por defecto; requiere --apply; backup JSON + auditoría.

Uso:
  python -m scripts.enable_traderspost_demo                       # dry-run, todas las elegibles
  python -m scripts.enable_traderspost_demo --only NQ5m_ConfirmationAny,MicroYM15m_Contrarian
  python -m scripts.enable_traderspost_demo --apply              # escribe (con backup)
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.audit_service import AuditService

HIDDEN = {"retired", "quarantined"}


async def _global(db):
    try:
        from app.services.repositories import get_global_profile
        return await get_global_profile(db)
    except Exception:
        return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribe (si no, dry-run)")
    ap.add_argument("--only", default="", help="lista de strategy_id separada por comas")
    args = ap.parse_args()
    apply = args.apply
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    print(f"=== enable_traderspost_demo — modo: {'APPLY' if apply else 'DRY-RUN'} ===\n")

    async with AsyncSessionLocal() as db:
        gp = await _global(db)
        if gp is not None:
            print(f"GlobalProfile: dry_run={gp.dry_run}  traderspost_enabled={gp.traderspost_enabled}")
            if gp.dry_run or not gp.traderspost_enabled:
                print("⚠️  El global neutraliza el dispatch (dry_run=global OR estrategia; "
                      "traderspost=global AND estrategia). Revisa el global antes de aplicar.")
        print()

        strategies = (await db.execute(
            select(Strategy).order_by(Strategy.asset_symbol, Strategy.created_at)
        )).scalars().all()

        to_change, skipped, already, backup = [], [], [], []
        for s in strategies:
            if only and s.strategy_id not in only:
                continue
            if s.status in HIDDEN:
                skipped.append((s.strategy_id, f"status={s.status}"))
                continue
            prof = (await db.execute(
                select(StrategyProfile).where(StrategyProfile.strategy_id == s.strategy_id)
            )).scalar_one_or_none()
            if prof is None:
                skipped.append((s.strategy_id, "sin StrategyProfile"))
                continue
            if not (prof.traderspost_webhook_url or "").strip():
                skipped.append((s.strategy_id, "sin webhook_url"))
                continue
            if prof.traderspost_enabled and not prof.dry_run:
                already.append(s.strategy_id)
                continue
            before = {"strategy_id": s.strategy_id, "traderspost_enabled": prof.traderspost_enabled,
                      "dry_run": prof.dry_run, "version": prof.version}
            to_change.append((s, prof, before))

        for s, prof, before in to_change:
            print(f"── {s.strategy_id} [{s.asset_symbol}] status={s.status}")
            print(f"   traderspost_enabled {before['traderspost_enabled']} → True")
            print(f"   dry_run             {before['dry_run']} → False")
            print(f"   webhook: {'set' if prof.traderspost_webhook_url else 'NONE'}\n")
            backup.append(before)
            if apply:
                prof.traderspost_enabled = True
                prof.dry_run = False
                prof.version = (prof.version or 1) + 1
                prof.updated_by = "enable_traderspost_demo"
                await AuditService().log(
                    db, actor="enable_traderspost_demo", action="UPDATE",
                    object_type="StrategyProfile", object_id=s.strategy_id,
                    old_value=before,
                    new_value={"traderspost_enabled": True, "dry_run": False},
                    reason="Activar dispatch TradersPost (cuenta DEMO)",
                )

        if apply and backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"traderspost_enable_backup_{ts}.json"
            bp.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
            print(f"🗄️  Backup de {len(backup)} perfiles → {bp}")
        if apply:
            await db.commit()
            print("✅ Cambios escritos.\n")
        else:
            await db.rollback()
            print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.\n")

        print("================ RESUMEN ================")
        print(f"A activar ({len(to_change)}): {[s.strategy_id for s, _, _ in to_change]}")
        print(f"Ya activas ({len(already)}): {already}")
        print(f"Saltadas ({len(skipped)}): {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
