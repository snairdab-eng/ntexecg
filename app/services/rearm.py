"""RA-2b — configuración del re-armado de piernas (`scale_entry.rearm`).

Módulo PURO (constantes + normalización + guardas de coherencia), SIN dependencias
de DB ni del feed. Lo comparten el camino de Aplicar (`scripts.mr_luxy`) y —más
adelante— el `RearmJob`. El re-armado NACE OFF: sin `enabled=true` explícito nada
corre (default global: nadie re-arma hasta que el operador lo aplique por
estrategia tras observar en demo).

Ver CONTRATO/RA2b_RearmJob_Diseno_2026-07-17.md (§1, E1, P2).
"""
from __future__ import annotations

# Defaults CONSERVADORES (P2 — fuente única de cada constante del re-armado).
# max_ciclos default 1 = OFF efectivo hasta que el veredicto RA-0v3 lo suba.
REARM_DEFAULTS: dict = {
    "max_ciclos": 1,             # R-RA4 — tope de ciclos (sembrado del veredicto)
    "k_sobre_c0": 1.0,           # R-RA3 — precio k×ATR favorable a C0 → no re-arma
    "umbral_atr": 1.5,           # R-RA7 — ATR vivo/ATR señal > umbral → no re-arma
    "min_antes_cierre_min": 30,  # R-RA8 — a <X min de 17:00 ET → no re-arma
    "timeframe": "5m",           # serie del feed para la inferencia de precio (P3)
}

# E1 — el ciclo SIN SOLAPE y el horizonte `max_ciclos` se calibraron con un TTL de
# 3600 s (RA-1 REARM_CYCLE_MIN=62 sobre el cancelAfter de 60 min). Con rearm ON el
# TTL DEBE ser exactamente esto o el ciclo real deja de mapear al horizonte del
# estudio. Fuente única del requisito.
REARM_REQUIRED_TTL_S = 3600


def normalize_rearm(raw) -> dict | None:
    """Normaliza el bloque `rearm` (del operador vía Aplicar) a una config
    canónica, o None si AUSENTE (⇒ OFF). `enabled` es True SOLO si viene True
    explícito — jamás nace sola. Constantes faltantes/ inválidas → default
    conservador (P2)."""
    if not isinstance(raw, dict):
        return None

    def _num(key, cast):
        v = raw.get(key)
        try:
            return cast(v) if v is not None else REARM_DEFAULTS[key]
        except (TypeError, ValueError):
            return REARM_DEFAULTS[key]

    tf = raw.get("timeframe")
    return {
        "enabled": raw.get("enabled") is True,
        "max_ciclos": max(1, _num("max_ciclos", int)),
        "k_sobre_c0": _num("k_sobre_c0", float),
        "umbral_atr": _num("umbral_atr", float),
        "min_antes_cierre_min": max(0, _num("min_antes_cierre_min", int)),
        "timeframe": (tf if isinstance(tf, str) and tf
                      else REARM_DEFAULTS["timeframe"]),
    }


def rearm_config(config: dict | None) -> dict:
    """El bloque `rearm` de la config efectiva (dentro de scale_entry), o {}."""
    se = (config or {}).get("scale_entry") or {}
    return se.get("rearm") or {}


def rearm_enabled(config: dict | None) -> bool:
    """¿El re-armado está ON en esta config efectiva? AUSENTE ⇒ OFF (fail-closed)."""
    return rearm_config(config).get("enabled") is True


def ttl_coherente(config: dict | None) -> tuple[bool, str | None]:
    """E1 — con rearm ON, el TTL (`entry_reserve_timeout_seconds` = cancelAfter)
    DEBE ser REARM_REQUIRED_TTL_S. Si no, (False, 'ttl_incoherente'). Con rearm
    OFF siempre coherente (no aplica)."""
    if not rearm_enabled(config):
        return True, None
    ttl = (config or {}).get("entry_reserve_timeout_seconds")
    try:
        ok = ttl is not None and int(ttl) == REARM_REQUIRED_TTL_S
    except (TypeError, ValueError):
        ok = False
    return (True, None) if ok else (False, "ttl_incoherente")
