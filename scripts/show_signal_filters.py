#!/usr/bin/env python3
"""show_signal_filters — qué filtros corrieron en una señal y con qué config.

Imprime, para una entrada (la última de la estrategia o una por --signal):
  - la traza real del pipeline (pipeline_execution_json), nivel por nivel,
  - la config de CALIDAD resuelta de la estrategia (filters, regime, score_minimum),
para ver si el score reflejó filtros reales o el 100 por defecto (sin filtros).

Uso (servidor, venv):
  source .venv/bin/activate
  python -m scripts.show_signal_filters --strategy NQ5m_ConfAny_ST_TC
  python -m scripts.show_signal_filters --strategy NQ5m_ConfAny_ST_TC --signal fa25ddd1
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.services.config_resolver import ConfigResolver
from app.services.repositories import get_strategy_by_id


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--signal", help="prefijo del id de decisión o de señal")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(StrategyDecision, NormalizedSignal)
            .join(NormalizedSignal,
                  StrategyDecision.normalized_signal_id == NormalizedSignal.id)
            .where(StrategyDecision.strategy_id == args.strategy,
                   NormalizedSignal.action != "exit")
            .order_by(StrategyDecision.created_at.desc())
        )).all()
        pick = None
        for dec, sig in rows:
            if args.signal:
                if str(dec.id).startswith(args.signal) or str(sig.id).startswith(args.signal):
                    pick = (dec, sig); break
            else:
                pick = (dec, sig); break
        if not pick:
            print("No encontré la entrada."); return
        dec, sig = pick

        print(f"=== {args.strategy} | {sig.ticker_received} {sig.action} @ {sig.price} "
              f"| {dec.created_at} ===")
        print(f"  outcome={dec.outcome} score={dec.score} sl={dec.sl_price} "
              f"atr={dec.atr_value} block={dec.block_reason}")

        print("\n-- Traza real del pipeline (lo que de verdad corrió) --")
        print(json.dumps(dec.pipeline_execution_json or {}, indent=2, default=str,
                         ensure_ascii=False))

        strat = await get_strategy_by_id(db, args.strategy)
        cfg = await ConfigResolver().resolve(db, args.strategy,
                                             getattr(strat, "asset_symbol", None))
        filters = cfg.get("filters") or {}
        regime = cfg.get("regime") or {}
        print("\n-- Config de CALIDAD de la estrategia --")
        print(f"  score_minimum : {cfg.get('score_minimum')}")
        print(f"  filters       : {filters if filters else '∅ (ninguno → score 100 por defecto)'}")
        print(f"  regime (HMM)  : "
              f"{regime if regime.get('enabled') else '∅ (desactivado)'}")

        if not filters and not regime.get("enabled"):
            print("\n  ➜ Conclusión: NO había filtros de calidad ni gate de régimen. "
                  "El score 100 es el PASE POR DEFECTO, no una medida de calidad.")
        else:
            print("\n  ➜ Había filtros/gate activos; el score refleja esa evaluación.")


if __name__ == "__main__":
    asyncio.run(main())
