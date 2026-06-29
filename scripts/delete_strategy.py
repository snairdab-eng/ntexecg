#!/usr/bin/env python3
"""delete_strategy — BORRA estrategias de la DB (StrategyProfile + Strategy).
El histórico (decisiones/señales/entregas, sin FK a strategies) queda como auditoría.
dry-run por defecto; --apply; backup JSON + auditoría. Omite las que no existan.

Uso:
  python -m scripts.delete_strategy --ids A,B,C
  python -m scripts.delete_strategy --ids A,B,C --apply
"""
from __future__ import annotations
import argparse, asyncio, json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.audit_service import AuditService

async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--ids",required=True); ap.add_argument("--apply",action="store_true")
    a=ap.parse_args()
    ids=[x.strip() for x in a.ids.split(",") if x.strip()]
    print(f"=== delete_strategy ({'APPLY' if a.apply else 'DRY-RUN'}) ===")
    async with AsyncSessionLocal() as db:
        done=[];skip=[];backup=[]
        for sid in ids:
            s=(await db.execute(select(Strategy).where(Strategy.strategy_id==sid))).scalar_one_or_none()
            if s is None: skip.append(sid); continue
            p=(await db.execute(select(StrategyProfile).where(StrategyProfile.strategy_id==sid))).scalar_one_or_none()
            backup.append({"strategy_id":sid,"name":s.name,"status":s.status,"asset":s.asset_symbol,
                           "profile":bool(p),"pipeline_config_json":(p.pipeline_config_json if p else None)})
            print(f"  borrar {sid}  (status={s.status}, perfil={'sí' if p else 'no'})")
            if a.apply:
                if p:
                    await db.delete(p); await db.flush()
                await db.delete(s); await db.flush()
                await AuditService().log(db,actor="delete_strategy",action="DELETE",object_type="Strategy",
                    object_id=sid,old_value={"status":s.status},new_value={},reason="cleanup/discard")
            done.append(sid)
        if a.apply and backup:
            ts=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"); Path("REPORTES").mkdir(exist_ok=True)
            bp=Path("REPORTES")/f"delete_strategies_{ts}.json"; bp.write_text(json.dumps(backup,indent=2,default=str),encoding="utf-8")
            await db.commit(); print(f"  🗄️  {bp}\n  ✅ Borradas: {done}")
        elif a.apply:
            print("  (nada que borrar)")
        else:
            await db.rollback(); print(f"  ℹ️  DRY-RUN. Borraría: {done} · omite: {skip}")
        if skip: print(f"  ⏭  no existen: {skip}")
if __name__=="__main__": asyncio.run(main())
