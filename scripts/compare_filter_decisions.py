#!/usr/bin/env python3
"""compare_filter_decisions — reporte SOLO LECTURA del efecto de los filtros del
Anexo 21 (GC QualityScorer, YM régimen) leyendo el log de decisiones.

Para cada estrategia objetivo, sobre una ventana de N días:
  - GC: distribución de score en entries (Nivel 4), cuántas pasan (≥ score_minimum)
        vs bloqueadas (score_below_minimum), y un "qué pasaría" a 50/55/60.
  - YM: distribución de régimen 1h en entries, cuántas permitidas (ranging) vs
        bloqueadas (regime_not_allowed).
  - Desglose de outcomes por estrategia (sanity de flujo/dispatch).

Nota: el P&L con/sin filtro requiere que lleguen resultados de TradersPost; este
reporte mide la ACTIVIDAD del filtro (bloqueadas vs ejecutadas). NO escribe nada.

Uso:
  python -m scripts.compare_filter_decisions               # 30 días
  python -m scripts.compare_filter_decisions --days 7
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.decision import StrategyDecision
from app.models.strategy import Strategy

TARGET_BASE = {"GC": "score", "YM": "regime"}


def base_instrument(symbol: str | None) -> str | None:
    if not symbol:
        return None
    s = symbol.upper()
    if "GC" in s:
        return "GC"
    if "YM" in s:
        return "YM"
    return None


def _l4(d: StrategyDecision) -> dict:
    return (d.pipeline_execution_json or {}).get("level_4") or {}


def _regime(d: StrategyDecision) -> dict:
    return (d.pipeline_execution_json or {}).get("regime") or {}


def _is_entry_scored(d: StrategyDecision) -> bool:
    l4 = _l4(d)
    return isinstance(l4, dict) and "score" in l4


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    print(f"=== compare_filter_decisions — últimos {args.days} días (desde {cutoff:%Y-%m-%d %H:%M} UTC) ===\n")

    async with AsyncSessionLocal() as db:
        strategies = (await db.execute(select(Strategy))).scalars().all()
        by_base = {}
        for s in strategies:
            b = base_instrument(s.asset_symbol)
            if b in TARGET_BASE:
                by_base.setdefault(b, []).append(s)

        decs = (await db.execute(
            select(StrategyDecision).where(StrategyDecision.decided_at >= cutoff)
            .order_by(StrategyDecision.decided_at)
        )).scalars().all()
        by_sid: dict[str, list] = {}
        for d in decs:
            by_sid.setdefault(d.strategy_id, []).append(d)

        # Desglose general
        print("── Outcomes por estrategia (todas) ──")
        all_sids = sorted({d.strategy_id for d in decs})
        if not all_sids:
            print("  (sin decisiones en la ventana)\n")
        for sid in all_sids:
            c = Counter(d.outcome for d in by_sid[sid])
            print(f"  {sid:42s} {dict(c)}")
        print()

        # GC — score
        for s in by_base.get("GC", []):
            rows = [d for d in by_sid.get(s.strategy_id, []) if _is_entry_scored(d)]
            print(f"── GC  {s.strategy_id}  — entries evaluadas por score: {len(rows)} ──")
            if rows:
                scores = [int(_l4(d).get("score", d.score or 0)) for d in rows]
                passed = sum(1 for d in rows if (_l4(d).get("passed")
                             or d.outcome == "APPROVE"))
                blocked = sum(1 for d in rows if d.block_reason == "score_below_minimum")
                buckets = Counter()
                for sc in scores:
                    b = ("<50" if sc < 50 else "50-54" if sc < 55 else "55-59" if sc < 60
                         else "60-69" if sc < 70 else "70-79" if sc < 80 else "80+")
                    buckets[b] += 1
                order = ["<50", "50-54", "55-59", "60-69", "70-79", "80+"]
                print(f"   pasaron={passed}  bloqueadas(score<min)={blocked}  "
                      f"score: min={min(scores)} med={sorted(scores)[len(scores)//2]} max={max(scores)}")
                print(f"   histograma: " + "  ".join(f"{k}:{buckets.get(k,0)}" for k in order))
                for t in (50, 55, 60):
                    keep = sum(1 for sc in scores if sc >= t)
                    print(f"   si score_minimum={t}: pasarían {keep}/{len(scores)}")
            print()

        # YM — régimen
        for s in by_base.get("YM", []):
            rows = [d for d in by_sid.get(s.strategy_id, []) if _regime(d)]
            print(f"── YM  {s.strategy_id}  — entries con régimen evaluado: {len(rows)} ──")
            if rows:
                regs = Counter(_regime(d).get("regime", "?") for d in rows)
                blocked = sum(1 for d in rows if d.block_reason == "regime_not_allowed")
                allowed = len(rows) - blocked
                print(f"   permitidas(ranging/unknown)={allowed}  bloqueadas(regime_not_allowed)={blocked}")
                print(f"   régimen visto: {dict(regs)}")
            print()

        print("Nota: solo lectura. El P&L con/sin filtro se añadirá cuando lleguen "
              "resultados de TradersPost (demo).")


if __name__ == "__main__":
    asyncio.run(main())
