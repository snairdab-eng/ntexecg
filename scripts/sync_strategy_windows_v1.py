#!/usr/bin/env python3
"""
sync_strategy_windows_v1 — quita las ventanas PROPIAS de las estrategias para que
hereden la ventana del activo (asset_profiles), centralizando la política.

Problema: una estrategia con pipeline_config_json["windows"] OVERRIDE la ventana del
activo (ConfigResolver §158-165 + SessionValidator). Si esas ventanas son viejas
(RTH heredado del setup anterior), tapan la calibración del activo (p. ej. 24h).

Fix: elimina la clave "windows" del pipeline_config_json de cada StrategyProfile →
SessionValidator cae a la ventana del activo. NO toca status, dispatch, ni nada más.

Reporta además (sin tocar) los overrides de SL/atr_tf por estrategia, para que los
veas; con --clear-sl-atr también los limpia (para heredar el activo).

Seguro: dry-run por defecto; backup JSON antes de --apply.

Uso:
  python -m scripts.sync_strategy_windows_v1 --dry-run
  python -m scripts.sync_strategy_windows_v1 --apply
  python -m scripts.sync_strategy_windows_v1 --apply --clear-sl-atr
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


def _win_summary(windows) -> str:
    if not windows:
        return "—"
    out = []
    for w in windows:
        days = w.get("days", w.get("days_enabled", []))
        out.append(f"{w.get('start', w.get('entry_start','?'))}-"
                   f"{w.get('end', w.get('entry_end','?'))} d{days}")
    return " | ".join(out)


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--clear-sl-atr", action="store_true",
                    help="también limpia sl_atr_multiplier/atr_timeframe propios (hereda activo)")
    args = ap.parse_args()
    apply = args.apply

    print(f"=== Sync ventanas de estrategia — modo: {'APPLY' if apply else 'DRY-RUN'} "
          f"{'(+clear sl/atr)' if args.clear_sl_atr else ''} ===\n")

    async with AsyncSessionLocal() as db:
        strats = (await db.execute(select(Strategy).order_by(Strategy.asset_symbol))).scalars().all()
        profs = {p.strategy_id: p for p in
                 (await db.execute(select(StrategyProfile))).scalars().all()}

        touched, with_win, sl_over, backup = [], [], [], []
        for s in strats:
            p = profs.get(s.strategy_id)
            pcfg = (p.pipeline_config_json or {}) if p else {}
            windows = pcfg.get("windows")
            sl = float(p.sl_atr_multiplier) if (p and p.sl_atr_multiplier is not None) else None
            atr_tf = p.atr_timeframe if p else None
            mark = []
            if windows:
                mark.append("WINDOWS propias")
                with_win.append(s.strategy_id)
            if sl is not None or atr_tf is not None:
                mark.append(f"SL/atr propios (sl={sl}, atr_tf={atr_tf})")
                sl_over.append(s.strategy_id)
            print(f"── {s.asset_symbol} · {s.strategy_id} [{s.status}]")
            print(f"   ventanas estrategia: {_win_summary(windows)}")
            if mark:
                print(f"   override: {', '.join(mark)}")
            if not p:
                print("   (sin StrategyProfile) \n")
                continue
            need = bool(windows) or (args.clear_sl_atr and (sl is not None or atr_tf is not None))
            print(f"   {'→ se limpiará para heredar el activo' if need else '(nada que limpiar)'}\n")
            if not need:
                continue
            touched.append(s.strategy_id)
            backup.append({"strategy_id": s.strategy_id,
                           "pipeline_config_json": p.pipeline_config_json,
                           "sl_atr_multiplier": sl, "atr_timeframe": atr_tf})
            if apply:
                if windows:
                    newcfg = dict(p.pipeline_config_json or {})
                    newcfg.pop("windows", None)
                    p.pipeline_config_json = newcfg
                if args.clear_sl_atr:
                    p.sl_atr_multiplier = None
                    p.atr_timeframe = None

        if apply and backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"strategy_profiles_backup_{ts}.json"
            bp.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
            print(f"🗄️  Backup de {len(backup)} StrategyProfiles → {bp}")
        if apply:
            await db.commit()
            print("✅ Cambios escritos.\n")
        else:
            await db.rollback()
            print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.\n")

        print("================ RESUMEN ================")
        print(f"Estrategias con ventana propia: {with_win}")
        print(f"Estrategias con SL/atr propios: {sl_over}")
        print(f"Estrategias que se limpiarían/limpiaron: {touched}")
        print("\nTras limpiar 'windows', la estrategia hereda la ventana del activo "
              "(asset_profiles). Status/dispatch NO se tocan.")


if __name__ == "__main__":
    asyncio.run(main())
