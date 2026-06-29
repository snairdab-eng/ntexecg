#!/usr/bin/env python3
"""preview_scaled — muestra los legs que NTEXECG enviaría para una ENTRADA, usando la
config REAL de la DB (ConfigResolver) + PayloadBuilder.build_scaled. SOLO LECTURA, sin HTTP.

Sirve para confirmar que el escalonado se aplica (mode=execute pasa por el resolver) sin
esperar una señal en vivo. Precios/ATR son ilustrativos (--price/--atr).

Uso:
  python -m scripts.preview_scaled --strategy ES5m --price 5000 --atr 5
  python -m scripts.preview_scaled --strategy 6J5mContrarianAny --price 0.006218 --atr 0.0000007 --side sell
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.services.config_resolver import ConfigResolver
from app.services.payload_builder import PayloadBuilder


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--price", type=float, default=100.0)
    ap.add_argument("--atr", type=float, default=1.0)
    ap.add_argument("--side", choices=["buy", "sell"], default="buy")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        strat = (await db.execute(
            select(Strategy).where(Strategy.strategy_id == args.strategy)
        )).scalar_one_or_none()
        if strat is None:
            print("❌ estrategia no encontrada"); return
        cfg = await ConfigResolver().resolve(db, args.strategy, strat.asset_symbol)
        se = cfg.get("scale_entry") or {}
        slm = float(cfg.get("sl_atr_multiplier") or 0)
        tpm = float(cfg.get("tp_atr_multiplier") or 0)
        is_long = args.side == "buy"
        sl = args.price - slm * args.atr if is_long else args.price + slm * args.atr
        tp = (args.price + tpm * args.atr if is_long else args.price - tpm * args.atr) if tpm else None

        print(f"=== preview {args.strategy} ({args.side}) ===")
        print(f"  asset={strat.asset_symbol}  resolver scale_entry: mode={se.get('mode')} "
              f"levels={se.get('levels')} qty={se.get('quantities')} max={se.get('max_micro_contracts')}")
        print(f"  precio={args.price} ATR={args.atr} SL×={slm} → stop={sl}  TP×={tpm} → tp={tp}\n")

        sig = SimpleNamespace(
            mapped_symbol=strat.asset_symbol, action=args.side, price=args.price,
            quantity=1, sentiment=("bullish" if is_long else "short"),
            signal_role=("entry_long" if is_long else "entry_short"),
            strategy_id=args.strategy, id=uuid.uuid4(),
        )
        pr = SimpleNamespace(sl_price=sl, tp_price=tp, score=100, atr_value=args.atr,
                             market_data_provider="preview")
        legs = PayloadBuilder().build_scaled(sig, None, cfg, pr)
        kind = "ESCALONADO ✅" if len(legs) > 1 or any(
            l.get("orderType") == "limit" for l in legs) else "entrada única (market)"
        print(f"  → {len(legs)} leg(s)  [{kind}]")
        for i, p in enumerate(legs, 1):
            print(json.dumps(p, indent=2))
        if len(legs) == 1 and legs[0].get("orderType") != "limit" and se.get("mode") == "execute":
            print("\n⚠ mode=execute pero salió market/1 leg: revisa quantities/levels/ATR.")


if __name__ == "__main__":
    asyncio.run(main())
