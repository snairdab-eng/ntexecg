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
