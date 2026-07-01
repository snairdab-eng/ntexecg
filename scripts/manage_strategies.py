#!/usr/bin/env python3
"""manage_strategies — lista estrategias con estado/actividad y retira duplicadas.

Sin acción: LISTA todas las estrategias (read-only) con estado, actividad
reciente (decisiones últimos 7d y última decisión) para identificar legacy.

--retire id1,id2 : cambia esas estrategias a status=retired (Nivel 1 las bloquea,
                   dejan de despachar). Reversible. dry-run salvo --apply; backup+audit.

Uso (servidor, venv):
  source .venv/bin/activate
  python -m scripts.manage_strategies
  python -m scripts.manage_strategies --retire 6J5mContrarianAny,ES5m --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.decision import StrategyDecision
from app.models.strategy import Strategy
from app.services.audit_service import AuditService


async def _activity(db, since):
    rows = (await db.execute(
        select(StrategyDecision.strategy_id,
               func.count(StrategyDecision.id),
               func.max(StrategyDecision.created_at))
        .where(StrategyDecision.created_at >= since)
        .group_by(StrategyDecision.strategy_id)
    )).all()
    return {sid: (n, last) for sid, n, last in rows}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retire", help="strategy_ids separados por coma")
    ap.add_argument("--status", default="retired",
                    help="estado destino al retirar (default: retired)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    since7 = datetime.now(timezone.utc) - timedelta(days=7)

    async with AsyncSessionLocal() as db:
        act = await _activity(db, since7)
        strats = (await db.execute(select(Strategy).order_by(Strategy.strategy_id))).scalars().all()

        if not args.retire:
            print(f"=== Estrategias ({len(strats)}) — actividad últimos 7d ===")
            print(f"{'strategy_id':<34}{'status':<12}{'asset':<8}{'dec7d':>6}  última decisión")
            for s in strats:
                n, last = act.get(s.strategy_id, (0, None))
                lasts = last.strftime('%Y-%m-%d %H:%M') if last else '—'
                print(f"{s.strategy_id:<34}{(s.status or ''):<12}"
                      f"{(getattr(s,'asset_symbol','') or ''):<8}{n:>6}  {lasts}")
            print("\nPara retirar: python -m scripts.manage_strategies "
                  "--retire id1,id2 --apply")
            return

        ids = [x.strip() for x in args.retire.split(",") if x.strip()]
        print(f"=== Retirar → status={args.status} "
              f"({'APPLY' if args.apply else 'DRY-RUN'}) ===")
        backup = []
        changed = []
        for sid in ids:
            s = next((x for x in strats if x.strategy_id == sid), None)
            if s is None:
                print(f"  ⚠ {sid}: no existe"); continue
            n, last = act.get(sid, (0, None))
            print(f"  {sid}: {s.status} → {args.status} "
                  f"(dec7d={n}, última={last.strftime('%Y-%m-%d %H:%M') if last else '—'})")
            if s.status == args.status:
                print("     (ya está en ese estado)"); continue
            backup.append({"strategy_id": sid, "status": s.status})
            if args.apply:
                s.status = args.status
                await AuditService().log(
                    db, actor="manage_strategies", action="UPDATE",
                    object_type="Strategy", object_id=sid,
                    old_value={"status": backup[-1]["status"]},
                    new_value={"status": args.status}, reason="retire duplicate/legacy",
                )
            changed.append(sid)

        if args.apply and backup:
            Path("REPORTES").mkdir(exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            bp = Path("REPORTES") / f"retire_backup_{ts}.json"
            bp.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
            await db.commit()
            print(f"\n🗄️  Backup → {bp}\n✅ Retiradas: {changed}")
        else:
            await db.rollback()
            print(f"\nℹ️  DRY-RUN: sin cambios. Usa --apply. Cambiarían: {changed}")


if __name__ == "__main__":
    asyncio.run(main())
