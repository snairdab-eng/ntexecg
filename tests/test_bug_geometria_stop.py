"""BUG-GEOMETRÍA STOP-vs-ESCALERA (parte 5) — aviso honesto cuando el backstop
queda MÁS SOMERO que una pierna (C2/C3): esa pierna opera más allá del stop
(huérfana R-RA6). GC 07-17: backstop −$3000 más somero que C3 −$4292. No se
prohíbe; el gate LX-11 lo suma como ÁMBAR y el preview lo muestra — jamás silencioso.
"""
import scripts.mr_luxy as mrl

S = mrl.stop_dentro_escalera


# ---------------------------------------------------------------------------
# 1) helper — geometría en ×ATR (backstop_pts/atr_med vs levels)
# ---------------------------------------------------------------------------

def test_c3_mas_profunda_que_stop_detecta():
    # b_pts 30 / atr_med 10 = 3.0×ATR; C3 a 4.0×ATR opera MÁS ALLÁ del stop
    r = S(30.0, [2.0, 4.0], 10.0)
    assert r["pierna"] == "C3" and r["pierna_atr"] == 4.0 and r["backstop_atr"] == 3.0


def test_stop_mas_profundo_que_escalera_sin_riesgo():
    assert S(50.0, [2.0, 4.0], 10.0) is None       # 5.0×ATR > todas las piernas


def test_c2_tambien_invertida_reporta_la_mas_profunda():
    # b 1.5×ATR: C2 (2.0) y C3 (4.0) ambas más allá → reporta la MÁS profunda (C3)
    r = S(15.0, [2.0, 4.0], 10.0)
    assert r["pierna"] == "C3"


def test_huerfana_pct_se_adjunta():
    r = S(30.0, [2.0, 4.0], 10.0, huerfana_pct=25.6)
    assert r["huerfana_pct"] == 25.6


def test_datos_faltantes_none():
    assert S(None, [2.0, 4.0], 10.0) is None
    assert S(30.0, [], 10.0) is None
    assert S(30.0, [2.0, 4.0], 0) is None


# ---------------------------------------------------------------------------
# 2) config_from_overrides adjunta la señal privada cuando el stop cae dentro
# ---------------------------------------------------------------------------

def test_config_marca_stop_dentro_escalera():
    # GC-like: sl 3000, C3 4292 (l3), ppt 100, atr_med 10 → b 3.0×ATR < C3 4.29×ATR
    o = {"sl_usd": 3000.0, "l2_usd": 2500.0, "l3_usd": 4292.0}
    cfg = mrl.config_from_overrides(o, 10.0, 100.0, [5, 3, 2], 3600)
    sde = cfg["_stop_dentro_escalera"]
    assert sde["pierna"] == "C3"


def test_config_stop_profundo_sin_marca():
    o = {"sl_usd": 6000.0, "l2_usd": 2500.0, "l3_usd": 4292.0}
    cfg = mrl.config_from_overrides(o, 10.0, 100.0, [5, 3, 2], 3600)
    assert "_stop_dentro_escalera" not in cfg


# ---------------------------------------------------------------------------
# 3) el gate LX-11 lo suma como ÁMBAR (no prohíbe; jamás silencioso)
# ---------------------------------------------------------------------------

def test_gate_amber_por_stop_dentro_escalera():
    apl = {"_stop_dentro_escalera": {"pierna": "C3", "pierna_atr": 4.29,
                                     "backstop_atr": 3.0, "huerfana_pct": 25.6}}
    g = mrl.gate_palancas({}, {}, None, aplicable=apl)
    assert g["nivel"] == "amber"
    assert any("DENTRO de la escalera" in t and "R-RA6" in t for t in g["triggers"])
