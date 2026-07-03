#!/usr/bin/env python3
"""apply_regime_gate — activa/desactiva el gate de régimen (L4) en UNA estrategia.

Escribe pipeline_config_json["regime"] = {enabled, timeframe, allowed_regimes}
— la MISMA clave que consume filter_pipeline (L4) y edita la ficha en la UI.
"unknown" sigue fallando ABIERTO (semántica viva intacta: solo bloquea un
régimen CONOCIDO fuera de la lista). Merge, no reemplazo: conserva
scale_entry, windows, filters, etc.

dry-run por defecto; --apply con backup JSON en REPORTES/ + auditoría.
Reversible: --disable --apply quita la clave (o restaurar el backup / la UI).

Uso (servidor, venv):
  python -m scripts.apply_regime_gate --strategy RTY15m_ConfNormal_NC_TST \
      --timeframe 1h --allow trending_bull --allow trending_bear
  ...igual + --apply            # aplicar
  ... --disable --apply         # revertir (quita el gate)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.strategy_profile import StrategyProfile
from app.services.audit_service import AuditService

VALID_REGIMES = ("trending_bull", "trending_bear", "ranging")
VALID_TIMEFRAMES = ("1h", "4h")


def merged_regime_cfg(pj: dict | None, timeframe: str,
                      allowed: list[str]) -> dict:
    """pipeline_config_json con el gate puesto (copia; merge, no reemplazo)."""
    cfg = dict(pj or {})
    cfg["regime"] = {"enabled": True, "timeframe": timeframe,
                     "allowed_regimes": list(allowed)}
    return cfg


def disabled_regime_cfg(pj: dict | None) -> dict:
    """pipeline_config_json sin el gate (misma semántica que la UI: la clave
    se QUITA — enabled sin regímenes sería un no-op confuso, NX-26)."""
    cfg = dict(pj or {})
    cfg.pop("regime", None)
    return cfg


async def apply_gate(
    db, strategy_id: str, timeframe: str | None = None,
    allowed: list[str] | None = None, disable: bool = False,
) -> dict | None:
    """Escribe (o quita) el gate en el StrategyProfile, con auditoría.
    Devuelve el regime anterior (None si no había). NO commitea."""
    if not disable:
        if timeframe not in VALID_TIMEFRAMES:
            raise ValueError(f"timeframe inválido: {timeframe!r} "
                             f"(válidos: {VALID_TIMEFRAMES})")
        bad = [r for r in (allowed or []) if r not in VALID_REGIMES]
        if bad or not allowed:
            raise ValueError(f"allowed_regimes inválidos: {bad or '(vacío)'} "
                             f"(válidos: {VALID_REGIMES})")
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == strategy_id
    ))).scalar_one_or_none()
    if prof is None:
        raise ValueError(f"StrategyProfile no encontrado: {strategy_id}")
    pj = prof.pipeline_config_json or {}
    old = pj.get("regime")
    new_cfg = (disabled_regime_cfg(pj) if disable
               else merged_regime_cfg(pj, timeframe, allowed))
    prof.pipeline_config_json = new_cfg
    prof.version = (prof.version or 1) + 1
    prof.updated_by = "apply_regime_gate"
    await db.flush()
    await AuditService().log(
        db, actor="apply_regime_gate", action="UPDATE",
        object_type="StrategyProfile", object_id=strategy_id,
        old_value={"regime": old},
        new_value={"regime": new_cfg.get("regime")},
        reason=("desactivar gate de régimen (reversión)" if disable else
                "activar gate de régimen L4 (calibración Laboratorio; "
                "unknown falla abierto; vigilar % de bloqueo en Analytics)"),
    )
    return old


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--timeframe", default="1h", choices=VALID_TIMEFRAMES)
    ap.add_argument("--allow", action="append", default=[],
                    choices=VALID_REGIMES,
                    help="régimen permitido (repetir por cada uno)")
    ap.add_argument("--disable", action="store_true",
                    help="quitar el gate (reversión)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    accion = ("QUITAR gate" if args.disable else
              f"regime {args.timeframe} ∈ {args.allow}")
    print(f"=== apply_regime_gate {args.strategy} — {accion} "
          f"({'APPLY' if args.apply else 'DRY-RUN'}) ===\n")

    async with AsyncSessionLocal() as db:
        prof = (await db.execute(select(StrategyProfile).where(
            StrategyProfile.strategy_id == args.strategy
        ))).scalar_one_or_none()
        if prof is None:
            print("❌ StrategyProfile no encontrado.")
            return
        pj = prof.pipeline_config_json or {}
        before = pj.get("regime")
        after = (None if args.disable else
                 {"enabled": True, "timeframe": args.timeframe,
                  "allowed_regimes": args.allow})
        print(f"   antes:    regime={json.dumps(before) if before else '(sin gate)'}")
        print(f"   después:  regime={json.dumps(after) if after else '(sin gate)'}")
        print("   (conserva: SL/TP, ventana, escalonado, filtros, guardarraíles)")
        print("   unknown falla ABIERTO — solo bloquea régimen conocido "
              "fuera de la lista.\n")

        if not args.apply:
            await db.rollback()
            print("ℹ️  DRY-RUN: sin cambios. Usa --apply para aplicar.")
            return

        old = await apply_gate(db, args.strategy, args.timeframe,
                               args.allow, disable=args.disable)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        Path("REPORTES").mkdir(exist_ok=True)
        bp = Path("REPORTES") / f"regime_gate_backup_{args.strategy}_{ts}.json"
        bp.write_text(json.dumps({"regime": old}, indent=2),
                      encoding="utf-8")
        await db.commit()
        print(f"🗄️  Backup → {bp}\n✅ Aplicado.")
        if not args.disable:
            print("👁  VIGILANCIA: observar en Analytics el % de BLOCK "
                  "regime_not_allowed las primeras semanas; revertir con "
                  "--disable --apply si estrangula de más.")


if __name__ == "__main__":
    asyncio.run(main())
