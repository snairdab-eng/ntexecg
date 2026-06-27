#!/usr/bin/env python3
"""
apply_strategy_calibration_v1 — escribe la calibración POR ESTRATEGIA (StrategyProfile).

La calibración (Anexos 16–20) salió de los trades de UNA estrategia de LuxAlgo, no del
instrumento. Por eso vive en StrategyProfile (override del activo en ConfigResolver),
NO en asset_profiles. Así, una estrategia nueva en el mismo símbolo NO hereda esta
calibración por accidente.

Escribe por strategy_id: pipeline_config_json["windows"], sl_atr_multiplier, atr_timeframe.
NO toca Strategy.status ni dispatch (todo sigue en paper). Dry-run por defecto + backup.

Uso:
  python -m scripts.apply_strategy_calibration_v1 --dry-run
  python -m scripts.apply_strategy_calibration_v1 --apply
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


def rth_win(start: str, end: str) -> dict:
    return {"days": [1, 2, 3, 4, 5], "start": start, "end": end, "next_day_end": False}


def h24_win() -> dict:
    return {"days": [0, 1, 2, 3, 4, 5], "start": "18:00", "end": "17:00", "next_day_end": True}


# Calibración por strategy_id (cada una salió de los trades de esa estrategia LuxAlgo).
CALIB: dict[str, dict] = {
    "ES5m":                      dict(windows=[rth_win("09:20", "15:45")], sl=2.5, atr_tf="5m"),
    "NQ5m_ConfirmationAny":      dict(windows=[h24_win()],                 sl=8.0, atr_tf="5m"),
    "MicroYM15m_Contrarian":     dict(windows=[h24_win()],                 sl=8.0, atr_tf="15m"),
    "MicroGC5mContrarianNormal": dict(windows=[rth_win("09:30", "15:45")], sl=2.5, atr_tf="5m"),
    "M2K15mConfirmationNormal":  dict(windows=[rth_win("09:30", "12:00")], sl=4.0, atr_tf="15m"),
    "6E5mConfirmationStrong":    dict(windows=[rth_win("09:30", "15:45")], sl=2.0, atr_tf="5m"),
    "6J5mContrarianAny":         dict(windows=[h24_win()],                 sl=8.0, atr_tf="5m"),
    "CL15mContrarianNormal":     dict(windows=[h24_win()],                 sl=8.0, atr_tf="15m"),
}


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    apply = args.apply
    print(f"=== Calibración por ESTRATEGIA — modo: {'APPLY' if apply else 'DRY-RUN'} ===\n")

    async with AsyncSessionLocal() as db:
        strat_ids = {s.strategy_id for s in (await db.execute(select(Strategy))).scalars().all()}
        profs = {p.strategy_id: p for p in
                 (await db.execute(select(StrategyProfile))).scalars().all()}

        updated, missing, backup = [], [], []
        for sid, c in CALIB.items():
            if sid not in strat_ids:
                missing.append(sid)
                print(f"!! {sid}: NO existe como Strategy → se omite (revisar strategy_id)\n")
                continue
            p = profs.get(sid)
            cur_sl = float(p.sl_atr_multiplier) if (p and p.sl_atr_multiplier is not None) else None
            cur_tf = p.atr_timeframe if p else None
            cur_win = (p.pipeline_config_json or {}).get("windows") if p else None
            print(f"── {sid}")
            print(f"   SL {cur_sl}→{c['sl']} · ATRtf {cur_tf}→{c['atr_tf']}")
            print(f"   windows {cur_win} → {c['windows']}\n")
            backup.append({"strategy_id": sid, "sl_atr_multiplier": cur_sl,
                           "atr_timeframe": cur_tf,
                           "pipeline_config_json": (p.pipeline_config_json if p else None)})
            updated.append(sid)
            if apply:
                if p is None:
                    p = StrategyProfile(strategy_id=sid)
                    db.add(p)
                p.sl_atr_multiplier = c["sl"]
                p.atr_timeframe = c["atr_tf"]
                cfg = dict(p.pipeline_config_json or {})
                cfg["windows"] = c["windows"]
                p.pipeline_config_json = cfg
                if hasattr(p, "updated_by"):
                    p.updated_by = "strategy_calib_v1"

        if apply and backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"strategy_profiles_backup_{ts}.json"
            bp.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
            print(f"🗄️  Backup de {len(backup)} StrategyProfiles → {bp}")
        if apply:
            await db.commit()
            print("✅ Calibración escrita en las estrategias.\n")
        else:
            await db.rollback()
            print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.\n")

        print("================ RESUMEN ================")
        print(f"Estrategias calibradas ({len(updated)}): {updated}")
        if missing:
            print(f"NO encontradas ({len(missing)}): {missing}")
        print("\nLa calibración vive en StrategyProfile y OVERRIDE el activo (ConfigResolver). "
              "Una estrategia nueva en el mismo símbolo NO hereda esta calibración. "
              "Status/dispatch intactos (paper).")


if __name__ == "__main__":
    asyncio.run(main())
