#!/usr/bin/env python3
"""apply_cancel_after — cancel_after de DISEÑO por estrategia (Laboratorio F3).

Mapea cada estrategia con StrategyProfile → instrumento del lab (micro→macro)
y su pierna límite más profunda ACTIVA: piernas con qty>0 en algún destino
EFECTIVO (resolve_destinations vivo — respeta perfiles que overridean
levels/quantities y la base que se cae sin webhook) y modo execute/live.
La profundidad se redondea HACIA ARRIBA en la grilla del lab
(PULLBACK_LEVELS) y se lee el cancel_after de diseño del cache del camino A
(REPORTES/lab_features_<SYM>.json → meta.pullback) — MISMO estimador que
pullback_timing (min(3600, p90·60+60)): UNA sola caducidad (NX-17/NX-28).

Sin pierna límite activa (entrada única o adds con qty 0) → la estrategia se
salta y su timeout queda intacto (6E/6J/RTY hoy). Sin cache del lab →
regenerar con `python -m scripts.lab_analyze --all-summary --stitch-db`.

dry-run por defecto (tabla completa); --apply escribe
entry_reserve_timeout_seconds vía pullback_timing.apply_suggestion (merge +
audit) con backup JSON en REPORTES/, e imprime la lista para copiar A MANO
en TradersPost (Strategy → Settings → "Cancel entry after"; no hay API).

Uso (servidor, venv):
  python -m scripts.apply_cancel_after                      # dry-run, tabla
  python -m scripts.apply_cancel_after --strategy NQ5m_ConfAny_ST_TC
  python -m scripts.apply_cancel_after --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Consola Windows (cp1252) vs unicode de los prints (mismo guard que el lab).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver
from app.services.dispatch_profiles import resolve_destinations
from scripts.lab_analyze import PULLBACK_LEVELS
from scripts.pullback_timing import apply_suggestion

REPORTES = Path("REPORTES")

# Micros del server → instrumento del Laboratorio (MJY es el micro real de
# 6J en symbol_maps; se aceptan también los macros tal cual).
MICRO_TO_LAB = {
    "MES": "ES", "MNQ": "NQ", "M2K": "RTY", "MGC": "GC",
    "MCL": "CL", "M6E": "6E", "MJY": "6J", "M6J": "6J", "MYM": "YM",
    "ES": "ES", "NQ": "NQ", "RTY": "RTY", "GC": "GC",
    "CL": "CL", "6E": "6E", "6J": "6J", "YM": "YM",
}


def lab_instrument(asset_symbol: str | None) -> str | None:
    """Instrumento del lab para un asset_symbol de estrategia (None si no hay)."""
    return MICRO_TO_LAB.get((asset_symbol or "").strip().upper())


def deepest_active_level(destinations: list[dict]) -> float | None:
    """Pierna límite más profunda (×ATR) con qty>0 entre TODOS los destinos
    efectivos. Semántica de payload_builder: la pierna i (i≥2) usa
    levels[i-1]; qty 0 o nivel inexistente = pierna que no se emite; modo
    fuera de execute/live = entrada única. None = market-only (no tocar)."""
    deepest: float | None = None
    for d in destinations:
        se = d.get("scale_entry") or {}
        if se.get("mode") not in ("execute", "live"):
            continue
        levels = [float(x) for x in (se.get("levels") or [])]
        qs = [int(q or 0) for q in (se.get("quantities") or [])]
        for i, q in enumerate(qs):
            if i >= 1 and q > 0 and (i - 1) < len(levels):
                lvl = levels[i - 1]
                deepest = lvl if deepest is None else max(deepest, lvl)
    return deepest


def grid_round_up(
    level: float, grid: tuple = PULLBACK_LEVELS,
) -> tuple[float, bool]:
    """Nivel de la grilla del lab ≥ level (hacia arriba; exacto no sube).
    Más profundo que la grilla → clampa al máximo y lo marca (el cancel_after
    a 5.0 ya suele estar topado a 3600 — conservador)."""
    for g in grid:
        if g >= level - 1e-9:
            return g, False
    return grid[-1], True


def load_pullback_meta(instrument: str) -> dict | None:
    """meta.pullback del cache del camino A ({str(nivel) → agregado})."""
    p = REPORTES / f"lab_features_{instrument}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return (data.get("meta") or {}).get("pullback")


def plan_row(
    strategy_id: str, asset_symbol: str | None, destinations: list[dict],
    pullback_meta: dict | None, current: int | None,
) -> dict:
    """Una fila del plan (pura): instrumento, pierna, grilla y valor de diseño."""
    row = {
        "strategy": strategy_id, "asset": asset_symbol,
        "instrument": lab_instrument(asset_symbol),
        "deepest": None, "grid": None, "clamped": False,
        "fill_rate": None, "t_p90": None, "cancel_after": None,
        "current": current, "status": None,
    }
    if row["instrument"] is None:
        row["status"] = "sin instrumento lab"
        return row
    row["deepest"] = deepest_active_level(destinations)
    if row["deepest"] is None:
        row["status"] = "sin pierna límite activa"
        return row
    if pullback_meta is None:
        row["status"] = "sin cache lab (regenerar --all-summary)"
        return row
    row["grid"], row["clamped"] = grid_round_up(row["deepest"])
    d = pullback_meta.get(str(row["grid"])) or {}
    row["fill_rate"] = d.get("fill_rate")
    row["t_p90"] = d.get("t_p90")
    row["cancel_after"] = d.get("cancel_after")
    row["status"] = ("aplicar" if row["cancel_after"] is not None
                     else "sin datos pullback en ese nivel")
    return row


def _fmt(v, suffix="") -> str:
    return f"{v}{suffix}" if v is not None else "—"


def print_table(rows: list[dict]) -> None:
    print(f"{'estrategia':38} {'instr':5} {'pierna':7} {'grilla':7} "
          f"{'fill%':6} {'p90':5} {'diseño':7} {'actual':7} estado")
    for r in rows:
        grid = (f"{r['grid']}⚠" if r["clamped"] else _fmt(r["grid"]))
        print(f"{r['strategy']:38} {_fmt(r['instrument']):5} "
              f"{_fmt(r['deepest']):7} {grid:7} "
              f"{_fmt(r['fill_rate']):6} {_fmt(r['t_p90']):5} "
              f"{_fmt(r['cancel_after']):7} {_fmt(r['current']):7} "
              f"{r['status']}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", help="solo esta estrategia (default: todas)")
    ap.add_argument("--apply", action="store_true",
                    help="escribir entry_reserve_timeout_seconds "
                         "(dry-run sin esto)")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        q = select(StrategyProfile)
        if args.strategy:
            q = q.where(StrategyProfile.strategy_id == args.strategy)
        profs = (await db.execute(
            q.order_by(StrategyProfile.strategy_id))).scalars().all()
        if not profs:
            print("❌ Sin StrategyProfile que procesar.")
            return

        resolver = ConfigResolver()
        meta_cache: dict[str, dict | None] = {}
        rows: list[dict] = []
        for prof in profs:
            strat = (await db.execute(select(Strategy).where(
                Strategy.strategy_id == prof.strategy_id
            ))).scalar_one_or_none()
            asset = strat.asset_symbol if strat else None
            cfg = await resolver.resolve(db, prof.strategy_id, asset)
            dests = resolve_destinations(cfg)
            instr = lab_instrument(asset)
            if instr is not None and instr not in meta_cache:
                meta_cache[instr] = load_pullback_meta(instr)
            current = (prof.pipeline_config_json or {}).get(
                "entry_reserve_timeout_seconds")
            rows.append(plan_row(
                prof.strategy_id, asset, dests,
                meta_cache.get(instr) if instr else None, current,
            ))

        print(f"=== cancel_after de diseño (Laboratorio F3) — "
              f"{len(rows)} estrategia(s) "
              f"[{'APPLY' if args.apply else 'DRY-RUN'}] ===\n")
        print_table(rows)

        todo = [r for r in rows if r["status"] == "aplicar"
                and r["cancel_after"] != r["current"]]
        if not args.apply:
            print(f"\nℹ️  DRY-RUN: sin cambios. {len(todo)} por aplicar "
                  "(usa --apply tras verificar la tabla).")
            return
        if not todo:
            print("\n(nada que aplicar: sin filas 'aplicar' con valor nuevo)")
            return

        applied: dict = {}
        for r in todo:
            old = await apply_suggestion(
                db, r["strategy"], r["cancel_after"],
                actor="apply_cancel_after",
                reason=(f"cancel_after de diseño (lab F3): pierna "
                        f"{r['deepest']}×ATR → grilla {r['grid']} "
                        f"({r['instrument']}, p90 {r['t_p90']}m)"),
            )
            applied[r["strategy"]] = {"old": old, "new": r["cancel_after"]}
            print(f"\n✅ {r['strategy']}: entry_reserve_timeout_seconds "
                  f"{old} → {r['cancel_after']}")

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        REPORTES.mkdir(exist_ok=True)
        bp = REPORTES / f"cancel_after_backup_{ts}.json"
        bp.write_text(json.dumps(applied, indent=2), encoding="utf-8")
        await db.commit()
        print(f"\n🗄️  Backup → {bp}")
        print("\n⚠  Copiar A MANO en TradersPost (Strategy → Settings → "
              "'Cancel entry after') — una sola caducidad para pierna, "
              "reserva y orden:")
        for sid, d in applied.items():
            print(f"   {sid} → {d['new']} s")


if __name__ == "__main__":
    asyncio.run(main())
