#!/usr/bin/env python3
"""set_scale_execution — activa/desactiva la EJECUCIÓN escalonada de estrategias.

Cambia pipeline_config_json["scale_entry"]["mode"] entre "execute" (envía C1+adds a
TradersPost) y "design_only" (solo diseño, una entrada). NO toca nada más.

⚠️ Con mode=execute y dispatch real (no dry_run), una señal de entrada genera VARIAS
órdenes en TradersPost (cuenta demo). dry-run por defecto; requiere --apply; backup
JSON + auditoría. Objetivo: una estrategia (--strategy) o todas (--all).

Uso:
  python -m scripts.set_scale_execution --strategy MicroGC5mContrarianNormal --on
  python -m scripts.set_scale_execution --all --on
  python -m scripts.set_scale_execution --all --on --apply
  python -m scripts.set_scale_execution --all --off --apply
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


async def main() -> None:
    ap = argparse.ArgumentParser()
    tgt = ap.add_mutually_exclusive_group(required=True)
    tgt.add_argument("--strategy", help="strategy_id exacto")
    tgt.add_argument("--all", action="store_true", help="todas las no retiradas con scale_entry")
    md = ap.add_mutually_exclusive_group(required=True)
    md.add_argument("--on", action="store_true", help="mode=execute")
    md.add_argument("--off", action="store_true", help="mode=design_only")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    new_mode = "execute" if args.on else "design_only"
    print(f"=== set_scale_execution → mode={new_mode} "
          f"({'APPLY' if args.apply else 'DRY-RUN'}) ===\n")

    async with AsyncSessionLocal() as db:
        # Resolver objetivos
        if args.all:
            strats = (await db.execute(select(Strategy))).scalars().all()
            sids = [s.strategy_id for s in strats if s.status not in HIDDEN]
        else:
            sids = [args.strategy]

        changed, skipped, backup = [], [], []
        for sid in sids:
            prof = (await db.execute(
                select(StrategyProfile).where(StrategyProfile.strategy_id == sid)
            )).scalar_one_or_none()
            if prof is None:
                skipped.append((sid, "sin StrategyProfile")); continue
            pj = dict(prof.pipeline_config_json or {})
            se = dict(pj.get("scale_entry") or {})
            if not se:
                skipped.append((sid, "sin scale_entry")); continue
            cur = se.get("mode")
            total = sum(int(q or 0) for q in (se.get("quantities") or []))
            maxm = se.get("max_micro_contracts")
            warn = ""
            if new_mode == "execute":
                if total <= 0:
                    warn = " ⚠ quantities suman 0 (no enviaría adds)"
                elif maxm and total > int(maxm):
                    warn = f" ⚠ total {total}>max {maxm} (caería a entrada única)"
            base_market = (se.get("quantities") or [0])[0] if se.get("quantities") else 0
            kind = "C1 a mercado + adds" if base_market else "solo límite en pullback"
            print(f"── {sid}: mode {cur} → {new_mode} | {se.get('quantities')} "
                  f"levels={se.get('levels')} total={total} ({kind}){warn}")
            if cur == new_mode:
                skipped.append((sid, f"ya {new_mode}")); 
                continue
            backup.append({"strategy_id": sid, "scale_entry": dict(se), "version": prof.version})
            if args.apply:
                se["mode"] = new_mode
                pj["scale_entry"] = se
                prof.pipeline_config_json = pj
                prof.version = (prof.version or 1) + 1
                prof.updated_by = "set_scale_execution"
                await AuditService().log(
                    db, actor="set_scale_execution", action="UPDATE",
                    object_type="StrategyProfile", object_id=sid,
                    old_value={"scale_entry_mode": cur},
                    new_value={"scale_entry_mode": new_mode},
                    reason="toggle scaled execution",
                )
            changed.append(sid)

        if args.apply and backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"scale_exec_backup_{ts}.json"
            bp.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
            print(f"\n🗄️  Backup → {bp}")
        if args.apply:
            await db.commit(); print("✅ Aplicado.")
        else:
            await db.rollback(); print("\nℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.")
        print(f"\nCambiadas ({len(changed)}): {changed}")
        print(f"Saltadas ({len(skipped)}): {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
