#!/usr/bin/env python3
"""
revert_asset_profiles_v1 — revierte asset_profiles a sus valores neutrales (pre-política),
restaurando desde el backup JSON que generó apply_profile_policy_v1.

Motivo: la calibración vive ahora en StrategyProfile (override del activo). El activo
debe quedar como respaldo NEUTRAL del instrumento, no cargar config de una estrategia.
Así, una estrategia nueva NO hereda calibración ajena.

Restaura por símbolo: sl_atr_multiplier, atr_timeframe, session_config_json.
Hace un backup del estado ACTUAL antes de revertir. Dry-run por defecto.

Uso:
  python -m scripts.revert_asset_profiles_v1 --dry-run
  python -m scripts.revert_asset_profiles_v1 --apply
  python -m scripts.revert_asset_profiles_v1 --file REPORTES/asset_profiles_backup_XXatr.json --apply
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.asset_profile import AssetProfile


def _sl(v):
    return None if v is None else float(v)


def _win(cfg):
    if not cfg:
        return "—"
    return (f"{cfg.get('entry_start','?')}-{cfg.get('entry_end','?')} "
            f"days={cfg.get('days_enabled')} nde={cfg.get('next_day_end')}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--file", default="", help="backup JSON a restaurar (default: el más reciente)")
    args = ap.parse_args()
    apply = args.apply

    path = args.file
    if not path:
        cands = sorted(glob.glob("REPORTES/asset_profiles_backup_*.json"))
        if not cands:
            print("❌ No hay REPORTES/asset_profiles_backup_*.json. Pasa --file.")
            return
        path = cands[-1]
    print(f"=== Revert asset_profiles desde {path} — modo: {'APPLY' if apply else 'DRY-RUN'} ===\n")

    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    by_target = {e["symbol"]: e for e in entries}

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AssetProfile))).scalars().all()
        by_sym = {p.symbol: p for p in rows}

        reverted, not_found, unchanged, prerevert = [], [], [], []
        for sym, tgt in by_target.items():
            p = by_sym.get(sym)
            if p is None:
                not_found.append(sym)
                continue
            cur = {"symbol": sym, "sl_atr_multiplier": _sl(p.sl_atr_multiplier),
                   "atr_timeframe": p.atr_timeframe, "session_config_json": p.session_config_json,
                   "version": p.version}
            tgt_sl = _sl(tgt.get("sl_atr_multiplier"))
            tgt_tf = tgt.get("atr_timeframe")
            tgt_win = tgt.get("session_config_json")
            changed = (cur["sl_atr_multiplier"] != tgt_sl or cur["atr_timeframe"] != tgt_tf
                       or (cur["session_config_json"] or None) != (tgt_win or None))
            print(f"── {sym}")
            print(f"   SL {cur['sl_atr_multiplier']}→{tgt_sl} · ATRtf {cur['atr_timeframe']}→{tgt_tf}")
            print(f"   Vent {_win(cur['session_config_json'])}  →  {_win(tgt_win)}")
            print(f"   {'(revierte)' if changed else '(ya neutral)'}\n")
            if not changed:
                unchanged.append(sym)
                continue
            reverted.append(sym)
            prerevert.append(cur)
            if apply:
                p.sl_atr_multiplier = tgt_sl
                p.atr_timeframe = tgt_tf
                p.session_config_json = tgt_win
                p.version = (p.version or 1) + 1
                p.updated_by = "revert_v1"

        if apply and prerevert:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"asset_profiles_prerevert_{ts}.json"
            bp.write_text(json.dumps(prerevert, indent=2, default=str), encoding="utf-8")
            print(f"🗄️  Backup del estado actual (pre-revert) → {bp}")
        if apply:
            await db.commit()
            print("✅ Activos revertidos a neutral.\n")
        else:
            await db.rollback()
            print("ℹ️  DRY-RUN: sin cambios. Usa --apply para revertir.\n")

        print("================ RESUMEN ================")
        print(f"Revertidos ({len(reverted)}): {reverted}")
        print(f"Ya neutrales ({len(unchanged)}): {unchanged}")
        print(f"No encontrados ({len(not_found)}): {not_found}")
        print("\nLa calibración real sigue en StrategyProfile (override). El activo queda como "
              "respaldo neutral; una estrategia nueva NO hereda calibración ajena.")


if __name__ == "__main__":
    asyncio.run(main())
