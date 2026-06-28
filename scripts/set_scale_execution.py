#!/usr/bin/env python3
"""set_scale_execution — activa/desactiva la EJECUCIÓN escalonada de UNA estrategia.

Cambia pipeline_config_json["scale_entry"]["mode"] entre "execute" (envía C1+adds a
TradersPost) y "design_only" (solo diseño, una entrada). NO toca nada más.

⚠️ Con mode=execute y el dispatch real activo (no dry_run), una señal de entrada genera
VARIAS órdenes en TradersPost. Pruébalo primero con dry_run/demo. dry-run por defecto;
requiere --apply; backup JSON + auditoría. Exige --strategy explícito (sin acciones masivas).

Uso:
  python -m scripts.set_scale_execution --strategy MicroGC5mContrarianNormal --on
  python -m scripts.set_scale_execution --strategy MicroGC5mContrarianNormal --on --apply
  python -m scripts.set_scale_execution --strategy MicroGC5mContrarianNormal --off --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.strategy_profile import StrategyProfile
from app.services.audit_service import AuditService


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, help="strategy_id exacto")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--on", action="store_true", help="mode=execute")
    g.add_argument("--off", action="store_true", help="mode=design_only")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    new_mode = "execute" if args.on else "design_only"
    print(f"=== set_scale_execution {args.strategy} → mode={new_mode} "
          f"({'APPLY' if args.apply else 'DRY-RUN'}) ===\n")

    async with AsyncSessionLocal() as db:
        prof = (await db.execute(
            select(StrategyProfile).where(StrategyProfile.strategy_id == args.strategy)
        )).scalar_one_or_none()
        if prof is None:
            print("❌ StrategyProfile no encontrado."); return
        pj = dict(prof.pipeline_config_json or {})
        se = dict(pj.get("scale_entry") or {})
        if not se:
            print("❌ La estrategia no tiene scale_entry definido (no hay diseño que ejecutar).")
            return
        before = {"strategy_id": args.strategy, "scale_entry": dict(se), "version": prof.version}
        print(f"   antes:   mode={se.get('mode')} levels={se.get('levels')} "
              f"qty={se.get('quantities')} max={se.get('max_micro_contracts')}")
        se["mode"] = new_mode
        pj["scale_entry"] = se
        total = sum(int(q or 0) for q in (se.get("quantities") or []))
        maxm = se.get("max_micro_contracts")
        if new_mode == "execute":
            if total <= 0:
                print("⚠️  quantities suman 0 → no se enviarían adds; revisa el diseño.")
            if maxm and total > int(maxm):
                print(f"⚠️  quantities ({total}) > max_micro_contracts ({maxm}) → caería a entrada única.")
            print(f"   ejecución: C1+adds, total {total} microcontratos por señal de entrada.")
        print(f"   después: mode={new_mode}\n")

        if args.apply:
            prof.pipeline_config_json = pj
            prof.version = (prof.version or 1) + 1
            prof.updated_by = "set_scale_execution"
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"scale_exec_backup_{args.strategy}_{ts}.json"
            bp.write_text(json.dumps(before, indent=2, default=str), encoding="utf-8")
            await AuditService().log(
                db, actor="set_scale_execution", action="UPDATE",
                object_type="StrategyProfile", object_id=args.strategy,
                old_value={"scale_entry_mode": before["scale_entry"].get("mode")},
                new_value={"scale_entry_mode": new_mode},
                reason="toggle scaled execution",
            )
            await db.commit()
            print(f"🗄️  Backup → {bp}\n✅ Aplicado.")
        else:
            await db.rollback()
            print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.")


if __name__ == "__main__":
    asyncio.run(main())
