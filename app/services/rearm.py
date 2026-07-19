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


# ---------------------------------------------------------------------------
# RA-2b sub-paso 3 — INFERENCIA DE PRECIO (P3 del diseño). Funciones PURAS:
# reciben barras YA obtenidas + estado y JUZGAN; el único no-puro es el
# wrapper `obtener_inferencia` (get_bars/get_atr y nada más). HUECO/ilegible
# ⇒ None ⇒ el job (sub-paso 5) no re-arma (REARM_SKIP{feed_hueco}).
#
# CONVENCIÓN DE TIEMPO: las barras del bridge son ET-naive wall-clock (LX-6,
# app/services/bar_store.py — parse_bar_time es la fuente única del parseo);
# `opened_at`/`now` llegan UTC-aware (risk_plan_json["opened_at"]) y aquí se
# normalizan a ET-naive (aware → America/New_York → naive; naive = ya ET).
# ---------------------------------------------------------------------------

# LA TRAMPA DEL CONTEO (decisión, 2026-07-19): "barras esperadas en
# [opened_at, now]" ingenuo marcaría como HUECO los gaps LEGÍTIMOS del
# calendario CME Globex — mantenimiento diario 17:00–18:00 ET (lun–jue) y fin
# de semana (vie 17:00 → dom 18:00) — y una posición que cruza el break
# quedaría fail-closed PARA SIEMPRE por un falso hueco. DECISIÓN: conteo por
# CALENDARIO DE SESIÓN (mercado_abierto_et, predicado puro nuevo): solo se
# esperan barras en slots con mercado abierto. `sesion_et` del proyecto es
# una PARTICIÓN DE DISPLAY (RTH/tarde/asia/europa), no un calendario de
# apertura — no sirve de fuente aquí; el predicado queda al lado de los
# puros del re-armado con esta justificación. FERIADOS CME: no modelados →
# un feriado produce "hueco" y el job NO re-arma ese día (fail-closed
# honesto; el costo es no re-armar en feriado — lado seguro de la asimetría,
# jamás pasa un hueco REAL como legítimo).
REARM_TOLERANCIA_BARRAS = 1     # bordes de rejilla/barra en formación — nombrada


def mercado_abierto_et(ts) -> bool:
    """¿El slot de barra (ET-naive, inicio de barra) cae con CME Globex
    ABIERTO? dom 18:00 → vie 17:00, con break diario 17:00–18:00 ET
    (lun–jue). sáb cerrado. Predicado PURO del calendario base (sin
    feriados — ver la decisión del conteo arriba)."""
    wd, h = ts.weekday(), ts.hour
    if wd == 5:                              # sábado
        return False
    if wd == 6:                              # domingo: abre 18:00 ET
        return h >= 18
    if wd == 4:                              # viernes: cierra 17:00 ET
        return h < 17
    return not (17 <= h < 18)                # lun–jue: break 17:00–18:00


def _a_et_naive(dt):
    """UTC/aware → ET-naive (la convención de las barras); naive = ya ET."""
    from datetime import datetime
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt
    from zoneinfo import ZoneInfo
    return dt.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)


def _tf_segundos(timeframe) -> int | None:
    try:
        s = str(timeframe).strip().lower()
        if s.endswith("m"):
            return int(s[:-1]) * 60
        if s.endswith("h"):
            return int(s[:-1]) * 3600
    except (TypeError, ValueError):
        pass
    return None


def barras_esperadas(inicio_et, fin_et, tf_s: int) -> int:
    """Nº de slots de la rejilla `tf_s` en (inicio, fin] con mercado ABIERTO
    (calendario CME base). Rejilla anclada a la hora en punto."""
    from datetime import datetime, timedelta
    paso = timedelta(seconds=tf_s)
    # primer slot de rejilla ESTRICTAMENTE posterior al inicio
    base = inicio_et.replace(minute=0, second=0, microsecond=0)
    t = base
    while t <= inicio_et:
        t += paso
    n = 0
    while t <= fin_et:
        if mercado_abierto_et(t):
            n += 1
        t += paso
    return n


def tramo_valido(bars, *, opened_at, timeframe, now,
                 heartbeat_max_age: float) -> list[dict] | None:
    """Barras del tramo [opened_at, now] VALIDADAS, o None (hueco/ilegible ⇒
    fail-closed). Los TRES chequeos del diseño P3:
      (i)  conteo: reales < esperadas (calendario CME) − tolerancia ⇒ hueco.
      (ii) high/low None/ausente (o time no parseable) en el tramo ⇒ ilegible.
      (iii) frescura: la barra más nueva más vieja que tf + heartbeat_max_age
            respecto de `now` ⇒ feed frío. NOTA: con el mercado CERRADO
            (break/finde) este chequeo falla naturalmente ⇒ no se infiere con
            el mercado cerrado — correcto (tampoco hay fills posibles); el
            job vuelve a inferir minutos después de la reapertura, y el
            CONTEO por calendario garantiza que el cruce del break jamás lo
            deja fail-closed para siempre."""
    from app.services.bar_store import parse_bar_time
    tf_s = _tf_segundos(timeframe)
    if tf_s is None or bars is None:
        return None
    try:
        t0 = _a_et_naive(opened_at)
        t1 = _a_et_naive(now)
    except (TypeError, ValueError):
        return None
    tramo = []
    for b in bars:
        if not isinstance(b, dict):
            return None
        ts = parse_bar_time(b.get("time"))
        if ts is None:
            return None                       # (ii) time ilegible
        if ts < t0 or ts > t1:
            continue
        if b.get("high") is None or b.get("low") is None:
            return None                       # (ii) OHLC mutilado en el tramo
        tramo.append({**b, "time": ts})
    if not tramo:
        return None
    tramo.sort(key=lambda b: b["time"])
    # (iii) frescura de la barra más nueva (una barra puede estar formándose:
    # su sello es el INICIO → tolerancia = un intervalo completo + heartbeat)
    edad_s = (t1 - tramo[-1]["time"]).total_seconds()
    if edad_s > tf_s + float(heartbeat_max_age):
        return None
    # (i) conteo por calendario de sesión (ver LA TRAMPA DEL CONTEO)
    esperadas = barras_esperadas(t0, t1, tf_s)
    if len(tramo) < esperadas - REARM_TOLERANCIA_BARRAS:
        return None
    return tramo


def extremos(tramo: list[dict]) -> tuple[float, float]:
    """(max_high, min_low) del tramo YA validado por `tramo_valido`."""
    return (max(float(b["high"]) for b in tramo),
            min(float(b["low"]) for b in tramo))


# CONVENCIÓN DE TOQUE (documentada): INCLUSIVE — el extremo que ALCANZA
# exactamente el nivel cuenta como tocado. Un toque exacto del límite puede
# haber llenado o no en el broker; para el re-armado se asume TOCADO porque
# R-RA2 dice "nivel tocado ⇒ jamás re-enviar": en el peor caso se deja de
# re-armar una pierna que no llenó (lado seguro de la asimetría) — lo
# contrario (re-enviar sobre un posible fill) arriesga posición doble.

def nivel_tocado(side: str, limit_price: float,
                 ext: tuple[float, float]) -> bool:
    """R-RA2 — ¿el precio alcanzó el nivel de la pierna? (long: pierna BAJO
    la entrada → min_low ≤ limit; short: pierna SOBRE la entrada →
    max_high ≥ limit). Toque exacto = tocado (inclusive)."""
    max_high, min_low = ext
    return (min_low <= limit_price) if side == "long" \
        else (max_high >= limit_price)


def backstop_tocado(side: str, sl_price, ext: tuple[float, float]) -> bool:
    """R-RA6 — ¿el backstop fue alcanzado? (long: stop ABAJO → min_low ≤ sl;
    short: stop ARRIBA → max_high ≥ sl). sl None ⇒ False (sin dato no se
    infiere muerte por stop — el job ya exigió estado legible con sl)."""
    if sl_price is None:
        return False
    max_high, min_low = ext
    return (min_low <= float(sl_price)) if side == "long" \
        else (max_high >= float(sl_price))


def tp_tocado(side: str, tp_price, ext: tuple[float, float]) -> bool:
    """R-RA6 — ¿el TP fue alcanzado? (long: TP ARRIBA → max_high ≥ tp;
    short: TP ABAJO → min_low ≤ tp). tp None ⇒ False (sin TP no hay
    muerte por TP)."""
    if tp_price is None:
        return False
    max_high, min_low = ext
    return (max_high >= float(tp_price)) if side == "long" \
        else (min_low <= float(tp_price))


def atr_expandido(atr_vivo, signal_atr, umbral) -> bool | None:
    """R-RA7 — ¿el régimen se expandió? atr_vivo/signal_atr > umbral ⇒ True
    (no re-armar). Datos ausentes/no positivos ⇒ None (ilegible → el caller
    trata None como fail-closed, no como 'no expandido')."""
    if not _num(atr_vivo) or not _num(signal_atr) or not _num(umbral):
        return None
    if atr_vivo <= 0 or signal_atr <= 0:
        return None
    return (atr_vivo / signal_atr) > umbral


# ---------------------------------------------------------------------------
# RA-2b sub-paso 4 — MOTOR DE REGLAS R-RA9 (PURO: sin DB, sin scheduler, sin
# despacho; consume el estado del sub-paso 2 y la inferencia del sub-paso 3).
# `decidir_pierna` devuelve UNA acción {accion, regla, detalle} lista para el
# AuditLog del sub-paso 5. Jerarquía estricta — la primera que dispara, corta.
#
# DECISIONES DE DISEÑO (justificadas):
# · El TIMING (toca_reenviar) se evalúa DESPUÉS de las muertes/toques
#   (R-RA5/6, E1, R-RA1, R-RA2, R-RA7) y ANTES de las reglas de re-envío
#   (R-RA3/4/8): una huérfana con el stop tocado se mata YA, no al minuto 62;
#   y R-RA4 (agotado) solo puede juzgarse cuando un re-envío ESTÁ debido —
#   matar la pierna antes de que expire su orden viva perdería el rastreo que
#   R-RA2 necesita (la orden del ciclo actual sigue viva hasta su cancelAfter).
# · ASSUMED_FILLED es la 5ª acción (además de REENVIAR/ESPERAR/MATAR/SKIP):
#   R-RA2 con toque en ventana viva exige la transición del sub-paso 2
#   (marcar_assumed_filled) — semánticamente distinta de MATAR (bloquea el
#   re-envío Y cuenta para exposición, E2; jamás toca la posición).
# · R-RA8 ⇒ ESPERAR (no MATAR): si llegamos a R-RA8 es que aún queda
#   horizonte de ciclos (R-RA4 va antes en la jerarquía); matar por reloj
#   quemaría horizonte del estudio por un artefacto del cierre — la pierna
#   podrá re-armarse tras la reapertura, y la orden viva ya tiene su
#   cancelAfter. Con el mercado ya cerrado la inferencia cae en R-RA1 antes.
# ---------------------------------------------------------------------------

# §3 — ciclo SIN SOLAPE: re-envío SOLO cuando now ≥ last_sent + TTL + GUARDA
# (minuto 61-62). GUARDA=120 s ⇒ ciclo = 3720 s = 62 min — EXACTAMENTE el
# REARM_CYCLE_MIN=62 del modelo RA-1 del estudio (coherencia de horizonte).
REARM_GUARDA_CIEGA_S = 120
REARM_CICLO_S = REARM_REQUIRED_TTL_S + REARM_GUARDA_CIEGA_S    # 3720

# Estados de posición que MATAN los re-armados (diseño §4, R-RA5). LONG/SHORT
# continúan; cualquier otro (PENDING_*, None, basura) ⇒ SKIP fail-closed (un
# tránsito no destruye estado, pero tampoco se re-arma sin posición razonable).
_POS_MATAR = ("EXITING", "FLAT", "REVERSING", "UNKNOWN", "LOCKED")
_POS_ABIERTA = ("LONG", "SHORT")


def _acc(accion: str, regla, detalle: str) -> dict:
    """Acción única del motor — (regla, detalle) viajan al AuditLog tal cual."""
    return {"accion": accion, "regla": regla, "detalle": detalle}


def toca_reenviar(last_sent_at, now) -> bool:
    """§3 — ¿ya toca re-enviar? SOLO cuando now ≥ last_sent + TTL + GUARDA
    (el cancelAfter ejecutó con CERTEZA; jamás dos órdenes vivas al mismo
    precio). Antes de eso el caller devuelve ESPERAR sin evaluar reglas de
    re-envío. Acepta ISO/datetime en cualquier tz (se normaliza a ET)."""
    delta = (_a_et_naive(now) - _a_et_naive(last_sent_at)).total_seconds()
    return delta >= REARM_CICLO_S


def atribuir_toque(t_toque, last_sent_at) -> str:
    """R-RA2, doble lectura — ¿el toque cayó con orden VIVA o en la ventana
    CIEGA? Aritmética modular de ciclos (los envíos van cada REARM_CICLO_S):
    pos = (t_toque − last_sent) mod ciclo; pos < TTL ⇒ "viva" (la orden
    trabajaba → ASUMIR FILL), pos ∈ [TTL, ciclo) ⇒ "ciega" (fill perdido
    honesto → pierna muerta). Funciona también para toques de ciclos PREVIOS
    (delta negativo: el mod lo lleva a su posición dentro de su ciclo).
    Granularidad de barra: t_toque = INICIO de la primera barra que toca —
    una barra que abre en viva se atribuye viva (lado assumed_filled, el
    conservador: cuenta exposición y bloquea re-envío)."""
    delta = (_a_et_naive(t_toque) - _a_et_naive(last_sent_at)).total_seconds()
    pos = delta % REARM_CICLO_S
    return "viva" if pos < REARM_REQUIRED_TTL_S else "ciega"


def _mins_a_cierre_et(now_et) -> float:
    """Minutos hasta las 17:00 ET del día de `now_et` (negativo si ya pasó)."""
    return (17 * 60) - (now_et.hour * 60 + now_et.minute
                        + now_et.second / 60.0)


def decidir_pierna(leg: dict, *, estado: dict, posicion: dict,
                   inferencia: dict | None, cfg_rearm: dict,
                   now_et) -> dict:
    """LA decisión del motor para UNA pierna (PURA) → {accion, regla, detalle}
    con accion ∈ {REENVIAR, ESPERAR, MATAR, SKIP, ASSUMED_FILLED}.

    `posicion` = {"state", "entry_price"} (lo arma el job desde
    PositionState). `estado` = el bloque rearm VALIDADO (leer_estado).
    `inferencia` = dict de obtener_inferencia o None. `cfg_rearm` = config
    normalizada (normalize_rearm). `now_et` = datetime ET-naive.

    Jerarquía R-RA9 (primera que dispara, corta):
      E1 → guard pierna → R-RA5 → R-RA6 → R-RA1 → R-RA2 (doble lectura) →
      R-RA7 → timing §3 → R-RA3 → R-RA4 → R-RA8 → REENVIAR."""
    # E1 — TTL incoherente REGISTRADO en la siembra ⇒ nada que razonar.
    if estado.get("ttl_incoherente"):
        return _acc("SKIP", "E1", "ttl_incoherente — el ciclo real no mapea "
                                  "al horizonte del estudio (corrige el TTL)")
    # guard (fuera de jerarquía): una pierna no-working no tiene acción.
    if leg.get("state") != "working":
        return _acc("ESPERAR", None,
                    f"pierna '{leg.get('state')}' — sin acción")
    # R-RA5 — la posición manda: cerrada/saliendo/incierta ⇒ matar re-armados.
    st = (posicion or {}).get("state")
    if st in _POS_MATAR:
        return _acc("MATAR", "R-RA5", f"posición {st} — re-armados mueren")
    if st not in _POS_ABIERTA:
        return _acc("SKIP", "R-RA5",
                    f"posición no razonable ({st!r}) — fail-closed sin matar")
    side = leg["side"]
    # R-RA6 — backstop/TP tocados ⇒ huérfana: matar YA (no espera al reloj).
    if inferencia is not None:
        ext = inferencia["extremos"]
        if backstop_tocado(side, estado.get("sl_price"), ext):
            return _acc("MATAR", "R-RA6",
                        f"backstop {estado.get('sl_price')} tocado — "
                        f"pierna huérfana post-stop")
        if tp_tocado(side, estado.get("tp_price"), ext):
            return _acc("MATAR", "R-RA6",
                        f"TP {estado.get('tp_price')} tocado — huérfana")
    # R-RA1 — feed ciego/hueco (o ATR vivo ilegible) ⇒ SKIP, jamás matar a ciegas.
    expandido = (atr_expandido(inferencia.get("atr_vivo"),
                               estado.get("signal_atr"),
                               cfg_rearm.get("umbral_atr"))
                 if inferencia is not None else None)
    if inferencia is None or expandido is None:
        return _acc("SKIP", "R-RA1",
                    "feed ciego/hueco o ATR ilegible — no se infiere, "
                    "no se re-arma")
    # R-RA2 — nivel tocado: DOBLE LECTURA por atribución del toque (una sola
    # lectura por toque: el PRIMER toque del tramo decide).
    if nivel_tocado(side, leg["limit_price"], inferencia["extremos"]):
        t_toque = next(
            (b["time"] for b in inferencia["tramo"]
             if (float(b["low"]) <= leg["limit_price"] if side == "long"
                 else float(b["high"]) >= leg["limit_price"])), None)
        ventana = (atribuir_toque(t_toque, leg["last_sent_at"])
                   if t_toque is not None else "viva")
        if ventana == "viva":
            return _acc("ASSUMED_FILLED", "R-RA2",
                        f"nivel {leg['limit_price']} tocado con orden VIVA "
                        f"(ciclo {leg['cycle_n']}) — asumir fill, jamás "
                        f"re-enviar (E2: la posición no se toca)")
        return _acc("MATAR", "R-RA2",
                    f"nivel {leg['limit_price']} tocado en ventana CIEGA — "
                    f"fill perdido honesto, pierna muerta")
    # R-RA7 — régimen expandido (None ya cayó en R-RA1).
    if expandido:
        return _acc("MATAR", "R-RA7",
                    f"ATR vivo {inferencia['atr_vivo']} / señal "
                    f"{estado['signal_atr']} > {cfg_rearm['umbral_atr']} — "
                    f"régimen expandido")
    # §3 — timing sin solape: antes de TTL+guarda no hay NADA que re-enviar.
    if not toca_reenviar(leg["last_sent_at"], now_et):
        return _acc("ESPERAR", "timing",
                    f"ciclo {leg['cycle_n']} vivo — re-envío al cumplirse "
                    f"TTL {REARM_REQUIRED_TTL_S}s + guarda "
                    f"{REARM_GUARDA_CIEGA_S}s")
    # R-RA3 — precio k×ATR favorable a C0 ⇒ pullback improbable ESTE ciclo.
    entry = (posicion or {}).get("entry_price")
    ultimo = inferencia["tramo"][-1].get("close")
    if entry is None or ultimo is None:
        return _acc("SKIP", "R-RA3",
                    "sin entry_price/close para la excursión — fail-closed")
    favorable = (float(ultimo) - float(entry)) if side == "long" \
        else (float(entry) - float(ultimo))
    if favorable >= cfg_rearm["k_sobre_c0"] * estado["signal_atr"]:
        return _acc("ESPERAR", "R-RA3",
                    f"precio {favorable:+.2f} pts favorable ≥ "
                    f"{cfg_rearm['k_sobre_c0']}×ATR señal — pullback "
                    f"improbable este ciclo")
    # R-RA4 — horizonte agotado (solo juzgable con el re-envío debido).
    if leg["cycle_n"] >= cfg_rearm["max_ciclos"]:
        return _acc("MATAR", "R-RA4",
                    f"agotado: ciclo {leg['cycle_n']} ≥ max_ciclos "
                    f"{cfg_rearm['max_ciclos']}")
    # R-RA8 — cerca del cierre: ESPERAR (ver decisión de diseño arriba).
    mins = _mins_a_cierre_et(now_et)
    if mins < cfg_rearm["min_antes_cierre_min"]:
        return _acc("ESPERAR", "R-RA8",
                    f"{mins:.0f} min a las 17:00 ET < "
                    f"{cfg_rearm['min_antes_cierre_min']} — no se re-arma "
                    f"pegado al cierre (la pierna sigue; tras la reapertura "
                    f"se re-evalúa)")
    return _acc("REENVIAR", None,
                f"ciclo {leg['cycle_n'] + 1} de {cfg_rearm['max_ciclos']} — "
                f"misma orden límite {leg['limit_price']}")


async def obtener_inferencia(market_data, symbol: str, *, opened_at,
                             timeframe: str, now=None) -> dict | None:
    """Wrapper NO-puro MÍNIMO (P3): obtiene barras y ATR vivo de
    MarketDataService y DELEGA todo el juicio a los puros. None ⇒ fail-closed
    (el job registrará REARM_SKIP{feed_hueco}). `heartbeat_max_age` = el
    MISMO NTBRIDGE_HEARTBEAT_MAX_AGE de L1.6 (P2 — fuente única, jamás un
    umbral propio). `now` inyectable solo para tests deterministas."""
    from datetime import datetime, timezone

    from app.core.config import settings

    if now is None:
        now = datetime.now(timezone.utc)
    tf_s = _tf_segundos(timeframe)
    if tf_s is None:
        return None
    try:
        t0 = _a_et_naive(opened_at)
        t1 = _a_et_naive(now)
    except (TypeError, ValueError):
        return None
    limite = barras_esperadas(t0, t1, tf_s) + 100        # margen de borde
    bars = await market_data.get_bars(symbol, timeframe, limit=limite)
    tramo = tramo_valido(
        bars, opened_at=opened_at, timeframe=timeframe, now=now,
        heartbeat_max_age=getattr(settings, "NTBRIDGE_HEARTBEAT_MAX_AGE", 60))
    if tramo is None:
        return None
    atr_vivo = await market_data.get_atr(symbol, timeframe)
    return {"tramo": tramo, "extremos": extremos(tramo),
            "atr_vivo": atr_vivo}
