#!/usr/bin/env python3
"""reset_position_state — pone a FLAT el estado de posición ESTIMADO de NTEXECG.

Úsalo tras aplanar manualmente en el bróker, para que NTEXECG no crea que hay
posiciones abiertas (evita forced-exits fantasma y bloqueos por reversa).
NO envía órdenes; solo corrige el estado interno. dry-run + backup + auditoría.

Uso:
  python -m scripts.reset_position_state                 # dry-run, todas
  python -m scripts.reset_position_state --apply         # aplica
  python -m scripts.reset_position_state --symbol MES --apply
"""
from __future__ import annotations
import argparse, asyncio, json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.position_state import PositionState
from app.services.audit_service import AuditService

async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--symbol",default=None); ap.add_argument("--account",default=None)
    ap.add_argument("--apply",action="store_true"); a=ap.parse_args()
    print(f"=== reset_position_state ({'APPLY' if a.apply else 'DRY-RUN'}) ===")
    async with AsyncSessionLocal() as db:
        q=select(PositionState)
        if a.symbol: q=q.where(PositionState.symbol==a.symbol)
        if a.account: q=q.where(PositionState.account_id==a.account)
        rows=(await db.execute(q)).scalars().all()
        rows=[r for r in rows if r.state!="FLAT" or (r.quantity or 0)!=0]
        if not rows: print("  Nada que resetear (todo FLAT)."); return
        backup=[]
        for r in rows:
            print(f"  {r.account_id}:{r.symbol}  state={r.state} dir={r.direction} qty={r.quantity} → FLAT/0")
            backup.append({"account_id":r.account_id,"symbol":r.symbol,"state":r.state,
                           "direction":r.direction,"quantity":r.quantity})
            if a.apply:
                r.state="FLAT"; r.direction=None; r.quantity=0; r.state_source="estimated"
        if a.apply:
            ts=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"); Path("REPORTES").mkdir(exist_ok=True)
            bp=Path("REPORTES")/f"position_reset_{ts}.json"; bp.write_text(json.dumps(backup,indent=2,default=str),encoding="utf-8")
            await AuditService().log(db,actor="reset_position_state",action="UPDATE",object_type="PositionState",
                object_id="*",old_value={"n":len(backup)},new_value={"state":"FLAT"},reason="reset estado estimado tras aplanar")
            await db.commit(); print(f"  🗄️  {bp}\n  ✅ {len(backup)} posiciones a FLAT.")
        else:
            await db.rollback(); print(f"\n  ℹ️  DRY-RUN: resetearía {len(rows)}. Usa --apply.")
if __name__=="__main__": asyncio.run(main())
