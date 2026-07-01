#!/usr/bin/env python3
"""check_leg_touch — ¿el precio TOCÓ cada pierna límite de una entrada escalonada?

Para las últimas N entradas APPROVE de una estrategia, imprime los precios límite
de las piernas base (lo que NTEXECG envió) y compara contra el mínimo/máximo que
hizo el precio DESPUÉS de la señal (barras 5m del bridge, OhlcvBar) para inferir
si cada pierna se pudo llenar. El fill/cancel definitivo vive en TradersPost.

Uso (en el servidor, con venv):
  source .venv/bin/activate
  python -m scripts.check_leg_touch --strategy NQ5m_ConfAny_ST_TC
  python -m scripts.check_leg_touch --strategy ES5m_ConfNormal_TC_TSR --n 8
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.ohlcv_bar import OhlcvBar
from app.models.webhook_delivery import WebhookDelivery
from app.services.symbol_mapper import SymbolMapper


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--n", type=int, default=5, help="últimas N entradas a revisar")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        mapper = SymbolMapper()
        decs = (await db.execute(
            select(StrategyDecision, NormalizedSignal)
            .join(NormalizedSignal,
                  StrategyDecision.normalized_signal_id == NormalizedSignal.id)
            .where(StrategyDecision.strategy_id == args.strategy,
                   StrategyDecision.outcome == "APPROVE")
            .order_by(StrategyDecision.created_at.desc()).limit(args.n)
        )).all()
        if not decs:
            print("Sin entradas APPROVE para", args.strategy)
            return

        for dec, sig in decs:
            if sig.action == "exit":
                continue
            ts = sig.signal_ts or dec.created_at
            dels = (await db.execute(
                select(WebhookDelivery)
                .where(WebhookDelivery.decision_id == dec.id)
                .order_by(WebhookDelivery.created_at)
            )).scalars().all()
            base = [d for d in dels if (d.destination or "") == "traderspost"]
            print("\n==============================================")
            print(f"Señal {str(dec.id)[:8]} | {sig.ticker_received}->{sig.mapped_symbol} "
                  f"{sig.action} @ {sig.price} | {ts} UTC | piernas base={len(base)}")

            data_sym = await mapper.resolve_market_data_symbol(db, sig.ticker_received)
            lo = hi = None
            used = None
            n_bars = 0
            last = None
            for cand in [data_sym, sig.mapped_symbol, sig.ticker_received]:
                if not cand:
                    continue
                bars = (await db.execute(
                    select(OhlcvBar.bar_time, OhlcvBar.high, OhlcvBar.low)
                    .where(OhlcvBar.symbol == cand, OhlcvBar.bar_time >= ts)
                    .order_by(OhlcvBar.bar_time)
                )).all()
                if bars:
                    lows = [float(b[2]) for b in bars if b[2] is not None]
                    highs = [float(b[1]) for b in bars if b[1] is not None]
                    if lows and highs:
                        lo, hi, used = min(lows), max(highs), cand
                        n_bars, last = len(bars), bars[-1][0]
                        break
            if lo is None:
                print("  ⚠ Sin barras en el bridge para este símbolo/rango — "
                      "confirma el fill directo en TradersPost.")
            else:
                print(f"  Barras {used}: {n_bars} desde la señal | "
                      f"mínimo={lo} máximo={hi} (última {last} UTC)")

            for d in base:
                p = d.payload_json or {}
                lp = p.get("limitPrice")
                side = p.get("action")
                q = p.get("quantity")
                if lp is None:
                    print(f"   • C-market {side} qty{q}: a mercado (fill inmediato) [{d.status}]")
                    continue
                lp = float(lp)
                if lo is None:
                    verdict = "¿? (sin barras)"
                elif side == "buy":
                    verdict = (f"TOCÓ ✅ (min {lo} <= {lp})" if lo <= lp
                               else f"NO tocó ❌ (min {lo} > {lp})")
                else:
                    verdict = (f"TOCÓ ✅ (max {hi} >= {lp})" if hi >= lp
                               else f"NO tocó ❌ (max {hi} < {lp})")
                print(f"   • Límite {side} qty{q} @ {lp}: {verdict} [{d.status}]")


if __name__ == "__main__":
    asyncio.run(main())
