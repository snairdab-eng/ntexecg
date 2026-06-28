#!/usr/bin/env python3
"""
apply_scale_entry_design_v1 — siembra el DISEÑO de compras escalonadas por estrategia.

SOLO diseño: escribe pipeline_config_json["scale_entry"] en cada StrategyProfile.
NO implementa ejecución escalonada (NTEXECG opera 1 entrada + bracket). mode siempre
"design_only"; stop_mode "common_position_stop". Resuelve por asset_symbol ignorando
retired/quarantined; ambiguo/faltante se reporta. Dry-run por defecto; backup + auditoría.

Uso:
  python -m scripts.apply_scale_entry_design_v1 --dry-run
  python -m scripts.apply_scale_entry_design_v1 --apply
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

INACTIVE = {"retired", "quarantined"}

# Config inicial por MICRO (asset_symbol) — Anexo 19/20.
DESIGN: dict[str, dict] = {
    "MES": dict(levels=[0.75, 1.25], quantities=[0, 1, 4], max_micro_contracts=5),
    "MNQ": dict(levels=[4, 5],       quantities=[0, 2, 2], max_micro_contracts=4),
    "MYM": dict(levels=[1.5, 2],     quantities=[0, 0, 4], max_micro_contracts=4),
    "MGC": dict(levels=[0.5, 0.75],  quantities=[0, 0, 3], max_micro_contracts=3),
    "M2K": dict(levels=[0.5, 1.5],   quantities=[3, 0, 0], max_micro_contracts=3),
    "M6E": dict(levels=[0.5, 0.75],  quantities=[3, 0, 0], max_micro_contracts=3),
    "MJY": dict(levels=[2, 3],       quantities=[0, 3, 0], max_micro_contracts=3),
    "MCL": dict(levels=[0.5, 2.5],   quantities=[0, 0, 3], max_micro_contracts=3),
}


def _se(d: dict) -> dict:
    return {"mode": "design_only", "levels": d["levels"], "quantities": d["quantities"],
            "max_micro_contracts": d["max_micro_contracts"], "stop_mode": "common_position_stop"}


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    apply = args.apply
    print(f"=== Scale-entry DISEÑO — modo: {'APPLY' if apply else 'DRY-RUN'} ===\n")

    async with AsyncSessionLocal() as db:
        strats = (await db.execute(select(Strategy))).scalars().all()
        profs = {p.strategy_id: p for p in
                 (await db.execute(select(StrategyProfile))).scalars().all()}
        by_sym: dict[str, list[Strategy]] = {}
        for s in strats:
            by_sym.setdefault(s.asset_symbol, []).append(s)

        updated, ambiguous, missing, backup = [], [], [], []
        for sym, d in DESIGN.items():
            active = [s for s in by_sym.get(sym, []) if s.status not in INACTIVE]
            if len(active) == 0:
                missing.append(sym); continue
            if len(active) > 1:
                ambiguous.append((sym, [s.strategy_id for s in active])); continue
            s = active[0]
            p = profs.get(s.strategy_id)
            before = (p.pipeline_config_json or {}).get("scale_entry") if p else None
            after = _se(d)
            print(f"── {sym} · {s.strategy_id} [{s.status}]")
            print(f"   scale_entry: {before} → {after}\n")
            updated.append(s.strategy_id)
            backup.append({"strategy_id": s.strategy_id, "scale_entry": before})
            if apply:
                if p is None:
                    p = StrategyProfile(strategy_id=s.strategy_id); db.add(p)
                cfg = dict(p.pipeline_config_json or {})
                cfg["scale_entry"] = after
                p.pipeline_config_json = cfg
                if hasattr(p, "updated_by"):
                    p.updated_by = "scale_design_v1"
                await AuditService().log(
                    db, actor="scale_design_v1", action="UPDATE", object_type="StrategyProfile",
                    object_id=s.strategy_id, old_value={"scale_entry": before},
                    new_value={"scale_entry": after}, reason="scale_entry design (no execution)")

        if apply and backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"scale_entry_backup_{ts}.json"
            bp.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
            print(f"🗄️  Backup → {bp}")
        if apply:
            await db.commit(); print("✅ Diseño de scale-entry escrito.")
        else:
            await db.rollback(); print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.")

        print("\n================ RESUMEN ================")
        print(f"Actualizables ({len(updated)}): {updated}")
        print(f"Ambiguas ({len(ambiguous)}): {ambiguous if ambiguous else '[]'}")
        print(f"Faltantes ({len(missing)}): {missing if missing else '[]'}")
        print("\nDISEÑO solamente — sin ejecución escalonada (1 entrada + bracket). "
              "mode='design_only'. La GUI lo muestra/edita en el tab 'Scale Entry'.")


if __name__ == "__main__":
    asyncio.run(main())
