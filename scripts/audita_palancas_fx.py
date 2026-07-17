"""FIX-FX-BACKSTOP — auditor READ-ONLY de las palancas VIVAS envenenadas.

Pregunta que responde: ¿alguna config viva YA tiene una palanca colapsada por el
round(_,2) pensado para índices (el backstop FX aplastado a 0, o cualquier valor
por debajo de 1 tick / fuera de la rejilla del instrumento)? 6J no tiene por qué
ser la única — este script las lista TODAS para que el operador decida corregir.

Qué revisa, por estrategia con `pipeline_config_json`:
  · backstop_points — está en PUNTOS DE PRECIO. Colapsado (==0) → NO hay stop de
    precio fijo efectivo (cae al SL×ATR sin avisar). Sub-tick (0<|v|<tick) →
    por debajo de la resolución del instrumento. Fuera de rejilla → el despacho
    lo snapea, pero conviene re-derivar.
  · scale_entry.levels (C2/C3) y c1_depth_atr — múltiplos ×ATR ADIMENSIONALES:
    no colapsan por el tick, pero un nivel 0 con cantidad>0 se marca como posible
    colapso (pierna a mercado donde se esperaba profundidad).

El tick sale del Symbol Mapper (SymbolMap.tick_size por tv_symbol == asset_symbol),
con respaldo en el catálogo `mr_report.TICK_SIZE` vía el instrumento raíz.

INVARIANTES: solo lectura. SELECT únicamente. No abre transacción de escritura,
no commitea, no despacha. Imprime un reporte y sale 0 (el operador decide).

Uso:
    .venv/Scripts/python.exe scripts/audita_palancas_fx.py
    (DATABASE_URL apuntando a la BD viva del server)
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from scripts.mr_report import FX_INSTRUMENTS, TICK_SIZE


def _tick_por_activo_raiz(asset_symbol: str | None) -> float | None:
    """Respaldo: instrumento raíz del catálogo (MES→ES) → TICK_SIZE."""
    if not asset_symbol:
        return None
    try:
        from scripts.lab_manifest import MICRO_TO_LAB
        raiz = MICRO_TO_LAB.get(asset_symbol, asset_symbol)
    except Exception:
        raiz = asset_symbol
    return TICK_SIZE.get(raiz)


def _on_grid(value: float, tick: float) -> bool:
    try:
        return (Decimal(str(value)) / Decimal(str(tick))) % 1 == 0
    except Exception:
        return True


def _revisar(cfg: dict, tick: float | None, activo_fx: bool) -> list[dict]:
    """Hallazgos (nivel, campo, valor, detalle) de UNA config viva."""
    h: list[dict] = []
    bp = cfg.get("backstop_points")
    if isinstance(bp, (int, float)) and not isinstance(bp, bool):
        if bp == 0:
            h.append({"nivel": "CRÍTICO", "campo": "backstop_points", "valor": bp,
                      "detalle": "COLAPSADO a 0 — sin stop de precio fijo efectivo "
                                 "(cae al SL×ATR en silencio). El síntoma del bug."})
        elif tick and abs(bp) < float(tick):
            h.append({"nivel": "CRÍTICO", "campo": "backstop_points", "valor": bp,
                      "detalle": f"sub-tick (|{bp:g}| < tick {tick:g}) — bajo la "
                                 "resolución del instrumento."})
        elif tick and not _on_grid(bp, tick):
            h.append({"nivel": "INFO", "campo": "backstop_points", "valor": bp,
                      "detalle": f"fuera de la rejilla del tick {tick:g} (el "
                                 "despacho lo snapea; conviene re-derivar)."})
    se = cfg.get("scale_entry") or {}
    if isinstance(se, dict):
        qty = list(se.get("quantities") or [])
        levels = list(se.get("levels") or [])          # [C2_depth, C3_depth] ×ATR
        for i, lv in enumerate(levels):
            c_idx = i + 1                                # levels[0]→C2, levels[1]→C3
            q = qty[c_idx] if c_idx < len(qty) else 0
            if isinstance(lv, (int, float)) and lv == 0 and q and q > 0:
                h.append({"nivel": "AVISO", "campo": f"scale_entry.levels[{i}] (C{c_idx+1})",
                          "valor": lv,
                          "detalle": f"nivel 0 con cantidad {q}>0 — pierna a mercado "
                                     "donde se esperaba profundidad (posible colapso)."})
        c1 = se.get("c1_depth_atr")
        if isinstance(c1, (int, float)) and c1 == 0:
            h.append({"nivel": "AVISO", "campo": "scale_entry.c1_depth_atr",
                      "valor": c1, "detalle": "C1 móvil escrito en 0 — no activa el "
                                              "cable C1-límite (posible colapso)."})
    return h


async def main() -> None:
    print("=" * 78)
    print("AUDITORÍA FX DE PALANCAS VIVAS (solo lectura) — FIX-FX-BACKSTOP")
    print("=" * 78)

    async with AsyncSessionLocal() as db:              # sesión de solo lectura
        smaps = (await db.execute(select(SymbolMap))).scalars().all()
        tick_por_tv = {s.tv_symbol: (float(s.tick_size)
                                     if s.tick_size is not None else None)
                       for s in smaps}
        strategies = (await db.execute(
            select(Strategy).order_by(Strategy.strategy_id))).scalars().all()
        profs = {p.strategy_id: p for p in (await db.execute(
            select(StrategyProfile))).scalars().all()}

    revisadas = 0
    criticos = avisos = 0
    for s in strategies:
        prof = profs.get(s.strategy_id)
        cfg = (prof.pipeline_config_json or {}) if prof else {}
        if not cfg:
            continue
        revisadas += 1
        tick = tick_por_tv.get(s.asset_symbol)
        if tick is None:
            tick = _tick_por_activo_raiz(s.asset_symbol)
        activo_fx = (s.asset_symbol in FX_INSTRUMENTS)
        hallazgos = _revisar(cfg, tick, activo_fx)
        estado = ("⛔ ENVENENADA" if any(x["nivel"] == "CRÍTICO" for x in hallazgos)
                  else ("⚠ revisar" if hallazgos else "✓ ok"))
        print(f"\n[{estado}] {s.strategy_id}  ({s.asset_symbol}, tick="
              f"{tick if tick is not None else '—'}, status={s.status})")
        bp = cfg.get("backstop_points")
        if bp is not None:
            print(f"    backstop_points vivo: {bp!r}")
        for x in hallazgos:
            if x["nivel"] == "CRÍTICO":
                criticos += 1
            elif x["nivel"] == "AVISO":
                avisos += 1
            print(f"      · {x['nivel']:8s} {x['campo']}={x['valor']!r} — {x['detalle']}")

    print("\n" + "=" * 78)
    print(f"RESUMEN: {revisadas} configs con palancas revisadas · "
          f"{criticos} CRÍTICO(s) · {avisos} aviso(s).")
    if criticos:
        print("ACCIÓN: re-aplica esas palancas (el fix ya no colapsa) o corrige a "
              "mano. El operador decide; este script NO escribe nada.")
    else:
        print("Ninguna palanca viva colapsada bajo el tick. Limpio.")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
