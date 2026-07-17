"""RA-1 — tercer modo de fills: corte por RE-ARMADO (solo estudio, CERO despacho).

leg_filled/ladder_outcome ganan el modo re-armado (tope MAX_CICLOS×62m, ventana
ciega [60,62) descontada). La tabla de cortes gana la columna comparativa
re-armado[MAX_CICLOS]. R-T1: el default del estudio SIGUE siendo corte 1h.
"""
import json
from types import SimpleNamespace

from scripts.mr_sims import (REARM_CYCLE_MIN, REARM_LIVE_MIN, HaircutCfg,
                             SimTrade, ladder_outcome, leg_filled, metrics_usd)
from scripts.ra0_study import ladder_cut_rearmado, ladder_cuts


def _st(touch, mae_atr=4.0, atr=10.0):
    return SimTrade(number=1, side="long", in_sample=True, entry_price=5000.0,
                    atr_pts=atr, mae_pts=mae_atr * atr, mfe_pts=atr,
                    native_pnl_usd=0.0,
                    pb_touch_min=(None if touch is None else {"1.0": touch}))


# ---------------------------------------------------------------------------
# 1) leg_filled — 3 modos en casos conocidos
# ---------------------------------------------------------------------------

def test_tres_modos_toque_temprano():
    st = _st(30.0)                        # toca a 30m
    assert leg_filled(st, 1.0)[0] is True                       # sin corte (techo)
    assert leg_filled(st, 1.0, cancel_after_s=3600.0)[0] is True   # corte 1h
    assert leg_filled(st, 1.0, rearm_ciclos=1)[0] is True          # re-armado 1


def test_rearmado_tope_max_ciclos():
    st = _st(70.0)                        # 70m = ciclo 2 (minuto 8)
    assert leg_filled(st, 1.0, cancel_after_s=3600.0)[0] is False  # 1h: 70>60
    assert leg_filled(st, 1.0, rearm_ciclos=1)[0] is False         # >62 → no re-arma
    assert leg_filled(st, 1.0, rearm_ciclos=2)[0] is True          # ≤124, minuto 8 vivo
    assert leg_filled(st, 1.0)[0] is True                          # sin corte sí


def test_ventana_ciega_descontada():
    st = _st(61.0)                        # 61m ∈ [60,62) de su ciclo → ciega
    assert leg_filled(st, 1.0)[0] is True                          # sin corte: sí
    assert leg_filled(st, 1.0, cancel_after_s=3600.0)[0] is False  # 1h: no
    # re-armado: aunque 61 ≤ N×62, cae en ventana ciega → fill PERDIDO honesto
    for n in (1, 2, 3):
        assert leg_filled(st, 1.0, rearm_ciclos=n)[0] is False


def test_nunca_toco_y_sin_tiempos():
    assert leg_filled(_st(30.0, mae_atr=0.5), 1.0, rearm_ciclos=3)[0] is False  # MAE<depth
    aprox = leg_filled(_st(None), 1.0, rearm_ciclos=1)                          # sin t_pb
    assert aprox == (True, True)                                                # MAE aprox


def test_constantes_ciclo():
    assert (REARM_CYCLE_MIN, REARM_LIVE_MIN) == (62.0, 60.0)


# ---------------------------------------------------------------------------
# 2) Monotonía: re-armado(N) ⊆ sin-corte ; re-armado sube con N (fuera de ciega)
# ---------------------------------------------------------------------------

def test_rearmado_subconjunto_de_sin_corte():
    # toques repartidos en varios ciclos (evitando la franja ciega)
    touches = [10.0, 40.0, 70.0, 130.0, 200.0]
    sts = [_st(t) for t in touches]
    sin = sum(1 for s in sts if leg_filled(s, 1.0)[0])
    for n in (1, 2, 3, 4):
        con = sum(1 for s in sts if leg_filled(s, 1.0, rearm_ciclos=n)[0])
        assert con <= sin, f"re-armado({n}) {con} > sin-corte {sin}"
    # más ciclos ⇒ ≥ fills (monótono creciente)
    seq = [sum(1 for s in sts if leg_filled(s, 1.0, rearm_ciclos=n)[0])
           for n in (1, 2, 3, 4)]
    assert seq == sorted(seq)


# ---------------------------------------------------------------------------
# 3) ladder_outcome — re-armado entre corte 1h y sin-corte
# ---------------------------------------------------------------------------

def test_ladder_outcome_rearmado_intermedio():
    legs = ((0.0, 0.5), (1.0, 0.5))       # C1 mercado + C2 a 1.0×ATR
    hc = HaircutCfg()
    st = _st(70.0)                        # C2 toca a 70m
    o = lambda **kw: ladder_outcome(st, legs, None, {"long": 8.0}, 50.0, hc, **kw)[1]
    w_1h = o(cancel_after_s=3600.0)       # peso llenado con corte 1h (solo C1)
    w_re2 = o(rearm_ciclos=2)             # re-armado 2 ciclos (C1+C2)
    w_sin = o()                           # sin corte (C1+C2)
    assert w_1h < w_re2 == w_sin          # el re-armado recupera C2 como el techo


# ---------------------------------------------------------------------------
# 4) Tabla de cortes — columna re-armado (ra0_study), casos conocidos
# ---------------------------------------------------------------------------

def _t(number, pnl, mae_atr=4.0):
    return SimpleNamespace(number=number, side="long", in_sample=True,
                           entry_price=5000.0, atr_entry=10.0,
                           mae_pct=mae_atr * 10.0 / 5000.0 * 100.0,
                           mfe_pct=1.0 * 10.0 / 5000.0 * 100.0, pnl_usd=pnl)


def test_ladder_cut_rearmado_vs_1h_y_sin_corte():
    c2 = 3.0
    # A toca C2 a 30m (todos los modos), B a 70m (1h/rearm1 NO, rearm2/sin-corte SÍ)
    trades = [_t(1, 100.0), _t(2, -60.0)]
    feats = [{"c2_min": 30.0, "c3_min": None}, {"c2_min": 70.0, "c3_min": None}]
    base = ladder_cuts(trades, feats, c2, 6.0, [1, 1, 0], None, {"long": 8.0}, 50.0)
    re1 = ladder_cut_rearmado(trades, feats, c2, 6.0, [1, 1, 0], None,
                              {"long": 8.0}, 50.0, 1)
    re2 = ladder_cut_rearmado(trades, feats, c2, 6.0, [1, 1, 0], None,
                              {"long": 8.0}, 50.0, 2)
    # re-armado 1 ciclo ≈ corte 1h (B's C2 fuera); re-armado 2 ≈ sin-corte (B dentro)
    assert re1["net_usd"] == base["1h"]["net_usd"]
    assert re2["net_usd"] == base["duracion"]["net_usd"]
    assert re1["net_usd"] != re2["net_usd"]        # el fill tardío de B cambia el neto


# ---------------------------------------------------------------------------
# 5) R-T1 — NO-DEFAULT: el re-armado es columna; el default aplicable sigue 1h
# ---------------------------------------------------------------------------

def test_no_default_rearmado_no_toca_config_aplicable():
    from scripts.mr_luxy import activacion_from_study
    study = {"cancel_after_s": 3600, "levers_in_sample": {
        "backstop_usd": 4500.0, "b_pts": 90.0,
        "ladder": {"alloc": [5, 3, 2], "levels": [0.0, 1.64, 3.28]}}}
    act = activacion_from_study(study)
    # el default de despacho vigente = corte 1h (cancel_after → reserve timeout)
    assert act["entry_reserve_timeout_seconds"] == 3600
    # NINGUNA llave de re-armado entra en la config que se aplicaría a producción
    assert "rearm" not in json.dumps(act).lower()
    assert "c1_depth_atr" not in json.dumps(act)   # LX-15: tampoco C1 móvil por defecto
