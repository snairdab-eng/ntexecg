#!/usr/bin/env python3
"""
apply_profile_policy_v1 — aplica la Política Operativa v1.0 (Anexo 20) a asset_profiles.

Actualiza SOLO lo que NTEXECG ya soporta: session_config_json (ventana),
sl_atr_multiplier y atr_timeframe. La salida principal sigue siendo la nativa de
LuxAlgo (no se toca). NO toca escalonado (scale_entry_*), cantidades ni órdenes
múltiples — eso requiere implementación aparte.

Seguro por diseño:
  • Dry-run por defecto: NO escribe nada salvo que pases --apply.
  • Antes de --apply hace backup JSON de los perfiles afectados.
  • before/after por perfil; validación anti "2.0 genérico"; resumen final.

Estado production/shadow: asset_profiles NO tiene columna de estado (eso vive en
Strategy.status). El script imprime la intención y advierte; no falla por ello.

Uso:
  python -m scripts.apply_profile_policy_v1 --dry-run     # (default) solo muestra
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
    """Ventana de día (L-V) — formato consistente con el seed (Sunday=0)."""
    cfg = {
        "timezone": NY,
        "days_enabled": [1, 2, 3, 4, 5],
        "entry_start": start,
        "entry_end": end,
        "next_day_end": False,
        "allow_overnight": False,
        "allow_exits_outside_window": True,  # salida nativa siempre permitida
    }
    if force_flat:
        cfg["force_flat_time"] = force_flat
    return cfg


def h24() -> dict:
    """Sesión casi-24h (Dom 18:00 → Vie 17:00) — formato del seed _FX_24H."""
    return {
        "timezone": NY,
        "days_enabled": [0, 1, 2, 3, 4, 5],
        "entry_start": "18:00",
        "entry_end": "17:00",
        "next_day_end": True,
        "allow_overnight": True,
        "allow_exits_outside_window": True,
    }


# Política v1.0 (Anexo 20). symbol micro -> objetivo.
POLICY: dict[str, dict] = {
    # ── Production ───────────────────────────────────────────────────────────
    "MES": dict(window=rth("09:20", "15:45", "15:55"), sl=2.5, atr_tf="5m", state="production"),
    "MNQ": dict(window=h24(), sl=8.0, atr_tf="5m", state="production"),
    "MYM": dict(window=h24(), sl=8.0, atr_tf="15m", state="production"),
    "MGC": dict(window=rth("09:30", "15:45", "15:55"), sl=2.5, atr_tf="5m", state="production"),
    # ── Shadow ───────────────────────────────────────────────────────────────
    "M2K": dict(window=rth("09:30", "12:00", "12:10"), sl=4.0, atr_tf="15m", state="shadow"),
    "M6E": dict(window=rth("09:30", "15:45", "15:55"), sl=2.0, atr_tf="5m", state="shadow"),
    "MJY": dict(window=h24(), sl=8.0, atr_tf="5m", state="shadow"),
    "MCL": dict(window=h24(), sl=8.0, atr_tf="15m", state="shadow"),
}
# Excepción intencional: M6E sí lleva 2.0 (su óptimo). El resto NUNCA debe quedar en 2.0.
SL_2_OK = {"M6E"}

# Variante alternativa de cosecha (Anexo 20): MGC 24h shadow. Solo si EXISTE un
# perfil separado para ella (no hay uno por defecto). Se busca por estos símbolos.
OPTIONAL_VARIANTS = {
    "MGC_24H": dict(window=h24(), sl=8.0, atr_tf="5m", state="shadow"),
}


def _sl_float(v) -> float | None:
    return None if v is None else float(v)


def _row_view(p: AssetProfile) -> dict:
    return {
        "symbol": p.symbol,
        "sl_atr_multiplier": _sl_float(p.sl_atr_multiplier),
        "atr_timeframe": p.atr_timeframe,
        "session_config_json": p.session_config_json,
        "version": p.version,
    }


def _win_summary(cfg: dict | None) -> str:
    if not cfg:
        return "—"
    return (f"{cfg.get('entry_start','?')}-{cfg.get('entry_end','?')} "
            f"days={cfg.get('days_enabled')} nde={cfg.get('next_day_end')}")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Aplicar Política Operativa v1.0 a asset_profiles")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="(default) solo muestra, no escribe")
    g.add_argument("--apply", action="store_true", help="escribe los cambios (con backup)")
    args = ap.parse_args()
    apply = args.apply  # sin --apply => dry-run

    mode = "APPLY (escribe BD)" if apply else "DRY-RUN (sin cambios)"
    print(f"=== Política Operativa v1.0 — modo: {mode} ===\n")

    # 0) ¿existe columna de estado en asset_profiles?
    has_state = hasattr(AssetProfile, "status") or hasattr(AssetProfile, "state")
    if not has_state:
        print("⚠️  asset_profiles NO tiene columna production/shadow "
              "(el estado vive en Strategy.status). Se aplican solo ventana/SL/atr_timeframe; "
              "la intención de estado se reporta abajo pero NO se persiste.\n")

    # validación 7: ningún objetivo (salvo M6E) debe ser 2.0
    bad = [s for s, t in POLICY.items() if t["sl"] == 2.0 and s not in SL_2_OK]
    if bad:
        print(f"❌ ABORT: objetivos con sl=2.0 genérico no permitido: {bad}")
        return

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AssetProfile))).scalars().all()
        by_sym = {p.symbol: p for p in rows}

        # incluir variantes opcionales solo si su perfil existe
        targets = dict(POLICY)
        for sym, t in OPTIONAL_VARIANTS.items():
            if sym in by_sym:
                targets[sym] = t

        updated, prod, shadow, not_found, unchanged = [], [], [], [], []
        affected_backup = []

        for sym, tgt in targets.items():
            p = by_sym.get(sym)
            if p is None:
                not_found.append(sym)
                continue

            before = _row_view(p)
            after = {
                "symbol": sym,
                "sl_atr_multiplier": float(tgt["sl"]),
                "atr_timeframe": tgt["atr_tf"],
                "session_config_json": tgt["window"],
                "version": (p.version or 1),
            }
            changed = (
                before["sl_atr_multiplier"] != after["sl_atr_multiplier"]
                or before["atr_timeframe"] != after["atr_timeframe"]
                or (before["session_config_json"] or {}) != after["session_config_json"]
            )

            print(f"── {sym}  [{tgt['state']}]")
            print(f"   SL:     {before['sl_atr_multiplier']}  →  {after['sl_atr_multiplier']}")
            print(f"   ATR tf: {before['atr_timeframe']}  →  {after['atr_timeframe']}")
            print(f"   Vent.:  {_win_summary(before['session_config_json'])}")
            print(f"        →  {_win_summary(after['session_config_json'])}")
            print(f"   {'(cambia)' if changed else '(sin cambios)'}\n")

            (prod if tgt["state"] == "production" else shadow).append(sym)
            if not changed:
                unchanged.append(sym)
                continue
            updated.append(sym)
            affected_backup.append(before)

            if apply:
                p.sl_atr_multiplier = after["sl_atr_multiplier"]
                p.atr_timeframe = after["atr_timeframe"]
                p.session_config_json = after["session_config_json"]
                p.version = (p.version or 1) + 1
                p.updated_by = "policy_v1"

        # 6) backup antes de commit
        if apply and affected_backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            bdir = Path("REPORTES")
            bdir.mkdir(exist_ok=True)
            bpath = bdir / f"asset_profiles_backup_{ts}.json"
            bpath.write_text(json.dumps(affected_backup, indent=2, default=str), encoding="utf-8")
            print(f"🗄️  Backup de {len(affected_backup)} perfiles → {bpath}")

        if apply:
            await db.commit()
            print("✅ Cambios escritos en la base.\n")
        else:
            await db.rollback()
            print("ℹ️  DRY-RUN: no se escribió nada. Usa --apply para aplicar.\n")

    # 8) resumen
    print("================ RESUMEN ================")
    print(f"Actualizados ({len(updated)}): {updated}")
    print(f"Production   ({len(prod)}): {prod}")
    print(f"Shadow       ({len(shadow)}): {shadow}")
    print(f"Sin cambios  ({len(unchanged)}): {unchanged}")
    print(f"No encontrados ({len(not_found)}): {not_found}")
    if not has_state:
        print("\nNota: production/shadow NO se persistió (sin columna en asset_profiles). "
              "Definir el estado al crear/editar la Strategy correspondiente.")


if __name__ == "__main__":
    asyncio.run(main())
