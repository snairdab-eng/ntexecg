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


# ---------------------------------------------------------------------------
# RA-2b sub-paso 2 — ESTADO DEL CICLO PERSISTENTE (diseño §2 + E1/E2).
# Capa PURA: sembrado desde los payloads despachados + lectura/validación
# fail-closed + transicionadores. El RearmJob (sub-paso 5) NO existe aún; el
# único escritor vivo es la siembra al despachar (webhooks_luxalgo) vía
# PositionService.set_rearm_state (que SOLO toca risk_plan_json["rearm"]).
# ---------------------------------------------------------------------------

LEG_STATES = ("working", "dead", "assumed_filled")

# Shape del diseño §2 — llave faltante ⇒ ilegible (fail-closed §2.3).
_LEG_KEYS = ("leg_index", "side", "level_atr", "limit_price", "qty",
             "cycle_n", "last_client_id", "last_sent_at", "state",
             "death_reason")
_TOP_KEYS = ("legs", "signal_atr", "sl_price", "tp_price", "updated_at")


def sembrar_estado(payloads: list[dict] | None, *, side: str,
                   now_iso: str, ttl_ok: bool) -> dict | None:
    """Estado INICIAL del ciclo (diseño §2) desde los payloads del destino
    PRIMARIO tal como se despacharon — los MISMOS números que viajaron al
    broker, no una reconstrucción. PURO (sin DB/reloj propio).

    Solo las piernas LÍMITE son re-armables (C1 a mercado llena al instante);
    sin ninguna pierna límite → None (nada que sembrar). `last_client_id`
    nace None (el envío inicial no lleva client id propio; los re-envíos del
    job usarán "<base>-r{n}", diseño §5). E1: `ttl_incoherente` se REGISTRA
    en el estado cuando el TTL efectivo ≠ 3600 — el job lo leerá como
    no-re-armar (defensa en profundidad; el gate ya lo puso en rojo)."""
    legs: list[dict] = []
    signal_atr = sl_price = tp_price = None
    for p in payloads or []:
        if not isinstance(p, dict):
            continue
        ex = p.get("extras") or {}
        if signal_atr is None and ex.get("atr_value") is not None:
            signal_atr = float(ex["atr_value"])          # congelado (R-RA7)
        if sl_price is None and isinstance(p.get("stopLoss"), dict):
            sl_price = p["stopLoss"].get("stopPrice")    # R-RA6
        if tp_price is None and isinstance(p.get("takeProfit"), dict):
            tp_price = p["takeProfit"].get("limitPrice")  # R-RA6
        if p.get("orderType") != "limit":
            continue                                     # C1 mercado: no re-armable
        legs.append({
            "leg_index": int(ex.get("leg_index") or 0),
            "side": side,
            "level_atr": float(ex.get("level_atr") or 0.0),
            "limit_price": p.get("limitPrice"),
            "qty": int(p.get("quantity") or 0),
            "cycle_n": 1,
            "last_client_id": None,
            "last_sent_at": now_iso,
            "state": "working",
            "death_reason": None,
        })
    if not legs:
        return None
    estado = {
        "legs": legs,
        "signal_atr": signal_atr,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "updated_at": now_iso,
    }
    if not ttl_ok:
        estado["ttl_incoherente"] = True                 # E1
    return estado


def _ts_ok(v) -> bool:
    from datetime import datetime
    if not isinstance(v, str) or not v:
        return False
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)   # True NO es un 1


def _leg_valida(leg) -> bool:
    if not isinstance(leg, dict) or any(k not in leg for k in _LEG_KEYS):
        return False
    if not _int(leg["leg_index"]) or leg["leg_index"] < 1:
        return False
    if leg["side"] not in ("long", "short"):
        return False
    if not _num(leg["level_atr"]) or leg["level_atr"] < 0:
        return False
    if not _num(leg["limit_price"]) or leg["limit_price"] <= 0:
        return False
    if not _int(leg["qty"]) or leg["qty"] < 1:
        return False
    if not _int(leg["cycle_n"]) or leg["cycle_n"] < 1:
        return False
    if leg["state"] not in LEG_STATES:
        return False
    if not _ts_ok(leg["last_sent_at"]):
        return False
    if leg["last_client_id"] is not None and not isinstance(
            leg["last_client_id"], str):
        return False
    if leg["death_reason"] is not None and not isinstance(
            leg["death_reason"], str):
        return False
    return True


def leer_estado(risk_plan_json) -> dict | None:
    """Estado del ciclo VALIDADO desde `risk_plan_json["rearm"]`, o None si
    ILEGIBLE — jamás excepción, jamás estado parcial (fail-closed §2.3: una
    orden que no podemos razonar NO se re-envía; el job registrará
    REARM_SKIP{estado_ilegible}). Devuelve una COPIA profunda (mutar el
    resultado nunca toca el JSON de la fila)."""
    import copy
    try:
        if not isinstance(risk_plan_json, dict):
            return None
        estado = risk_plan_json.get("rearm")
        if not isinstance(estado, dict):
            return None
        if any(k not in estado for k in _TOP_KEYS):
            return None
        legs = estado["legs"]
        if not isinstance(legs, list) or not legs:
            return None
        if not all(_leg_valida(leg) for leg in legs):
            return None
        if not _num(estado["signal_atr"]) or estado["signal_atr"] <= 0:
            return None
        if not _num(estado["sl_price"]) or estado["sl_price"] <= 0:
            return None
        if estado["tp_price"] is not None and not _num(estado["tp_price"]):
            return None
        if not _ts_ok(estado["updated_at"]):
            return None
        if "ttl_incoherente" in estado and not isinstance(
                estado["ttl_incoherente"], bool):
            return None
        return copy.deepcopy(estado)
    except Exception:
        return None                        # fail-closed: ilegible, no revienta


# ── Transicionadores PUROS (devuelven COPIA; jamás mutan la pierna dada) ──

def marcar_muerta(leg: dict, razon: str) -> dict:
    """Pierna → dead con la regla que la mató (R-RA*). Copia pura."""
    out = dict(leg)
    out["state"] = "dead"
    out["death_reason"] = str(razon)
    return out


def marcar_assumed_filled(leg: dict) -> dict:
    """E2 — pierna → assumed_filled: su ÚNICO efecto es bloquear el re-envío
    de ESTA pierna. JAMÁS toca la posición (state/direction/quantity son de
    position_service) — invariante con test explícito. Copia pura."""
    out = dict(leg)
    out["state"] = "assumed_filled"
    out["death_reason"] = None
    return out


def avanzar_ciclo(leg: dict, client_id: str, ts_iso: str) -> dict:
    """Re-envío: cycle_n+1 con el client id correlacionado y su timestamp.
    SOLO desde 'working' — avanzar una pierna muerta/assumed es un bug del
    job, no un estado: revienta (el job jamás debe llegar aquí con esas)."""
    if leg.get("state") != "working":
        raise ValueError(
            f"avanzar_ciclo sobre pierna '{leg.get('state')}' — solo working")
    out = dict(leg)
    out["cycle_n"] = int(leg["cycle_n"]) + 1
    out["last_client_id"] = str(client_id)
    out["last_sent_at"] = ts_iso
    return out
