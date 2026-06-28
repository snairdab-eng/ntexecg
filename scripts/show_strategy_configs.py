#!/usr/bin/env python3
"""show_strategy_configs — diagnóstico SOLO LECTURA de la config efectiva por estrategia.

Resuelve la configuración real que usaría el pipeline (ConfigResolver:
GlobalProfile < AssetProfile < StrategyProfile) y la muestra por estrategia:
status, modo/dry_run, SL/TP ATR, ventana, score_minimum + filtros de calidad,
gate de régimen, y diseño scale_entry. Marca el estado de las recomendaciones
del Anexo 21 (GC: QualityScorer; YM: régimen ranging; NQ: sin cambios).

NO escribe nada (sin commit). Uso:
  python -m scripts.show_strategy_configs            # todas
  python -m scripts.show_strategy_configs --all      # incluye retired/quarantined
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver

_DAYS = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]
HIDDEN = {"retired", "quarantined"}

# Anexo 21 — recomendaciones por instrumento base.
REC = {
    "GC": "Activar QualityScorer (score_minimum≈55, filtros activos)",
    "YM": 'Activar gate de régimen (allowed_regimes=["ranging"])',
    "NQ": "Sin cambios (edge contrarian; score frágil; régimen contraproducente)",
}


def base_instrument(symbol: str | None) -> str | None:
    if not symbol:
        return None
    s = symbol.upper()
    for b in ("NQ", "YM", "GC", "ES", "RTY", "CL", "6E", "6J", "2K", "MES"):
        if b in s:
            # normaliza micros equivalentes
            return {"2K": "RTY", "MES": "ES"}.get(b, b)
    return None


def fmt_days(days) -> str:
    days = days or []
    return ",".join(_DAYS[d] for d in days if 0 <= d <= 6) or "—"


def fmt_window(sc: dict | None) -> str:
    if not sc:
        return "— (sin ventana / respaldo)"
    wins = sc.get("windows")
    if wins:
        out = []
        for w in wins:
            d = w.get("days", w.get("days_enabled", []))
            out.append(f"{w.get('start', w.get('entry_start','?'))}-"
                       f"{w.get('end', w.get('entry_end','?'))} "
                       f"[{fmt_days(d)}] nde={w.get('next_day_end', False)}")
        return f"{len(wins)} ventana(s): " + " ; ".join(out)
    return (f"{sc.get('entry_start','?')}-{sc.get('entry_end','?')} ET "
            f"[{fmt_days(sc.get('days_enabled'))}] nde={sc.get('next_day_end', False)}")


def fmt_filters(filters: dict | None) -> str:
    if not filters:
        return "OFF (score=100, pass-through)"
    on = [(n, f.get("weight")) for n, f in filters.items()
          if isinstance(f, dict) and f.get("enabled")]
    if not on:
        return "OFF (definidos pero ninguno enabled)"
    return "ON → " + ", ".join(f"{n}(w={w})" for n, w in on)


def fmt_regime(regime: dict | None) -> str:
    if not regime or not regime.get("enabled"):
        return "OFF"
    return (f"ON → tf={regime.get('timeframe', '1h')} "
            f"allowed={regime.get('allowed_regimes') or '[]'}")


def anexo21_status(base: str, cfg: dict) -> str:
    filters = cfg.get("filters") or {}
    quality_on = any(isinstance(f, dict) and f.get("enabled") for f in filters.values())
    regime = cfg.get("regime") or {}
    regime_ranging = bool(regime.get("enabled")) and "ranging" in (regime.get("allowed_regimes") or [])
    if base == "GC":
        return ("✅ APLICADO" if quality_on else "⏳ PENDIENTE") + \
               f" — {REC['GC']} | score_minimum actual={cfg.get('score_minimum')}"
    if base == "YM":
        return ("✅ APLICADO" if regime_ranging else "⏳ PENDIENTE") + f" — {REC['YM']}"
    if base == "NQ":
        return f"➖ OK — {REC['NQ']}"
    return "— (sin recomendación en Anexo 21)"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="incluye retired/quarantined")
    args = ap.parse_args()

    resolver = ConfigResolver()
    async with AsyncSessionLocal() as db:
        strategies = (await db.execute(
            select(Strategy).order_by(Strategy.asset_symbol, Strategy.created_at)
        )).scalars().all()

        shown = 0
        summary = []
        for s in strategies:
            if not args.all and s.status in HIDDEN:
                continue
            shown += 1
            cfg = await resolver.resolve(db, s.strategy_id, s.asset_symbol)
            prof = (await db.execute(
                select(StrategyProfile).where(StrategyProfile.strategy_id == s.strategy_id)
            )).scalar_one_or_none()
            pj = (prof.pipeline_config_json or {}) if prof else {}
            scale = pj.get("scale_entry")
            base = base_instrument(s.asset_symbol)

            print("=" * 80)
            print(f"{s.strategy_id}   ({s.name})")
            print(f"  activo={s.asset_symbol}  base={base or '?'}  status={s.status}  "
                  f"enabled={s.enabled}  tf={s.timeframe}")
            print(f"  modo={cfg.get('mode')}  dry_run={cfg.get('dry_run')}  "
                  f"traderspost={cfg.get('traderspost_enabled')}")
            print(f"  SL×ATR={cfg.get('sl_atr_multiplier')}  TP×ATR={cfg.get('tp_atr_multiplier')}  "
                  f"ATR(tf={cfg.get('atr_timeframe')}, period={cfg.get('atr_period')})")
            print(f"  Ventana: {fmt_window(cfg.get('session_config_json'))}")
            print(f"  score_minimum={cfg.get('score_minimum')}")
            print(f"  Filtros calidad (Nivel 4): {fmt_filters(cfg.get('filters'))}")
            print(f"  Régimen (HMM): {fmt_regime(cfg.get('regime'))}")
            if scale:
                _m = scale.get('mode')
                _lbl = "EJECUTA ⚠" if _m in ("execute", "live") else "diseño, no ejecuta"
                print(f"  Scale entry ({_lbl}): mode={_m} "
                      f"levels={scale.get('levels')} qty={scale.get('quantities')} "
                      f"max={scale.get('max_micro_contracts')}")
            else:
                print("  Scale entry: —")
            g = pj.get("guardrails") or {}
            if g:
                print(f"  Guardarraíles: {g}")
            print(f"  ANEXO 21: {anexo21_status(base, cfg)}")
            summary.append((s.strategy_id, base, s.status, anexo21_status(base, cfg)))

        print("=" * 80)
        print(f"\n=== RESUMEN ({shown} estrategias mostradas) — estado Anexo 21 ===")
        for sid, base, status, st21 in summary:
            print(f"  {sid:42s} [{base or '?':3s}] {status:12s} {st21}")
        print("\nNota: diagnóstico de solo lectura. No se modificó ninguna configuración.")


if __name__ == "__main__":
    asyncio.run(main())
