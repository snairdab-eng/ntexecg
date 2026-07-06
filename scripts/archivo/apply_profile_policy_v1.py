#!/usr/bin/env python3
"""
apply_profile_policy_v1 — aplica la Política Operativa v1.0 (Anexo 20) a asset_profiles.

Actualiza SOLO lo soportado: session_config_json (ventana), sl_atr_multiplier y
atr_timeframe. Salida principal = nativa LuxAlgo (no se toca). NO toca scale_entry_*,
cantidades ni órdenes múltiples.

v1.1: alinea también los PADRES (ES, NQ, YM, RTY, GC, CL, 6J, 6E) a los mismos
valores que su micro. Los padres son data-only (no se tradean); el alineado es por
consistencia. asset_profiles NO tiene columna production/shadow (vive en Strategy.status).

Uso:
  python -m scripts.apply_profile_policy_v1 --dry-run     # (default)
  python -m scripts.apply_profile_policy_v1 --apply       # escribe (con backup)
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

NY = "America/New_York"


def rth(start: str, end: str, force_flat: str | None = None) -> dict:
    cfg = {"timezone": NY, "days_enabled": [1, 2, 3, 4, 5], "entry_start": start,
           "entry_end": end, "next_day_end": False, "allow_overnight": False,
           "allow_exits_outside_window": True}
    if force_flat:
        cfg["force_flat_time"] = force_flat
    return cfg


def h24() -> dict:
    return {"timezone": NY, "days_enabled": [0, 1, 2, 3, 4, 5], "entry_start": "18:00",
            "entry_end": "17:00", "next_day_end": True, "allow_overnight": True,
            "allow_exits_outside_window": True}


# Objetivo por símbolo. micro = operado; padre = data-only (mismos valores).
_MES = dict(window=rth("09:20", "15:45", "15:55"), sl=2.5, atr_tf="5m")
_MNQ = dict(window=h24(), sl=8.0, atr_tf="5m")
_MYM = dict(window=h24(), sl=8.0, atr_tf="15m")
_MGC = dict(window=rth("09:30", "15:45", "15:55"), sl=2.5, atr_tf="5m")
_M2K = dict(window=rth("09:30", "12:00", "12:10"), sl=4.0, atr_tf="15m")
_M6E = dict(window=rth("09:30", "15:45", "15:55"), sl=2.0, atr_tf="5m")
_MJY = dict(window=h24(), sl=8.0, atr_tf="5m")
_MCL = dict(window=h24(), sl=8.0, atr_tf="15m")

POLICY: dict[str, dict] = {
    # micros (operados)
    "MES": {**_MES, "state": "production"},
    "MNQ": {**_MNQ, "state": "production"},
    "MYM": {**_MYM, "state": "production"},
    "MGC": {**_MGC, "state": "production"},
    "M2K": {**_M2K, "state": "shadow"},
    "M6E": {**_M6E, "state": "shadow"},
    "MJY": {**_MJY, "state": "shadow"},
    "MCL": {**_MCL, "state": "shadow"},
    # padres (data-only, alineados al micro)
    "ES": {**_MES, "state": "data"},
    "NQ": {**_MNQ, "state": "data"},
    "YM": {**_MYM, "state": "data"},
    "GC": {**_MGC, "state": "data"},
    "RTY": {**_M2K, "state": "data"},
    "M6E_PARENT_UNUSED": {},  # placeholder removed below
    "6E": {**_M6E, "state": "data"},
    "6J": {**_MJY, "state": "data"},
    "CL": {**_MCL, "state": "data"},
}
POLICY.pop("M6E_PARENT_UNUSED", None)

# Excepción intencional: 2.0 permitido solo en M6E/6E (su óptimo).
SL_2_OK = {"M6E", "6E"}


def _sl_float(v):
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
    args = ap.parse_args()
    apply = args.apply

    print(f"=== Política v1.1 (micros + padres) — modo: {'APPLY' if apply else 'DRY-RUN'} ===\n")

    bad = [s for s, t in POLICY.items() if t["sl"] == 2.0 and s not in SL_2_OK]
    if bad:
        print(f"❌ ABORT: objetivos con sl=2.0 no permitido: {bad}")
        return

    if not (hasattr(AssetProfile, "status") or hasattr(AssetProfile, "state")):
        print("⚠️  asset_profiles sin columna production/shadow → solo ventana/SL/atr_tf; "
              "estado se reporta pero NO se persiste.\n")

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AssetProfile))).scalars().all()
        by_sym = {p.symbol: p for p in rows}

        updated, prod, shadow, data, not_found, unchanged, backup = [], [], [], [], [], [], []
        for sym, tgt in POLICY.items():
            p = by_sym.get(sym)
            if p is None:
                not_found.append(sym)
                continue
            before = {"symbol": sym, "sl_atr_multiplier": _sl_float(p.sl_atr_multiplier),
                      "atr_timeframe": p.atr_timeframe, "session_config_json": p.session_config_json,
                      "version": p.version}
            changed = (before["sl_atr_multiplier"] != float(tgt["sl"])
                       or before["atr_timeframe"] != tgt["atr_tf"]
                       or (before["session_config_json"] or {}) != tgt["window"])
            print(f"── {sym} [{tgt['state']}]")
            print(f"   SL {before['sl_atr_multiplier']}→{tgt['sl']} · "
                  f"ATRtf {before['atr_timeframe']}→{tgt['atr_tf']}")
            print(f"   Vent {_win(before['session_config_json'])}  →  {_win(tgt['window'])}")
            print(f"   {'(cambia)' if changed else '(sin cambios)'}\n")
            {"production": prod, "shadow": shadow, "data": data}[tgt["state"]].append(sym)
            if not changed:
                unchanged.append(sym)
                continue
            updated.append(sym)
            backup.append(before)
            if apply:
                p.sl_atr_multiplier = float(tgt["sl"])
                p.atr_timeframe = tgt["atr_tf"]
                p.session_config_json = tgt["window"]
                p.version = (p.version or 1) + 1
                p.updated_by = "policy_v1.1"

        if apply and backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"asset_profiles_backup_{ts}.json"
            bp.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
            print(f"🗄️  Backup de {len(backup)} perfiles → {bp}")
        if apply:
            await db.commit()
            print("✅ Cambios escritos.\n")
        else:
            await db.rollback()
            print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.\n")

    print("================ RESUMEN ================")
    print(f"Actualizados ({len(updated)}): {updated}")
    print(f"Production ({len(prod)}): {prod}")
    print(f"Shadow ({len(shadow)}): {shadow}")
    print(f"Padres/data ({len(data)}): {data}")
    print(f"Sin cambios ({len(unchanged)}): {unchanged}")
    print(f"No encontrados ({len(not_found)}): {not_found}")
    print("\nNota: production/shadow NO se persiste en asset_profiles (vive en Strategy.status).")


if __name__ == "__main__":
    asyncio.run(main())
