#!/usr/bin/env python3
"""audit_signal_flow — traza, por señal reciente, el resultado del pipeline y el dispatch.
SOLO LECTURA. Responde: ¿las entradas se aprueban con SL? ¿dónde se bloquean? ¿lo que sale
a TradersPost son entradas con stopLoss o solo salidas?

Uso: python -m scripts.audit_signal_flow --limit 40
"""
from __future__ import annotations
import argparse, asyncio
from collections import Counter
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.webhook_delivery import WebhookDelivery

async def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--limit",type=int,default=40); a=ap.parse_args()
    async with AsyncSessionLocal() as db:
        decs=(await db.execute(select(StrategyDecision).order_by(StrategyDecision.decided_at.desc()).limit(a.limit))).scalars().all()
        decs=list(reversed(decs))
        if not decs: print("(sin decisiones)"); return
        print(f"{'hora':16} {'estrategia':26} {'acc':4} {'role':12} {'outcome':16} {'blk':3} {'block_reason':22} {'score':>5} {'sl_price':>10} {'deliv':8} {'SL?':3}")
        print("-"*150)
        oc=Counter(); naked=Counter(); withsl=Counter()
        for d in decs:
            ns=(await db.execute(select(NormalizedSignal).where(NormalizedSignal.id==d.normalized_signal_id))).scalar_one_or_none()
            dels=(await db.execute(select(WebhookDelivery).where(WebhookDelivery.decision_id==d.id))).scalars().all()
            act=(ns.action if ns else "?"); role=(ns.signal_role if ns else "?")
            dl=dels[-1] if dels else None
            payact=(dl.payload_json or {}).get("action") if dl else ""
            hassl="sí" if (dl and (dl.payload_json or {}).get("stopLoss")) else ("no" if dl else "")
            dst=(dl.status if dl else "—")
            sl=f"{float(d.sl_price):.2f}" if d.sl_price is not None else "None"
            oc[d.outcome]+=1
            if dl and dst in ("SENT","DRY_RUN"):
                (withsl if hassl=="sí" else naked)[act]+=1
            t=d.decided_at.strftime("%m-%d %H:%M:%S") if d.decided_at else "?"
            print(f"{t:16} {(d.strategy_id or '')[:26]:26} {act:4} {(role or '')[:12]:12} {d.outcome:16} {str(d.block_level or ''):3} {(d.block_reason or '')[:22]:22} {str(d.score or ''):>5} {sl:>10} {dst:8} {hassl:3}")
        print("\n=== RESUMEN ===")
        print("outcomes:", dict(oc))
        print("ENVIADAS con SL por accion:", dict(withsl))
        print("ENVIADAS SIN SL por accion (naked):", dict(naked))
        print("\nLectura: 'naked' con accion buy/sell = BUG real. 'naked' con accion exit = correcto (salidas no llevan SL).")
if __name__=="__main__": asyncio.run(main())
