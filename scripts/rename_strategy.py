#!/usr/bin/env python3
"""rename_strategy — cambia el strategy_id recreando la estrategia con el id nuevo
y RETIRANDO la vieja (no renombra en sitio para no romper FKs en producción).

Copia íntegra la calibración (Strategy + StrategyProfile), conserva el webhook de
TradersPost (saliente). El webhook de ENTRADA (LuxAlgo→NTEXECG) cambia de path
(/webhooks/luxalgo/<nuevo_id>) → reconfigurar la alerta en TradingView.

Opcional: --score N añade QualityScorer a la copia nueva.
dry-run por defecto; --apply; backup JSON + auditoría.

Uso:
  python -m scripts.rename_strategy --old ES5m --new ES5m_ConfNormal_TC_TSR --score 55
  python -m scripts.rename_strategy --old ES5m --new ES5m_ConfNormal_TC_TSR --score 55 --apply
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

FILTERS = {"volume_relative":{"enabled":True,"weight":25},"atr_normalized":{"enabled":True,"weight":25},
           "vwap_position":{"enabled":True,"weight":25},"time_of_day":{"enabled":True,"weight":25}}

def clone(obj, model, overrides):
    data={}
    for c in model.__table__.columns:
        if c.name in ("id","created_at","updated_at"): continue
        data[c.name]=getattr(obj,c.name)
    data.update(overrides); return model(**data)

async def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--old",required=True); ap.add_argument("--new",required=True)
    ap.add_argument("--score",type=int,default=0,help="añadir QualityScorer score_minimum (0=no)")
    ap.add_argument("--apply",action="store_true")
    a=ap.parse_args()
    print(f"=== rename {a.old} → {a.new}  ({'APPLY' if a.apply else 'DRY-RUN'}) ===\n")
    async with AsyncSessionLocal() as db:
        old=(await db.execute(select(Strategy).where(Strategy.strategy_id==a.old))).scalar_one_or_none()
        if old is None: print("❌ estrategia vieja no encontrada"); return
        if (await db.execute(select(Strategy).where(Strategy.strategy_id==a.new))).scalar_one_or_none():
            print("❌ el nuevo strategy_id ya existe"); return
        oprof=(await db.execute(select(StrategyProfile).where(StrategyProfile.strategy_id==a.old))).scalar_one_or_none()
        backup={"strategy":{c.name:str(getattr(old,c.name)) for c in Strategy.__table__.columns},
                "profile":({c.name:str(getattr(oprof,c.name)) for c in StrategyProfile.__table__.columns} if oprof else None)}
        pj=dict((oprof.pipeline_config_json or {})) if oprof else {}
        if a.score>0: pj["filters"]=FILTERS; pj["score_minimum"]=a.score
        print(f"  Vieja {a.old}: status {old.status} → retired (enabled→False)")
        print(f"  Nueva {a.new}: copia activo={old.asset_symbol} tf={old.timeframe} status={old.status}")
        print(f"     SL={getattr(oprof,'sl_atr_multiplier',None)} atr_tf={getattr(oprof,'atr_timeframe',None)} "
              f"score_minimum={pj.get('score_minimum')} filters={'sí' if pj.get('filters') else 'no'} "
              f"scale={'sí' if pj.get('scale_entry') else 'no'} webhook_tp={'copiado' if oprof and oprof.traderspost_webhook_url else 'n/a'}")
        print(f"  ⚠ Reconfigurar alerta TradingView → /webhooks/luxalgo/{a.new}\n")
        if a.apply:
            tok=old.webhook_token
            old.status="retired"; old.enabled=False; old.webhook_token=None
            await db.flush()
            ns=clone(old,Strategy,{"strategy_id":a.new,"status":(backup['strategy']['status']),
                                   "enabled":True,"webhook_token":tok})
            db.add(ns); await db.flush()
            if oprof:
                npf=clone(oprof,StrategyProfile,{"strategy_id":a.new})
                npf.pipeline_config_json=pj
                db.add(npf)
            ts=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp=Path("REPORTES")/f"rename_{a.old}_to_{a.new}_{ts}.json"
            bp.write_text(json.dumps(backup,indent=2,default=str),encoding="utf-8")
            await AuditService().log(db,actor="rename_strategy",action="UPDATE",object_type="Strategy",
                object_id=a.new,old_value={"renamed_from":a.old},new_value={"score_minimum":pj.get("score_minimum")},
                reason="rename strategy_id (recreate+retire)")
            await db.commit()
            print(f"🗄️  Backup → {bp}\n✅ Hecho. Vieja retirada, nueva creada.")
        else:
            await db.rollback(); print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.")

if __name__=="__main__":
    asyncio.run(main())
