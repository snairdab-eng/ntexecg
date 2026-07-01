#!/usr/bin/env python3
"""check_leg_touch — ¿el precio TOCÓ cada pierna límite, y cuánto tardó?

Para las últimas N entradas APPROVE de una estrategia, imprime los precios límite
de las piernas base (lo que NTEXECG envió) y, con las barras 5m del bridge
(OhlcvBar), calcula por cada pierna:
  - si el precio la TOCÓ después de la señal,
  - la hora del PRIMER toque,
  - cuánto tiempo pasó desde la señal (o sea, cuánto habría tenido que seguir
    viva la orden para poder llenarse).
El fill/cancel definitivo vive en TradersPost.

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


def fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 0:
        s = 0
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"


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
            bars: list = []
            used = None
            for cand in [data_sym, sig.mapped_symbol, sig.ticker_received]:
                if not cand:
                    continue
                rows = (await db.execute(
                    select(OhlcvBar.bar_time, OhlcvBar.high, OhlcvBar.low)
                    .where(OhlcvBar.symbol == cand, OhlcvBar.bar_time >= ts)
                    .order_by(OhlcvBar.bar_time)
                )).all()
                rows = [(b[0], float(b[1]), float(b[2]))
                        for b in rows if b[1] is not None and b[2] is not None]
                if rows:
                    bars, used = rows, cand
                    break

            if not bars:
                print("  ⚠ Sin barras en el bridge para este símbolo/rango — "
                      "confirma el fill directo en TradersPost.")
            else:
                lo = min(b[2] for b in bars)
                hi = max(b[1] for b in bars)
                print(f"  Barras {used}: {len(bars)} desde la señal | "
                      f"mínimo={lo} máximo={hi} (última {bars[-1][0]} UTC)")

            for d in base:
                p = d.payload_json or {}
                lp = p.get("limitPrice")
                side = p.get("action")
                q = p.get("quantity")
                if lp is None:
                    print(f"   • C-market {side} qty{q}: a mercado (fill inmediato) [{d.status}]")
                    continue
                lp = float(lp)
                if not bars:
                    print(f"   • Límite {side} qty{q} @ {lp}: ¿? (sin barras) [{d.status}]")
                    continue
                # primer toque
                first = None
                for bt, bh, bl in bars:
                    hit = (bl <= lp) if side == "buy" else (bh >= lp)
                    if hit:
                        first = bt
                        break
                if first is None:
                    ext = (min(b[2] for b in bars) if side == "buy"
                           else max(b[1] for b in bars))
                    cmp = ">" if side == "buy" else "<"
                    print(f"   • Límite {side} qty{q} @ {lp}: NO tocó ❌ "
                          f"(extremo {ext} {cmp} {lp}) [{d.status}]")
                else:
                    elapsed = fmt_elapsed((first - ts).total_seconds())
                    print(f"   • Límite {side} qty{q} @ {lp}: TOCÓ ✅ "
                          f"primer toque {first} UTC (~{elapsed} después de la señal) "
                          f"[{d.status}]")


if __name__ == "__main__":
    asyncio.run(main())
