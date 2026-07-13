#!/usr/bin/env python3
"""sesiones_et — R-T7: partición ÚNICA de sesiones/zonas horarias ET (canónica).

FUENTE COMPARTIDA de las 6 zonas de Luxy (en tiempo de New York), a la
granularidad de la receta. El estudio Luxy construye `reco.zones`/`zones_partition`
de aquí y el front las renderiza tal cual (front == motor, no re-particiona).

Este módulo es la semántica CANÓNICA: extiende el `sesion_et` grueso del motor
(RTH/tarde/asia/europa, que vive en `scripts/nt_riesgo.py`) a estas 6 zonas con
su rango ET. Riesgo v1 conserva su `sesion_et` propio hasta su retiro (L7b); a
partir de este lote la fuente de las zonas de Luxy es una sola: este módulo.
"""
from __future__ import annotations

# (nombre, rango ET legible, horas ET que cubre). Cobertura total 0..23 sin
# solapes ni huecos — el invariante que verifica el test de R-T7.
LUXY_ZONES: list[tuple[str, str, list[int]]] = [
    ("Asia", "19:00–01:59 ET", [19, 20, 21, 22, 23, 0, 1]),
    ("Europa/Londres", "02:00–07:59 ET", [2, 3, 4, 5, 6, 7]),
    ("Apertura US", "08:00–09:59 ET", [8, 9]),
    ("NY media", "10:00–11:59 ET", [10, 11]),
    ("NY tarde", "12:00–15:59 ET", [12, 13, 14, 15]),
    ("Cierre US", "16:00–18:59 ET", [16, 17, 18]),
]

_DAY_ES: dict[int, str] = {0: "Lunes", 1: "Martes", 2: "Miércoles",
                           3: "Jueves", 4: "Viernes", 5: "Sábado",
                           6: "Domingo"}


def zone_of_hour(hr: int | None) -> str | None:
    """Zona de la partición ÚNICA (R-T7) para una hora ET. None si no mapea."""
    if hr is None:
        return None
    for name, _et, hours in LUXY_ZONES:
        if hr in hours:
            return name
    return None


# ---------------------------------------------------------------------------
# LX-8 — COMPILADOR puro: (zonas ON/OFF, días ON/OFF) → ventanas L2 MÍNIMAS.
# Determinista, sin efectos. `days` en convención %w (Dom=0..Sáb=6), la del
# store de la pestaña Ventanas y de SessionValidator. La hora `h` activa cubre
# [h:00, h:59]; un rango de horas contiguas → una ventana; los huecos → varias;
# el envolvente medianoche (Asia 19..01) → `next_day_end`. Todo-OFF → None.
# ---------------------------------------------------------------------------

def _horas_a_rangos(active: set[int]) -> list[tuple[int, int, bool]]:
    """Horas activas (0..23) → [(hora_inicio, hora_fin, cruza_medianoche)].
    24h → un solo rango 00..23 sin envolvente. Determinista (ordenado)."""
    if len(active) >= 24:
        return [(0, 23, False)]
    inicios = sorted(h for h in active if ((h - 1) % 24) not in active)
    rangos: list[tuple[int, int, bool]] = []
    for s in inicios:
        e = s
        while ((e + 1) % 24) in active and ((e + 1) % 24) != s:
            e = (e + 1) % 24
        rangos.append((s, e, e < s))          # e < s ⇒ el rango cruzó medianoche
    return rangos


def compilar_ventanas_l2(zonas_on: dict[str, bool],
                         dias_on_w: dict[int, bool]) -> list[dict] | None:
    """(zonas ON/OFF por nombre, días ON/OFF en %w) → ventanas L2 mínimas
    [{days, start, end, next_day_end}] o None si el resultado es vacío
    (todo-OFF → inválido, no se aplica). `days` se comparte entre todas las
    ventanas (los toggles de hora aplican a los días activos)."""
    active: set[int] = set()
    for name, _et, hours in LUXY_ZONES:
        if zonas_on.get(name, True):
            active.update(hours)
    dias = sorted(d for d, on in dias_on_w.items() if on)
    if not active or not dias:
        return None
    ventanas = []
    for h0, h1, wraps in _horas_a_rangos(active):
        w = {"days": dias, "start": f"{h0:02d}:00", "end": f"{h1:02d}:59"}
        if wraps:
            w["next_day_end"] = True
        ventanas.append(w)
    return ventanas
