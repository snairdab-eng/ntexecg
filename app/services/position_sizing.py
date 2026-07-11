"""Sizing de posición — helpers COMPARTIDOS, sin dependencias de Luxy.

Los reusan: el estudio Luxy (reparto de la escalera), el panel de Perfiles (L4)
y la regla 3 del Módulo de Riesgo de Portafolio (P-B: `worst_case_loss` — el
peor-caso agregado en $). Todo en unidades explícitas (puntos / $-por-micro),
sin acoplarse al dominio de ningún módulo.
"""
from __future__ import annotations

MICROS_PER_MINI = 10                # 1 mini = 10 micros → PV_micro = PV / 10


def _largest_remainder(raw: list[float], target: int,
                       floor_c1: bool = True) -> list[int]:
    """Reparte `target` enteros ∝ `raw` por MAYOR RESIDUO. Si `floor_c1`, C1
    (la entrada, índice 0) nunca queda en 0 cuando hay tamaño."""
    out = [int(x) for x in raw]
    rem = target - sum(out)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in range(max(0, rem)):
        out[order[i % len(order)]] += 1
    if floor_c1 and out and out[0] == 0 and target > 0:
        out[0] = 1
        j = max(range(1, len(out)), key=lambda i: out[i]) if len(out) > 1 else 0
        if j:
            out[j] -= 1
    return out


def alloc_from(weights: list[float], total: int = MICROS_PER_MINI) -> list[int]:
    """Reparte `total` micros ∝ pesos, redondeo por MAYOR RESIDUO, C1≥1.
    (El reparto derivado por frecuencia de pullback del estudio Luxy.)"""
    s = sum(weights) or 1.0
    raw = [w / s * total for w in weights]
    return _largest_remainder(raw, total)


def scale_alloc(alloc: list[int], size_scale: float,
                max_contracts: int | None = None) -> list[int]:
    """Re-escala un reparto de micros a `size_scale` del total (mayor residuo,
    C1≥1), opcionalmente topado a `max_contracts`. Portado de
    LUXY/luxy_engine.scale_alloc (misma semántica)."""
    total = sum(alloc)
    if total == 0:
        return [0 for _ in alloc] or [0, 0, 0]
    target = max(1, round(total * size_scale))
    if max_contracts:
        target = min(target, max_contracts)
    raw = [a / total * target for a in alloc]
    return _largest_remainder(raw, target)


def worst_case_loss(sl: float, levels: list[float], alloc: list[int],
                    pv_micro: float) -> float:
    """Peor-caso por operación en $: todas las piernas llenan y la posición para
    en el SL. `sl` y `levels` en PUNTOS (misma unidad); una pierna a profundidad
    L pierde (SL − L) puntos. loss = Σ q·(SL − L)·PV_micro.

    Sin dependencias de Luxy — lo consume la regla 3 del Portafolio (P-B) para
    el peor-caso agregado. Una pierna más profunda que el SL no llena antes de
    parar → aporta 0 (se recorta, nunca negativo)."""
    lv = list(levels) + [0.0] * (len(alloc) - len(levels))
    return round(sum(q * max(0.0, sl - L) * pv_micro
                     for q, L in zip(alloc, lv)), 2)


def size_for_caps(base_alloc: list[int], *, sl: float | None,
                  levels: list[float], pv_micro: float,
                  max_contracts: int | None = None,
                  max_loss_per_trade: float | None = None) -> dict:
    """Aplica los caps de la cuenta al reparto base (lógica de
    luxy_engine.build_profile): primero topa el nº de micros a `max_contracts`;
    luego, si `max_loss_per_trade` se excede con el peor-caso, BAJA el tamaño
    (mayor residuo) hasta cumplir. Devuelve {alloc, total, worst_case,
    limited_by}."""
    alloc = list(base_alloc)
    limited = None
    if max_contracts and sum(alloc) > max_contracts:
        alloc = scale_alloc(alloc, 1.0, max_contracts)
        limited = "max_contracts"
    if max_loss_per_trade and sl:
        guard = 0
        while (sum(alloc) > 1
               and worst_case_loss(sl, levels, alloc, pv_micro)
               > max_loss_per_trade and guard < 10000):
            guard += 1
            alloc = scale_alloc(alloc, (sum(alloc) - 1) / sum(alloc),
                                max_contracts)
            limited = "max_loss_per_trade"
    wc = (worst_case_loss(sl, levels, alloc, pv_micro)
          if sl is not None else None)
    return {"alloc": alloc, "total": sum(alloc), "worst_case": wc,
            "limited_by": limited}


def micros_that_fit(sl: float, pv_micro: float,
                    max_loss_per_trade: float | None) -> int | None:
    """Insight: cuántos micros aguanta una cuenta con tope `max_loss_per_trade`
    dado el SL (peor-caso de 1 micro a la entrada = SL·PV_micro). None si falta
    dato."""
    if not (sl and pv_micro and max_loss_per_trade):
        return None
    per_micro = sl * pv_micro
    return int(max_loss_per_trade // per_micro) if per_micro > 0 else None
