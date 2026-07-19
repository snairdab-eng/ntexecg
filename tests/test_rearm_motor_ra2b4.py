"""RA-2b SUB-PASO 4 — motor de reglas R-RA9 (PURO, sobre estado + inferencia).

UNA regla por test con su acción exacta; conflictos de jerarquía (la de mayor
rango corta); timing sin solape §3 (TTL 3600 + guarda 120 = ciclo 3720 s =
62 min, el MISMO horizonte del modelo RA-1); atribución viva/ciega en los
bordes exactos; E1 antes que todo; R-RA8 en el borde de las 17:00 ET con
ZoneInfo. Toda acción lleva (regla, detalle) para el AuditLog del sub-paso 5.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.rearm import (
    REARM_CICLO_S,
    REARM_GUARDA_CIEGA_S,
    REARM_REQUIRED_TTL_S,
    atribuir_toque,
    decidir_pierna,
    normalize_rearm,
    toca_reenviar,
)

CFG = normalize_rearm({"enabled": True, "max_ciclos": 3, "k_sobre_c0": 1.0,
                       "umbral_atr": 1.5, "min_antes_cierre_min": 30})

# last_sent: 14:05 UTC = 10:05 ET (jul, EDT) — cruce de tz a propósito.
_SENT_UTC = "2026-07-14T14:05:00+00:00"
_SENT_ET = datetime(2026, 7, 14, 10, 5)
_NOW = datetime(2026, 7, 14, 12, 0)          # ET-naive; ciclo ya cumplido
POS = {"state": "LONG", "entry_price": 5000.0}


def _leg(**kw):
    leg = {"leg_index": 2, "side": "long", "level_atr": 1.0,
           "limit_price": 4992.0, "qty": 3, "cycle_n": 1,
           "last_client_id": None, "last_sent_at": _SENT_UTC,
           "state": "working", "death_reason": None}
    leg.update(kw)
    return leg


def _estado(**kw):
    e = {"legs": [_leg()], "signal_atr": 8.0, "sl_price": 4988.0,
         "tp_price": 5620.0, "updated_at": _SENT_UTC}
    e.update(kw)
    return e


def _bar(ts, hi=5005.0, lo=4995.0, close=5000.0):
    return {"time": ts, "open": 5000.0, "high": hi, "low": lo,
            "close": close, "volume": 10}


def _inf(bars=None, atr=8.0):
    bars = bars or [_bar(_SENT_ET + timedelta(minutes=5 * i))
                    for i in range(1, 4)]
    hi = max(b["high"] for b in bars)
    lo = min(b["low"] for b in bars)
    return {"tramo": bars, "extremos": (hi, lo), "atr_vivo": atr}


def _decide(leg=None, estado=None, pos=POS, inf="default", now=_NOW, cfg=CFG):
    return decidir_pierna(leg or _leg(), estado=estado or _estado(),
                          posicion=pos,
                          inferencia=_inf() if inf == "default" else inf,
                          cfg_rearm=cfg, now_et=now)


# ═══════════════════════════════════════════════════════════════════════════
# 1) Una regla por test — acción exacta
# ═══════════════════════════════════════════════════════════════════════════

def test_camino_feliz_reenviar():
    a = _decide()
    assert a["accion"] == "REENVIAR" and a["regla"] is None
    assert "ciclo 2 de 3" in a["detalle"]


def test_e1_ttl_incoherente_skip_antes_que_todo():
    a = _decide(estado=_estado(ttl_incoherente=True),
                pos={"state": "EXITING", "entry_price": 5000.0})
    assert a["accion"] == "SKIP" and a["regla"] == "E1"     # ni R-RA5 habla


def test_guard_pierna_no_working_sin_accion():
    a = _decide(leg=_leg(state="dead"))
    assert a["accion"] == "ESPERAR" and "sin acción" in a["detalle"]


@pytest.mark.parametrize("st", ["EXITING", "FLAT", "REVERSING", "UNKNOWN",
                                "LOCKED"])
def test_rra5_posicion_no_abierta_mata(st):
    a = _decide(pos={"state": st, "entry_price": 5000.0})
    assert a["accion"] == "MATAR" and a["regla"] == "R-RA5"
    assert st in a["detalle"]


def test_rra5_posicion_no_razonable_skip_fail_closed():
    for st in ("PENDING_LONG", None, "zombie"):
        a = _decide(pos={"state": st, "entry_price": 5000.0})
        assert a["accion"] == "SKIP" and a["regla"] == "R-RA5"


def test_rra6_backstop_tocado_mata_huerfana():
    inf = _inf([_bar(_SENT_ET + timedelta(minutes=5), lo=4988.0)])
    a = _decide(inf=inf)
    assert a["accion"] == "MATAR" and a["regla"] == "R-RA6"
    assert "backstop" in a["detalle"]


def test_rra6_tp_tocado_mata_huerfana():
    inf = _inf([_bar(_SENT_ET + timedelta(minutes=5), hi=5620.0)])
    a = _decide(inf=inf)
    assert a["accion"] == "MATAR" and a["regla"] == "R-RA6"
    assert "TP" in a["detalle"]


def test_rra6_lados_invertidos_short():
    # short: stop ARRIBA (max_high ≥ sl), TP ABAJO (min_low ≤ tp)
    est = _estado(sl_price=5010.0, tp_price=4985.0)
    leg = _leg(side="short", limit_price=5008.0)
    a = decidir_pierna(leg, estado=est,
                       posicion={"state": "SHORT", "entry_price": 5000.0},
                       inferencia=_inf([_bar(_SENT_ET + timedelta(minutes=5),
                                             hi=5010.0)]),
                       cfg_rearm=CFG, now_et=_NOW)
    assert a["accion"] == "MATAR" and a["regla"] == "R-RA6"


def test_rra1_feed_ciego_skip_no_mata():
    a = _decide(inf=None)
    assert a["accion"] == "SKIP" and a["regla"] == "R-RA1"


def test_rra1_atr_vivo_ilegible_skip():
    a = _decide(inf=_inf(atr=None))
    assert a["accion"] == "SKIP" and a["regla"] == "R-RA1"


def test_rra2_toque_con_orden_viva_assumed_filled():
    # toque a +25 min del envío (pos 1500 s < TTL) → orden VIVA → asumir fill
    toque = _SENT_ET + timedelta(minutes=25)
    inf = _inf([_bar(_SENT_ET + timedelta(minutes=5)),
                _bar(toque, lo=4990.0)])
    a = _decide(inf=inf)
    assert a["accion"] == "ASSUMED_FILLED" and a["regla"] == "R-RA2"
    assert "VIVA" in a["detalle"]


def test_rra2_toque_en_ventana_ciega_mata():
    # toque a +61 min (pos 3660 ∈ [3600, 3720)) → CIEGA → muerta honesta
    toque = _SENT_ET + timedelta(minutes=61)
    inf = _inf([_bar(_SENT_ET + timedelta(minutes=5)),
                _bar(toque, lo=4990.0)])
    a = _decide(inf=inf)
    assert a["accion"] == "MATAR" and a["regla"] == "R-RA2"
    assert "CIEGA" in a["detalle"]


def test_rra7_atr_expandido_mata():
    a = _decide(inf=_inf(atr=12.1))               # 12.1/8 = 1.51 > 1.5
    assert a["accion"] == "MATAR" and a["regla"] == "R-RA7"


def test_timing_antes_de_ttl_mas_guarda_espera():
    a = _decide(now=_SENT_ET + timedelta(seconds=REARM_CICLO_S - 1))
    assert a["accion"] == "ESPERAR" and a["regla"] == "timing"
    # y en el borde EXACTO (≥) el ciclo se cumple → sigue a las de re-envío
    a2 = _decide(now=_SENT_ET + timedelta(seconds=REARM_CICLO_S))
    assert a2["accion"] == "REENVIAR"


def test_rra3_precio_favorable_espera_este_ciclo():
    # long: close 5009 − entry 5000 = +9 ≥ 1.0×ATR(8) → ESPERAR
    inf = _inf([_bar(_SENT_ET + timedelta(minutes=5), hi=5009.0, close=5009.0)])
    a = _decide(inf=inf)
    assert a["accion"] == "ESPERAR" and a["regla"] == "R-RA3"
    # justo debajo del umbral → no dispara → REENVIAR
    inf2 = _inf([_bar(_SENT_ET + timedelta(minutes=5), hi=5007.9,
                      close=5007.9)])
    assert _decide(inf=inf2)["accion"] == "REENVIAR"


def test_rra3_short_favorable_es_hacia_abajo():
    inf = _inf([_bar(_SENT_ET + timedelta(minutes=5), lo=4991.0, close=4991.0)])
    leg = _leg(side="short", limit_price=5008.0)
    est = _estado(sl_price=5012.0, tp_price=4985.5)
    a = decidir_pierna(leg, estado=est,
                       posicion={"state": "SHORT", "entry_price": 5000.0},
                       inferencia=inf, cfg_rearm=CFG, now_et=_NOW)
    assert a["accion"] == "ESPERAR" and a["regla"] == "R-RA3"


def test_rra4_horizonte_agotado_mata():
    a = _decide(leg=_leg(cycle_n=3))
    assert a["accion"] == "MATAR" and a["regla"] == "R-RA4"
    # max_ciclos=1 = OFF efectivo: el primer re-envío debido ya está agotado
    cfg1 = normalize_rearm({"enabled": True, "max_ciclos": 1})
    assert _decide(cfg=cfg1)["accion"] == "MATAR"


def test_rra8_borde_de_las_17_et_con_zoneinfo():
    def _et(h, m):
        return (datetime(2026, 7, 14, h, m, tzinfo=timezone.utc)
                .astimezone(ZoneInfo("America/New_York"))
                .replace(tzinfo=None))
    # 20:31 UTC = 16:31 ET → 29 min < 30 → ESPERAR R-RA8 (jamás MATAR: si
    # llegamos aquí queda horizonte — R-RA4 va antes; ver decisión de diseño)
    a = _decide(now=_et(20, 31))
    assert a["accion"] == "ESPERAR" and a["regla"] == "R-RA8"
    # 20:30 UTC = 16:30 ET → exactamente 30 min (no < 30) → pasa → REENVIAR
    assert _decide(now=_et(20, 30))["accion"] == "REENVIAR"


# ═══════════════════════════════════════════════════════════════════════════
# 2) Conflictos de jerarquía — dos reglas armadas ⇒ gana la de mayor rango
# ═══════════════════════════════════════════════════════════════════════════

def test_conflicto_rra5_gana_a_rra6():
    inf = _inf([_bar(_SENT_ET + timedelta(minutes=5), lo=4988.0)])  # stop tocado
    a = _decide(pos={"state": "EXITING", "entry_price": 5000.0}, inf=inf)
    assert a["regla"] == "R-RA5" and a["accion"] == "MATAR"


def test_conflicto_rra6_gana_a_rra2():
    # stop tocado Y nivel tocado en la misma barra → huérfana (R-RA6) manda
    inf = _inf([_bar(_SENT_ET + timedelta(minutes=5), lo=4988.0)])
    a = _decide(inf=inf)
    assert a["regla"] == "R-RA6"


def test_conflicto_rra1_gana_a_rra4_jamas_matar_a_ciegas():
    a = _decide(leg=_leg(cycle_n=3), inf=None)
    assert a["accion"] == "SKIP" and a["regla"] == "R-RA1"


def test_conflicto_rra2_gana_a_rra7():
    toque = _SENT_ET + timedelta(minutes=25)
    inf = _inf([_bar(toque, lo=4990.0)], atr=12.1)      # tocado + expandido
    a = _decide(inf=inf)
    assert a["regla"] == "R-RA2" and a["accion"] == "ASSUMED_FILLED"


def test_conflicto_rra7_gana_a_rra4():
    a = _decide(leg=_leg(cycle_n=3), inf=_inf(atr=12.1))
    assert a["regla"] == "R-RA7" and a["accion"] == "MATAR"


def test_conflicto_e1_gana_a_todas():
    inf = _inf([_bar(_SENT_ET + timedelta(minutes=5), lo=4988.0)])
    a = _decide(estado=_estado(ttl_incoherente=True), inf=inf,
                pos={"state": "FLAT", "entry_price": None})
    assert a["regla"] == "E1" and a["accion"] == "SKIP"


# ═══════════════════════════════════════════════════════════════════════════
# 3) Helpers de timing — bordes exactos
# ═══════════════════════════════════════════════════════════════════════════

def test_toca_reenviar_bordes_y_tz_mixta():
    assert REARM_CICLO_S == REARM_REQUIRED_TTL_S + REARM_GUARDA_CIEGA_S == 3720
    assert toca_reenviar(_SENT_UTC,
                         _SENT_ET + timedelta(seconds=3719)) is False
    assert toca_reenviar(_SENT_UTC,
                         _SENT_ET + timedelta(seconds=3720)) is True
    # datetime aware UTC como now también normaliza
    now_utc = datetime(2026, 7, 14, 15, 7, 0, tzinfo=timezone.utc)  # 11:07 ET
    assert toca_reenviar(_SENT_UTC, now_utc) is True


def test_atribuir_toque_bordes_exactos_de_la_ventana():
    t = lambda s: _SENT_ET + timedelta(seconds=s)
    assert atribuir_toque(t(0), _SENT_UTC) == "viva"
    assert atribuir_toque(t(3599), _SENT_UTC) == "viva"
    assert atribuir_toque(t(3600), _SENT_UTC) == "ciega"    # [TTL, ciclo)
    assert atribuir_toque(t(3719), _SENT_UTC) == "ciega"
    assert atribuir_toque(t(3720), _SENT_UTC) == "viva"     # ciclo siguiente
    # toque ANTES del último envío (ciclo previo): −100 s ≡ 3620 → ciega
    assert atribuir_toque(t(-100), _SENT_UTC) == "ciega"
    assert atribuir_toque(t(-3720), _SENT_UTC) == "viva"    # inicio ciclo previo


# ═══════════════════════════════════════════════════════════════════════════
# 4) Contrato de la acción — (regla, detalle) siempre presentes (AuditLog)
# ═══════════════════════════════════════════════════════════════════════════

def test_toda_accion_lleva_regla_y_detalle():
    casos = [
        _decide(),
        _decide(inf=None),
        _decide(pos={"state": "FLAT", "entry_price": None}),
        _decide(leg=_leg(cycle_n=3)),
        _decide(estado=_estado(ttl_incoherente=True)),
        _decide(now=_SENT_ET + timedelta(seconds=100)),
    ]
    for a in casos:
        assert set(a) == {"accion", "regla", "detalle"}
        assert a["accion"] in ("REENVIAR", "ESPERAR", "MATAR", "SKIP",
                               "ASSUMED_FILLED")
        assert isinstance(a["detalle"], str) and a["detalle"]
