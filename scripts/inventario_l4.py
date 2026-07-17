#!/usr/bin/env python3
"""inventario_l4 — inventario y APAGADO del Nivel 4 (quality score + régimen HMM).

Decisión del operador (2026-07-17): la misión es controlar riesgo con SL +
escalonadas (Luxy), no mejorar la señal de LuxAlgo. El N4 no aportó edge y bloqueó
señales válidas (GC 01:55 score 52<55). Se APAGA en producción. El N4 SIGUE en el
código (passthrough honesto NX-04: sin filtros → score 100, quality=UNKNOWN) y el
proyecto de filtros VIVE en el Lab (quality_scorer/HMM intactos — futuro por decidir).

FASE 1 (default, read-only): por cada estrategia, el L4 EFECTIVO desde
pipeline_config_json (bloquearía / passthrough).

FASE 2 (--strategy … / --all, --apply gated): neutraliza — QUITA filters/regime/
score_minimum de pipeline_config_json. Retiro, no disable-in-place: es la misma
convención anti-P2-4 de apply_regime_gate --disable (una clave muerta es huérfana,
P2-4). El resto de la config queda intacto. AuditLog FILTERS_L4_OFF con old/new.
dry-run por defecto; --apply escribe (backup JSON en REPORTES/).

NO toca: pipeline (N4 queda en código), Lab, ventanas L2, guardarraíles, heartbeat,
L3, L5, kill-switch, ni el score_minimum global/de-activo (inerte sin filtros).

Uso (servidor, venv):
  python -m scripts.inventario_l4                                   # FASE 1 (todas)
  python -m scripts.inventario_l4 --strategy GC5m_ContraNormal_ST_WeakConf   # dry-run
  python -m scripts.inventario_l4 --strategy GC5m_… --apply         # aplica + audit
  python -m scripts.inventario_l4 --all --apply                     # todas
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
from app.services.quality_scorer import active_filter_names

# Llaves del Nivel 4 que se retiran de pipeline_config_json (anti-P2-4).
_L4_KEYS = ("filters", "regime", "score_minimum")


def l4_effective(pj: dict | None) -> dict:
    """L4 EFECTIVO leído de pipeline_config_json (sin correr el motor).

    bloquearía = hay score gate REAL (algún filtro del scorer activo) O el gate de
    régimen está habilitado CON lista de permitidos (regime.enabled sin lista es
    no-op, NX-26). passthrough = ninguna de las dos."""
    pj = pj or {}
    filters = active_filter_names(pj)
    regime = pj.get("regime") or {}
    regime_on = bool(regime.get("enabled")) and bool(regime.get("allowed_regimes"))
    bloquearia = bool(filters) or regime_on
    return {
        "active_filters": filters,
        "score_minimum": pj.get("score_minimum"),
        "quality_high_threshold": pj.get("quality_high_threshold"),
        "regime_enabled": bool(regime.get("enabled")),
        "regime_allowed": regime.get("allowed_regimes") or [],
        "regime_on": regime_on,
        "efecto": "bloquearía" if bloquearia else "passthrough",
    }


def neutralize_l4(pj: dict | None) -> tuple[dict, dict]:
    """(pj_neutralizado, removed). QUITA filters/regime/score_minimum; el resto
    intacto. `removed` = {llave: valor} de lo retirado (para el AuditLog)."""
    pj = dict(pj or {})
    removed = {k: pj[k] for k in _L4_KEYS if k in pj}
    for k in _L4_KEYS:
        pj.pop(k, None)
    return pj, removed


def _fmt_row(sid: str, eff: dict) -> str:
    fil = ",".join(eff["active_filters"]) or "—"
    reg = ("on:" + ",".join(eff["regime_allowed"])) if eff["regime_on"] else (
        "enabled(no-op)" if eff["regime_enabled"] else "—")
    smin = eff["score_minimum"] if eff["score_minimum"] is not None else "(hereda)"
    marca = "⛔ BLOQUEARÍA" if eff["efecto"] == "bloquearía" else "✓ passthrough"
    return (f"  {sid:38} score_min={str(smin):>8}  filtros={fil:22} "
            f"regime={reg:24} → {marca}")


async def _inventario(db) -> list[StrategyProfile]:
    rows = list((await db.execute(
        select(StrategyProfile).order_by(StrategyProfile.strategy_id))).scalars())
    print("=== FASE 1 — INVENTARIO L4 (read-only) ===\n")
    n_block = 0
    for prof in rows:
        eff = l4_effective(prof.pipeline_config_json)
        if eff["efecto"] == "bloquearía":
            n_block += 1
        print(_fmt_row(prof.strategy_id, eff))
    print(f"\n  {len(rows)} estrategias · {n_block} con L4 que BLOQUEARÍA · "
          f"{len(rows) - n_block} ya passthrough.")
    return rows


async def _apagar(db, sids: list[str], apply: bool) -> None:
    modo = "APPLY" if apply else "DRY-RUN"
    print(f"=== FASE 2 — APAGADO L4 ({modo}) · {len(sids)} estrategia(s) ===\n")
    backups: dict = {}
    for sid in sids:
        prof = (await db.execute(select(StrategyProfile).where(
            StrategyProfile.strategy_id == sid))).scalar_one_or_none()
        if prof is None:
            print(f"  ❌ {sid}: StrategyProfile no encontrado — omitido.")
            continue
        pj = prof.pipeline_config_json or {}
        new_pj, removed = neutralize_l4(pj)
        if not removed:
            print(f"  ✓ {sid}: ya neutralizada (sin filters/regime/score_minimum).")
            continue
        print(f"  {sid}: retira {list(removed)}")
        print(f"     old: {json.dumps(removed, ensure_ascii=False)}")
        if not apply:
            continue
        backups[sid] = removed
        prof.pipeline_config_json = new_pj
        prof.version = (prof.version or 1) + 1
        prof.updated_by = "inventario_l4"
        await db.flush()
        await AuditService().log(
            db, actor="inventario_l4", action="FILTERS_L4_OFF",
            object_type="StrategyProfile", object_id=sid,
            old_value={k: removed.get(k) for k in _L4_KEYS},
            new_value={k: None for k in _L4_KEYS},
            reason="Nivel 4 (quality score + régimen HMM) retirado de producción "
                   "por decisión del operador 2026-07-17 (el N4 no aportó edge y "
                   "bloqueó señales válidas). N4 sigue en código (passthrough NX-04, "
                   "quality=UNKNOWN); el proyecto de filtros vive en el Lab.")
    if apply and backups:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        Path("REPORTES").mkdir(exist_ok=True)
        bp = Path("REPORTES") / f"filtros_l4_off_backup_{ts}.json"
        bp.write_text(json.dumps(backups, indent=2, ensure_ascii=False),
                      encoding="utf-8")
        await db.commit()
        print(f"\n🗄️  Backup → {bp}\n✅ Aplicado a {len(backups)} estrategia(s).")
    elif apply:
        await db.rollback()
        print("\nℹ️  Nada que aplicar (todas ya neutralizadas).")
    else:
        await db.rollback()
        print("\nℹ️  DRY-RUN: sin cambios. Añade --apply para escribir + auditar.")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Inventario / apagado del Nivel 4.")
    ap.add_argument("--strategy", action="append", default=[],
                    help="estrategia a apagar (repetir por cada una)")
    ap.add_argument("--all", action="store_true",
                    help="FASE 2 sobre TODAS las estrategias (con cuidado)")
    ap.add_argument("--apply", action="store_true",
                    help="escribir los cambios (default dry-run)")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        if not args.strategy and not args.all:
            await _inventario(db)                       # FASE 1
            print("\n→ FASE 2: --strategy <id> [--apply]  ó  --all --apply")
            return
        if args.all:
            sids = [p.strategy_id for p in (await db.execute(
                select(StrategyProfile))).scalars()]
        else:
            sids = args.strategy
        await _apagar(db, sids, args.apply)


if __name__ == "__main__":
    asyncio.run(main())
