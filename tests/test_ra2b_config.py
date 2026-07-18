"""RA-2b sub-paso 1 — config del re-armado: normalización, escritura SOLO vía
Aplicar (jamás nace sola), gate (ON=ámbar; E1 ttl_incoherente=rojo), y coherencia
del TTL. Sin job todavía.
"""
import app.web.routes_riesgo as rr
import scripts.mr_luxy as mrl
from app.services.rearm import (REARM_DEFAULTS, REARM_REQUIRED_TTL_S,
                                normalize_rearm, rearm_config, rearm_enabled,
                                ttl_coherente)


# ---------------------------------------------------------------------------
# 1) normalize_rearm — AUSENTE=OFF, enabled solo si True, defaults conservadores
# ---------------------------------------------------------------------------

def test_ausente_es_off():
    assert normalize_rearm(None) is None
    assert normalize_rearm("x") is None                 # no-dict → OFF


def test_enabled_solo_si_true_explicito():
    assert normalize_rearm({})["enabled"] is False       # sin enabled → OFF
    assert normalize_rearm({"enabled": 1})["enabled"] is False   # truthy ≠ True
    assert normalize_rearm({"enabled": True})["enabled"] is True


def test_defaults_conservadores():
    r = normalize_rearm({"enabled": True})
    assert r["max_ciclos"] == REARM_DEFAULTS["max_ciclos"] == 1
    assert r["k_sobre_c0"] == 1.0 and r["umbral_atr"] == 1.5
    assert r["min_antes_cierre_min"] == 30 and r["timeframe"] == "5m"


def test_siembra_del_veredicto_y_clamps():
    r = normalize_rearm({"enabled": True, "max_ciclos": 3, "k_sobre_c0": 0.8,
                         "umbral_atr": 1.2, "min_antes_cierre_min": 45,
                         "timeframe": "15m"})
    assert (r["max_ciclos"], r["k_sobre_c0"], r["umbral_atr"]) == (3, 0.8, 1.2)
    assert r["min_antes_cierre_min"] == 45 and r["timeframe"] == "15m"
    # inválidos → default; max_ciclos nunca < 1
    bad = normalize_rearm({"enabled": True, "max_ciclos": 0, "umbral_atr": "x"})
    assert bad["max_ciclos"] == 1 and bad["umbral_atr"] == 1.5


# ---------------------------------------------------------------------------
# 2) config_from_overrides — escribe rearm en scale_entry SOLO vía Aplicar
# ---------------------------------------------------------------------------

def _base(**extra):
    o = {"sl_usd": 4500.0, "l2_usd": 5000.0, "l3_usd": 8000.0}
    o.update(extra)
    return o


def test_config_sin_rearm_no_escribe_llave():
    cfg = mrl.config_from_overrides(_base(), 8.0, 50.0, [5, 3, 2], 3600)
    assert "rearm" not in cfg["scale_entry"]              # jamás nace sola


def test_config_con_rearm_escribe_en_scale_entry():
    cfg = mrl.config_from_overrides(
        _base(rearm={"enabled": True, "max_ciclos": 3}),
        8.0, 50.0, [5, 3, 2], 3600)
    r = cfg["scale_entry"]["rearm"]
    assert r["enabled"] is True and r["max_ciclos"] == 3


def test_config_rearm_sin_escalera_no_escribe():
    # sin C2/C3 ni C1 móvil no hay scale_entry → no hay dónde colgar rearm
    cfg = mrl.config_from_overrides(
        {"sl_usd": 4500.0, "rearm": {"enabled": True}},
        8.0, 50.0, [10, 0, 0], 3600)
    assert "scale_entry" not in cfg


# ---------------------------------------------------------------------------
# 3) merge — rearm persiste dentro de scale_entry al aplicar (NX-11 intacto)
# ---------------------------------------------------------------------------

def test_merge_preserva_rearm_y_mode_vivo():
    act = mrl.config_from_overrides(
        _base(rearm={"enabled": True, "max_ciclos": 2}),
        8.0, 50.0, [5, 3, 2], 3600)
    vivo = {"scale_entry": {"mode": "execute", "stop_mode": "x"}}
    cfg = rr._merge_activacion(vivo, act)
    assert cfg["scale_entry"]["rearm"]["max_ciclos"] == 2
    assert cfg["scale_entry"]["mode"] == "execute"        # NX-11 preserva el mode vivo


# ---------------------------------------------------------------------------
# 4) rearm_enabled / ttl_coherente — E1
# ---------------------------------------------------------------------------

def test_rearm_enabled_ausente_off():
    assert rearm_enabled({}) is False
    assert rearm_enabled({"scale_entry": {}}) is False
    assert rearm_enabled({"scale_entry": {"rearm": {"enabled": True}}}) is True


def test_ttl_coherente_e1():
    off = {"scale_entry": {"rearm": {"enabled": False}}}
    assert ttl_coherente(off) == (True, None)             # OFF no aplica
    on_ok = {"scale_entry": {"rearm": {"enabled": True}},
             "entry_reserve_timeout_seconds": REARM_REQUIRED_TTL_S}
    assert ttl_coherente(on_ok) == (True, None)
    on_bad = {"scale_entry": {"rearm": {"enabled": True}},
              "entry_reserve_timeout_seconds": 1800}
    assert ttl_coherente(on_bad) == (False, "ttl_incoherente")
    on_missing = {"scale_entry": {"rearm": {"enabled": True}}}
    assert ttl_coherente(on_missing) == (False, "ttl_incoherente")


# ---------------------------------------------------------------------------
# 5) gate — rearm ON = ámbar; E1 (TTL≠3600) = rojo
# ---------------------------------------------------------------------------

def _apl(enabled, ttl=3600):
    return {"scale_entry": {"mode": "execute",
                            "rearm": {"enabled": enabled, "max_ciclos": 2}},
            "entry_reserve_timeout_seconds": ttl}


def test_gate_verde_sin_rearm():
    g = mrl.gate_palancas({}, {}, None, aplicable=_apl(False))
    assert g["nivel"] == "verde"


def test_gate_amber_con_rearm_on():
    g = mrl.gate_palancas({}, {}, _apl(True)["scale_entry"], aplicable=_apl(True))
    assert g["nivel"] == "amber"
    assert any("re-armado" in t for t in g["triggers"])


def test_gate_rojo_ttl_incoherente_e1():
    apl = _apl(True, ttl=1800)
    g = mrl.gate_palancas({}, {}, apl["scale_entry"], aplicable=apl)
    assert g["nivel"] == "rojo"
    assert any("ttl_incoherente" in t for t in g["triggers"])
