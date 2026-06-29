#!/usr/bin/env python3
"""apply_quality_filter — activa el QualityScorer (Nivel 4) en UNA estrategia.

Mergea en pipeline_config_json: filters (4 subscores peso igual) + score_minimum.
NO toca SL/TP, ventana, escalonado ni nada más. dry-run por defecto; --apply;
backup JSON + auditoría. Exige --strategy explícito.

Uso:
  python -m scripts.apply_quality_filter --strategy ES5m --score 55
  python -m scripts.apply_quality_filter --strategy ES5m --score 55 --apply
"""
from __future__ import annotations
import argparse, asyncio, json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.strategy_profile import StrategyProfile
from app.services.audit_service import AuditService

FILTERS = {
    "volume_relative": {"enabled": True, "weight": 25},
    "atr_normalized":  {"enabled": True, "weight": 25},
    "vwap_position":    {"enabled": True, "weight": 25},
    "time_of_day":      {"enabled": True, "weight": 25},
}

async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--score", type=int, default=55)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    print(f"=== apply_quality_filter {args.strategy} score_minimum={args.score} "
          f"({'APPLY' if args.apply else 'DRY-RUN'}) ===\n")
    async with AsyncSessionLocal() as db:
        prof = (await db.execute(
            select(StrategyProfile).where(StrategyProfile.strategy_id == args.strategy)
        )).scalar_one_or_none()
        if prof is None:
            print("❌ StrategyProfile no encontrado."); return
        pj = dict(prof.pipeline_config_json or {})
        before = {"score_minimum": pj.get("score_minimum"), "filters": pj.get("filters")}
        print(f"   antes:    score_minimum={before['score_minimum']} filters={'sí' if before['filters'] else 'no'}")
        pj["filters"] = FILTERS
        pj["score_minimum"] = args.score
        print(f"   después:  score_minimum={args.score} filters=4 activos (peso 25 c/u)")
        print(f"   (conserva: SL/TP, ventana, escalonado, guardarraíles)\n")
        if args.apply:
            prof.pipeline_config_json = pj
            prof.version = (prof.version or 1) + 1
            prof.updated_by = "apply_quality_filter"
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"quality_filter_backup_{args.strategy}_{ts}.json"
            bp.write_text(json.dumps(before, indent=2, default=str), encoding="utf-8")
            await AuditService().log(db, actor="apply_quality_filter", action="UPDATE",
                object_type="StrategyProfile", object_id=args.strategy,
                old_value=before, new_value={"score_minimum": args.score, "filters": "4 enabled"},
                reason="Activar QualityScorer (Anexo 23)")
            await db.commit()
            print(f"🗄️  Backup → {bp}\n✅ Aplicado.")
        else:
            await db.rollback(); print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.")

if __name__ == "__main__":
    asyncio.run(main())
