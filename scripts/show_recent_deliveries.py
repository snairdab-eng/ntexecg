#!/usr/bin/env python3
"""show_recent_deliveries — visor SOLO LECTURA de los últimos envíos a TradersPost.

Agrupa por decisión (una señal puede generar varios legs en entradas escalonadas) y
muestra action, tipo de orden (market/limit), limitPrice, quantity, stop y leg.

Uso:
  python -m scripts.show_recent_deliveries                 # últimos 20
  python -m scripts.show_recent_deliveries --limit 40
  python -m scripts.show_recent_deliveries --strategy ES5m
  python -m scripts.show_recent_deliveries --entries       # solo entradas (buy/sell)
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.webhook_delivery import WebhookDelivery


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--entries", action="store_true", help="solo buy/sell")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        stmt = select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(args.limit)
        if args.strategy:
            stmt = select(WebhookDelivery).where(
                WebhookDelivery.strategy_id == args.strategy
            ).order_by(WebhookDelivery.created_at.desc()).limit(args.limit)
        rows = (await db.execute(stmt)).scalars().all()
        rows = list(reversed(rows))  # cronológico

        by_dec = defaultdict(list)
        for r in rows:
            by_dec[str(r.decision_id)].append(r)

        if not rows:
            print("(sin envíos registrados)"); return

        print(f"=== Últimos {len(rows)} envíos (agrupados por señal/decisión) ===\n")
        for dec_id, legs in by_dec.items():
            p0 = legs[0].payload_json or {}
            action = p0.get("action")
            strat = legs[0].strategy_id
            when = legs[0].created_at
            is_entry = action in ("buy", "sell")
            tag = "ENTRADA" if is_entry else "SALIDA"
            if args.entries and not is_entry:
                continue
            print(f"── {when:%Y-%m-%d %H:%M} {strat} [{tag} {action}] "
                  f"legs={len(legs)} decision={dec_id[:8]}")
            for r in legs:
                p = r.payload_json or {}
                ot = p.get("orderType", "market")
                lp = p.get("limitPrice")
                sl = (p.get("stopLoss") or {}).get("stopPrice")
                ex = p.get("extras") or {}
                price_str = f"limit@{lp}" if ot == "limit" else "market"
                print(f"     · {r.status:7} qty={p.get('quantity')} {price_str} "
                      f"stop={sl} leg={ex.get('leg_index')} levelATR={ex.get('level_atr')}")
            print()

        # Resumen de legs por entrada
        entradas = [(d, l) for d, l in by_dec.items()
                    if (l[0].payload_json or {}).get("action") in ("buy", "sell")]
        if entradas:
            print("── Resumen entradas: legs por señal ──")
            for d, l in entradas:
                p0 = l[0].payload_json or {}
                print(f"   {l[0].strategy_id:30s} legs={len(l)} "
                      f"qty_total={sum(int((x.payload_json or {}).get('quantity') or 0) for x in l)}")


if __name__ == "__main__":
    asyncio.run(main())
