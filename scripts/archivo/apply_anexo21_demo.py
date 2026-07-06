#!/usr/bin/env python3
"""apply_anexo21_demo — aplica las recomendaciones del Anexo 21 a las estrategias
de GC e YM (para forward-test en TradersPost demo).

GC (MicroGC5m…): activa QualityScorer → score_minimum=55 + 4 filtros (peso igual).
YM (MicroYM15m…): activa gate de régimen → allowed_regimes=["ranging"] en 1h.

Mergea sobre StrategyProfile.pipeline_config_json conservando scale_entry,
guardrails y windows existentes. NO toca SL/TP, ventana, status ni traderspost.

dry-run por defecto; requiere --apply; backup JSON + auditoría. Idempotente.

Uso:
  python -m scripts.apply_anexo21_demo            # dry-run
  python -m scripts.apply_anexo21_demo --apply    # escribe (con backup)
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.audit_service import AuditService

HIDDEN = {"retired", "quarantined"}

# Delta a mergear por instrumento base (Anexo 21).
TARGETS: dict[str, dict] = {
    "GC": {
        "score_minimum": 55,
        "filters": {
            "volume_relative": {"enabled": True, "weight": 25},
            "atr_normalized": {"enabled": True, "weight": 25},
            "vwap_position": {"enabled": True, "weight": 25},
            "time_of_day": {"enabled": True, "weight": 25},
        },
    },
    "YM": {
        "regime": {"enabled": True, "timeframe": "1h", "allowed_regimes": ["ranging"]},
    },
}


def base_instrument(symbol: str | None) -> str | None:
    if not symbol:
        return None
    s = symbol.upper()
    if "GC" in s:
        return "GC"
    if "YM" in s:
        return "YM"
    return None


def relevant(pj: dict) -> dict:
    return {k: pj.get(k) for k in ("score_minimum", "filters", "regime")}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    apply = args.apply
    print(f"=== apply_anexo21_demo — modo: {'APPLY' if apply else 'DRY-RUN'} ===\n")

    async with AsyncSessionLocal() as db:
        strategies = (await db.execute(select(Strategy))).scalars().all()
        matched, skipped, backup = [], [], []
        for s in strategies:
            base = base_instrument(s.asset_symbol)
            if base not in TARGETS:
                continue
            if s.status in HIDDEN:
                skipped.append((s.strategy_id, f"status={s.status}"))
                continue
            prof = (await db.execute(
                select(StrategyProfile).where(StrategyProfile.strategy_id == s.strategy_id)
            )).scalar_one_or_none()
            if prof is None:
                skipped.append((s.strategy_id, "sin StrategyProfile"))
                continue
            matched.append((s, prof, base))

        for s, prof, base in matched:
            existing = dict(prof.pipeline_config_json or {})
            delta = TARGETS[base]
            new_pj = {**existing, **delta}
            before = {"strategy_id": s.strategy_id, "pipeline_config_json": existing,
                      "version": prof.version}
            print(f"── {s.strategy_id} [{s.asset_symbol}] base={base} status={s.status}")
            print(f"   antes:    {relevant(existing)}")
            print(f"   después:  {relevant(new_pj)}")
            print(f"   (conserva: scale_entry={'sí' if existing.get('scale_entry') else 'no'}, "
                  f"guardrails={'sí' if existing.get('guardrails') else 'no'}, "
                  f"windows={'sí' if existing.get('windows') else 'no'})\n")
            backup.append(before)
            if apply:
                prof.pipeline_config_json = new_pj
                prof.version = (prof.version or 1) + 1
                prof.updated_by = "apply_anexo21_demo"
                await AuditService().log(
                    db, actor="apply_anexo21_demo", action="UPDATE",
                    object_type="StrategyProfile", object_id=s.strategy_id,
                    old_value=relevant(existing), new_value=relevant(new_pj),
                    reason=f"Anexo 21 {base}: filtros de calidad / régimen (demo)",
                )

        if apply and backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"anexo21_backup_{ts}.json"
            bp.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
            print(f"🗄️  Backup → {bp}")
        if apply:
            await db.commit()
            print("✅ Cambios escritos.\n")
        else:
            await db.rollback()
            print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.\n")

        print("================ RESUMEN ================")
        print(f"Aplicadas ({len(matched)}): {[b['strategy_id'] for b in backup]}")
        print(f"Saltadas ({len(skipped)}): {skipped}")
        print("\nRecuerda: corre  python -m scripts.show_strategy_configs  para verificar.")


if __name__ == "__main__":
    asyncio.run(main())
