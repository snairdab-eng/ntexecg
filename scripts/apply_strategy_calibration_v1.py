#!/usr/bin/env python3
"""
apply_strategy_calibration_v1 — calibración POR ESTRATEGIA (capa correcta).

La calibración salió de los trades de UNA estrategia de LuxAlgo, no del instrumento.
Se escribe en StrategyProfile (que el ConfigResolver prioriza sobre asset_profiles):
  - pipeline_config_json["windows"]  → ventana de la estrategia (SessionValidator)
  - sl_atr_multiplier
  - atr_timeframe
NO toca asset_profiles, NO toca Strategy.status, NO activa operación real.

Resolución por asset_symbol (micro): si un símbolo tiene >1 estrategia → AMBIGUO, se salta
y se reporta. Si no tiene estrategia → FALTANTE. Dry-run por defecto; backup antes de --apply.

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
from app.models.asset_profile import AssetProfile
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile


def rth_win(start: str, end: str) -> dict:
    return {"days": [1, 2, 3, 4, 5], "start": start, "end": end, "next_day_end": False}


def h24_win() -> dict:
    return {"days": [0, 1, 2, 3, 4, 5], "start": "18:00", "end": "17:00", "next_day_end": True}


# Calibración por MICRO (asset_symbol). La estrategia se resuelve en runtime.
CALIB: dict[str, dict] = {
    "MES": dict(windows=[rth_win("09:20", "15:45")], sl=2.5, atr_tf="5m"),
    "MGC": dict(windows=[rth_win("09:30", "15:45")], sl=2.5, atr_tf="5m"),
    "M2K": dict(windows=[rth_win("09:30", "12:00")], sl=4.0, atr_tf="15m"),
    "M6E": dict(windows=[rth_win("09:30", "15:45")], sl=2.0, atr_tf="5m"),
    "MCL": dict(windows=[h24_win()],                sl=8.0, atr_tf="15m"),
    "MJY": dict(windows=[h24_win()],                sl=8.0, atr_tf="5m"),
    # confirmados desde la lista de estrategias (1 por símbolo):
    "MNQ": dict(windows=[h24_win()],                sl=8.0, atr_tf="5m"),
    "MYM": dict(windows=[h24_win()],                sl=8.0, atr_tf="15m"),
}


def _sl(v):
    return None if v is None else float(v)


def _win(windows):
    if not windows:
        return "—"
    return " | ".join(f"{w.get('start','?')}-{w.get('end','?')} d{w.get('days')}"
                      f"{' nde' if w.get('next_day_end') else ''}" for w in windows)


def _asset_win(cfg):
    if not cfg:
        return "—"
    return (f"{cfg.get('entry_start','?')}-{cfg.get('entry_end','?')} "
            f"d{cfg.get('days_enabled')} nde={cfg.get('next_day_end')}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    apply = args.apply
    print(f"=== Calibración por ESTRATEGIA — modo: {'APPLY' if apply else 'DRY-RUN'} ===\n")

    async with AsyncSessionLocal() as db:
        strats = (await db.execute(select(Strategy).order_by(Strategy.asset_symbol))).scalars().all()
        profs = {p.strategy_id: p for p in
                 (await db.execute(select(StrategyProfile))).scalars().all()}
        assets = {a.symbol: a for a in (await db.execute(select(AssetProfile))).scalars().all()}

        # 1) Estrategias encontradas
        print("── (1) ESTRATEGIAS ENCONTRADAS ──")
        print(f"{'asset':<7}{'strategy_id':<28}{'status':<12}{'profile':<8} name")
        by_symbol: dict[str, list[Strategy]] = {}
        for s in strats:
            by_symbol.setdefault(s.asset_symbol, []).append(s)
            print(f"{str(s.asset_symbol):<7}{s.strategy_id:<28}{s.status:<12}"
                  f"{'sí' if s.strategy_id in profs else 'no':<8} {s.name}")

        updatable, ambiguous, missing, backup = [], [], [], []
        plan = []  # (symbol, strategy, profile_or_None, calib)
        for sym, c in CALIB.items():
            lst = by_symbol.get(sym, [])
            if len(lst) == 0:
                missing.append(sym)
            elif len(lst) > 1:
                ambiguous.append((sym, [s.strategy_id for s in lst]))
            else:
                s = lst[0]
                plan.append((sym, s, profs.get(s.strategy_id), c))
                updatable.append((sym, s.strategy_id))

        # 2) Before/after de los actualizables
        print("\n── (2) BEFORE / AFTER (estrategias actualizables) ──")
        for sym, s, p, c in plan:
            cur_sl = _sl(p.sl_atr_multiplier) if (p and p.sl_atr_multiplier is not None) else None
            cur_tf = p.atr_timeframe if p else None
            cur_win = (p.pipeline_config_json or {}).get("windows") if p else None
            eff_win = _win(cur_win) if cur_win else f"(hereda activo: {_asset_win((assets.get(sym).session_config_json) if assets.get(sym) else None)})"
            print(f"\n── {sym} · {s.strategy_id} [{s.status}]  (profile: {'sí' if p else 'NO → se crea'})")
            print(f"   ventana efectiva: {eff_win}")
            print(f"                  →  {_win(c['windows'])}")
            print(f"   SL  {cur_sl} → {c['sl']}")
            print(f"   ATRtf {cur_tf} → {c['atr_tf']}")
            backup.append({"strategy_id": s.strategy_id, "sl_atr_multiplier": cur_sl,
                           "atr_timeframe": cur_tf,
                           "pipeline_config_json": (p.pipeline_config_json if p else None)})
            if apply:
                if p is None:
                    p = StrategyProfile(strategy_id=s.strategy_id)
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
            print(f"\n🗄️  Backup de {len(backup)} StrategyProfiles → {bp}")
        if apply:
            await db.commit()
            print("✅ Calibración escrita en las estrategias.")
        else:
            await db.rollback()
            print("\nℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.")

        # 3) asset_profiles neutral?
        print("\n── (3) CONFIRMACIÓN: asset_profiles NEUTRAL ──")
        for sym in CALIB:
            a = assets.get(sym)
            if not a:
                print(f"   {sym}: (sin asset_profile)")
                continue
            sl = _sl(a.sl_atr_multiplier)
            has_win = bool((a.session_config_json or {}).get("windows"))
            flag = "" if (sl == 2.0 and not has_win) else "  ⚠ revisar (no parece neutral)"
            print(f"   {sym}: SL {sl} · atr_tf {a.atr_timeframe} · {_asset_win(a.session_config_json)}{flag}")

        # Resumen
        print("\n================ RESUMEN ================")
        print(f"Actualizables ({len(updatable)}): {updatable}")
        print(f"Ambiguas ({len(ambiguous)}): {ambiguous if ambiguous else '[]'}")
        print(f"Faltantes ({len(missing)}): {missing if missing else '[]'}")
        print("\nConfigResolver: StrategyProfile OVERRIDE asset_profiles "
              "(merge global<asset<strategy; ver config_resolver.py §120-165). "
              "La calibración de la estrategia manda; el activo es respaldo neutral. "
              "Status/dispatch intactos (paper).")


if __name__ == "__main__":
    asyncio.run(main())
