#!/usr/bin/env python3
"""create_new_strategies_v1 — alta de las 2 estrategias nuevas (S5 ES, S4 6J) en NTEXECG.

Crea Strategy + StrategyProfile con su calibración + webhook TradersPost (demo).
Genera webhook_token por estrategia (se imprime → úsalo en la alerta de TradingView).
dry-run por defecto; --apply; auditoría. Omite si el strategy_id ya existe.
"""
from __future__ import annotations
import argparse, asyncio, secrets
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.audit_service import AuditService

DOMAIN="https://ntexecg.lipatolicucho.com"
H24={"days":[0,1,2,3,4,5],"start":"18:00","end":"17:00","next_day_end":True}
GUARD={"enforce_symbol_match":True,"enforce_timeframe_match":True}
FILTERS={"volume_relative":{"enabled":True,"weight":25},"atr_normalized":{"enabled":True,"weight":25},
         "vwap_position":{"enabled":True,"weight":25},"time_of_day":{"enabled":True,"weight":25}}

NEW=[
 dict(strategy_id="ES5m_ConfStrong_TSR_WeakConf",
      name="MicroES5m - Confirmation Strong - Trend Strength Ranging - Weak Confluence",
      asset_symbol="MES", timeframe="5m", sl=8.0, atr_tf="5m",
      tp_url="https://webhooks.traderspost.io/trading/webhook/63a96cf8-0170-49cd-a536-e43e162d342b/ffb64bc61a96346a2889375bebcd9110",
      pj={"windows":[H24],"filters":FILTERS,"score_minimum":60,"guardrails":GUARD}),
 dict(strategy_id="6J5m_ConfNormal_TSR_MF50",
      name="Micro6J5m - Confirmation Normal - Trend Strength Ranging - Money Flow 50",
      asset_symbol="MJY", timeframe="5m", sl=2.5, atr_tf="5m",
      tp_url="https://webhooks.traderspost.io/trading/webhook/b80831ac-eec0-4327-8972-71ada9b4f190/c9054b987709992bb1e1ede1daebcc41",
      pj={"windows":[H24],"guardrails":GUARD}),
]

async def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--apply",action="store_true"); a=ap.parse_args()
    print(f"=== create_new_strategies_v1 ({'APPLY' if a.apply else 'DRY-RUN'}) ===\n")
    async with AsyncSessionLocal() as db:
        for n in NEW:
            ex=(await db.execute(select(Strategy).where(Strategy.strategy_id==n["strategy_id"]))).scalar_one_or_none()
            if ex: print(f"⏭  {n['strategy_id']} ya existe, omito.\n"); continue
            tok=secrets.token_hex(16)
            qs="score_minimum" in n["pj"]
            print(f"── {n['strategy_id']}")
            print(f"   activo={n['asset_symbol']} tf={n['timeframe']} SL={n['sl']}× ventana=24h "
                  f"QualityScorer={'score≥'+str(n['pj'].get('score_minimum')) if qs else 'OFF'} "
                  f"traderspost=demo dry_run=False")
            print(f"   ALERTA TradingView → {DOMAIN}/webhooks/luxalgo/{n['strategy_id']}?token={tok}")
            if a.apply:
                db.add(Strategy(strategy_id=n["strategy_id"],name=n["name"],asset_symbol=n["asset_symbol"],
                                timeframe=n["timeframe"],status="paper",enabled=True,webhook_token=tok,
                                traderspost_webhook_url=n["tp_url"]))
                await db.flush()
                db.add(StrategyProfile(strategy_id=n["strategy_id"],sl_atr_multiplier=n["sl"],tp_atr_multiplier=None,
                                       atr_timeframe=n["atr_tf"],traderspost_webhook_url=n["tp_url"],
                                       traderspost_enabled=True,dry_run=False,mode="paper",pipeline_config_json=n["pj"]))
                await AuditService().log(db,actor="create_new_strategies_v1",action="CREATE",object_type="Strategy",
                    object_id=n["strategy_id"],old_value={},new_value={"sl":n["sl"],"score_minimum":n["pj"].get("score_minimum")},
                    reason="alta estrategia nueva (Anexo 23)")
            print()
        if a.apply:
            await db.commit(); print("✅ Altas escritas. Guarda los token de arriba para las alertas.")
        else:
            await db.rollback(); print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.")
if __name__=="__main__": asyncio.run(main())
