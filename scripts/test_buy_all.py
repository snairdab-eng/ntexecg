#!/usr/bin/env python3
"""test_buy_all — dispara una COMPRA de prueba a cada estrategia activa (vía el webhook
real de NTEXECG en localhost), espera la decisión y muestra outcome + SL + legs enviados.

⚠️ Esto envía órdenes REALES a la cuenta demo de TradersPost (es el objetivo: probar el
camino completo). Correr en el SERVIDOR (usa localhost:8000 + la DB). Después, aplanar.

Uso: python -m scripts.test_buy_all
     python -m scripts.test_buy_all --only NQ5m_ConfAny_ST_TC   (una sola)
"""
from __future__ import annotations
import argparse, asyncio, time
from datetime import datetime, timezone
import httpx
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.webhook_delivery import WebhookDelivery

BASE="http://localhost:8000"
PRICES={"MES":7533.0,"MNQ":30000.0,"MGC":4065.0,"M2K":3036.0,"M6E":1.146,"MJY":0.0062,"MYM":44000.0,"MCL":70.0}

async def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--only",default=None); args=ap.parse_args()
    async with AsyncSessionLocal() as db:
        q=select(Strategy).where(Strategy.status!="retired")
        if args.only: q=select(Strategy).where(Strategy.strategy_id==args.only)
        strats=(await db.execute(q.order_by(Strategy.asset_symbol))).scalars().all()
    if not strats: print("sin estrategias"); return
    print(f"Disparando COMPRA de prueba a {len(strats)} estrategia(s)…\n")
    for s in strats:
        tok=s.webhook_token or "dev_global_token"
        px=PRICES.get(s.asset_symbol,100.0); iv=(s.timeframe or "5m").replace("m","")
        payload={"ticker":s.asset_symbol,"action":"buy","sentiment":"long","quantity":"1",
                 "price":f"{px}","time":datetime.now(timezone.utc).isoformat(),"interval":iv}
        url=f"{BASE}/webhooks/luxalgo/{s.strategy_id}?token={tok}"
        print(f"── {s.strategy_id}  (ticker={s.asset_symbol} px={px} tf={iv}m)")
        try:
            r=httpx.post(url,json=payload,timeout=10.0)
        except Exception as e:
            print(f"   ❌ POST falló: {e}\n"); continue
        if r.status_code!=200:
            print(f"   ❌ HTTP {r.status_code}: {r.text[:120]}\n"); continue
        sid=r.json().get("signal_id")
        # poll decision + deliveries
        dec=None; dels=[]; dl=time.monotonic()+8
        while time.monotonic()<dl:
            async with AsyncSessionLocal() as db:
                row=(await db.execute(select(StrategyDecision).join(NormalizedSignal,StrategyDecision.normalized_signal_id==NormalizedSignal.id).where(NormalizedSignal.raw_signal_id==sid).order_by(StrategyDecision.created_at.desc()).limit(1))).scalar_one_or_none()
                if row is not None:
                    dec=row
                    dels=(await db.execute(select(WebhookDelivery).where(WebhookDelivery.decision_id==row.id))).scalars().all()
                    break
            await asyncio.sleep(0.4)
        if dec is None:
            print("   ⏳ sin decisión (timeout)\n"); continue
        if dec.outcome=="APPROVE":
            print(f"   ✅ APPROVE · score={dec.score} · SL={float(dec.sl_price) if dec.sl_price is not None else None} · legs={len(dels)}")
            for d in dels:
                p=d.payload_json or {}
                print(f"        leg: {d.status} {p.get('action')} qty={p.get('quantity')} "
                      f"{('limit@'+str(p.get('limitPrice'))) if p.get('orderType')=='limit' else 'market'} "
                      f"stopLoss={'sí' if p.get('stopLoss') else 'NO'}")
        else:
            print(f"   ❌ {dec.outcome} · {dec.block_reason} (Nivel {dec.block_level})")
        print()
    print("Listo. Revisa la cuenta 'NTEXECG' en TradersPost. Para limpiar: aplana en NinjaTrader.")
if __name__=="__main__": asyncio.run(main())
