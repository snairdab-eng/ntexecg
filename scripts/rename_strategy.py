#!/usr/bin/env python3
"""rename_strategy — cambia strategy_id recreando con el id nuevo y eliminando/retirando
la vieja. NO renombra en sitio (evita romper la FK strategy_profiles→strategies).

Copia íntegra la calibración (Strategy + StrategyProfile) y el webhook TradersPost.
El webhook de ENTRADA (LuxAlgo→NTEXECG) cambia de path → reconfigurar la alerta.

  --delete-old  borra la vieja (DB) en vez de retirarla (default: retira)
  --score N     añade QualityScorer score_minimum a la copia (0=no)
dry-run por defecto; --apply; backup JSON + auditoría. Idempotente (omite si falta old / existe new).
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

FILTERS={"volume_relative":{"enabled":True,"weight":25},"atr_normalized":{"enabled":True,"weight":25},
         "vwap_position":{"enabled":True,"weight":25},"time_of_day":{"enabled":True,"weight":25}}
def coldict(obj,model):
    return {c.name:getattr(obj,c.name) for c in model.__table__.columns if c.name not in ("id","created_at","updated_at")}

async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--old",required=True);ap.add_argument("--new",required=True)
    ap.add_argument("--score",type=int,default=0);ap.add_argument("--delete-old",action="store_true")
    ap.add_argument("--apply",action="store_true")
    a=ap.parse_args()
    mode="DELETE-OLD" if a.delete_old else "RETIRE-OLD"
    print(f"=== rename {a.old} → {a.new} [{mode}] ({'APPLY' if a.apply else 'DRY-RUN'}) ===")
    async with AsyncSessionLocal() as db:
        old=(await db.execute(select(Strategy).where(Strategy.strategy_id==a.old))).scalar_one_or_none()
        if old is None: print(f"  ⏭  '{a.old}' no existe, omito.\n"); return
        if (await db.execute(select(Strategy).where(Strategy.strategy_id==a.new))).scalar_one_or_none():
            print(f"  ⏭  '{a.new}' ya existe, omito.\n"); return
        oprof=(await db.execute(select(StrategyProfile).where(StrategyProfile.strategy_id==a.old))).scalar_one_or_none()
        sdata=coldict(old,Strategy); pdata=coldict(oprof,StrategyProfile) if oprof else None
        tok=old.webhook_token
        pj=dict(oprof.pipeline_config_json or {}) if oprof else {}
        if a.score>0: pj["filters"]=FILTERS; pj["score_minimum"]=a.score
        backup={"strategy":{k:str(v) for k,v in sdata.items()},"profile":({k:str(v) for k,v in pdata.items()} if pdata else None)}
        print(f"  activo={old.asset_symbol} tf={old.timeframe} SL={getattr(oprof,'sl_atr_multiplier',None)} "
              f"score_minimum={pj.get('score_minimum')} filters={'sí' if pj.get('filters') else 'no'} "
              f"scale={'sí' if pj.get('scale_entry') else 'no'} webhook_tp={'copiado' if oprof and oprof.traderspost_webhook_url else 'n/a'}")
        print(f"  vieja → {'BORRADA' if a.delete_old else 'retirada'} · ⚠ reapuntar alerta a /webhooks/luxalgo/{a.new}")
        if a.apply:
            if a.delete_old:
                if oprof: await db.delete(oprof)
                await db.delete(old); await db.flush()
            else:
                old.status="retired"; old.enabled=False; old.webhook_token=None; await db.flush()
            ns=Strategy(**{**sdata,"strategy_id":a.new,"enabled":True,"webhook_token":tok}); db.add(ns); await db.flush()
            if pdata is not None:
                npf=StrategyProfile(**{**pdata,"strategy_id":a.new}); npf.pipeline_config_json=pj; db.add(npf)
            ts=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"); Path("REPORTES").mkdir(exist_ok=True)
            bp=Path("REPORTES")/f"rename_{a.old}_to_{a.new}_{ts}.json"; bp.write_text(json.dumps(backup,indent=2,default=str),encoding="utf-8")
            await AuditService().log(db,actor="rename_strategy",action="UPDATE",object_type="Strategy",object_id=a.new,
                old_value={"renamed_from":a.old,"old_deleted":a.delete_old},new_value={"score_minimum":pj.get("score_minimum")},reason="normalize strategy_id")
            await db.commit(); print(f"  🗄️  {bp}\n  ✅ Hecho.\n")
        else:
            await db.rollback(); print("  ℹ️  DRY-RUN.\n")
if __name__=="__main__": asyncio.run(main())
