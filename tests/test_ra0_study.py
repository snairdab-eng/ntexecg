"""RA-0v2 — sección FIJA "Piernas / Re-armado" del estudio Luxy (por estrategia).

Prueba los helpers PUROS (curva de llegada, ventana ciega, R-RA3 graduada, orden
de eventos, tabla de cortes, recomendación con n/s honesto) y, gated, la sección
real sobre ES (HOLC alineado 07-14 si está disponible).
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ra0_study import (arrival_stats, blind_window_pct, graduated_prob,
                               ladder_cuts, order_of_events, recomendar)


# ---------------------------------------------------------------------------
# 1) Curva de llegada
# ---------------------------------------------------------------------------

def test_arrival_stats_conocido():
    # 5 trades: toques a 10, 50, 90, 150 min y uno que nunca toca
    a = arrival_stats([10.0, 50.0, 90.0, 150.0, None])
    assert a["n"] == 5 and a["n_touched"] == 4
    assert a["mediana_min"] == 70.0                 # mediana de [10,50,90,150]
    assert a["pct_le_1h"] == 40.0                   # 10,50 (2/5)
    assert a["pct_le_2h"] == 60.0                   # +90 (3/5)
    assert a["pct_le_3h"] == 80.0                   # +150 (4/5, ≤180 min)
    assert a["pct_nunca"] == 20.0


def test_arrival_stats_vacio():
    a = arrival_stats([None, None])
    assert a["n_touched"] == 0 and a["mediana_min"] is None and a["pct_nunca"] == 100.0


# ---------------------------------------------------------------------------
# 2) Ventana ciega (ciclos 62m, vive 60m)
# ---------------------------------------------------------------------------

def test_blind_window():
    # 61 y 123 caen en la franja ciega [60,62) de su ciclo; 30 y 100 no.
    b = blind_window_pct([30.0, 61.0, 100.0, 123.0])
    assert b["n_touched"] == 4 and b["pct_en_ciega"] == 50.0


# ---------------------------------------------------------------------------
# 3) R-RA3 graduada
# ---------------------------------------------------------------------------

def test_graduated_prob():
    # 3 trades sin toque hasta t=60 y ≥1×ATR favorable: 1 toca luego, 2 no.
    feats = [
        {"c2_min": 90.0, "fav_at": {60.0: 1.5}},    # sin toque en 60, toca luego
        {"c2_min": None, "fav_at": {60.0: 1.2}},    # nunca toca
        {"c2_min": None, "fav_at": {60.0: 1.0}},    # nunca toca
        {"c2_min": 30.0, "fav_at": {60.0: 0.2}},    # ya tocó / no cumple k
    ]
    g = graduated_prob(feats, "c2_min", 60.0, 1.0)
    assert g["n_cond"] == 3 and g["p_toque_luego_pct"] == pytest.approx(33.3, abs=0.1)


# ---------------------------------------------------------------------------
# 4) Orden de eventos (R-RA6)
# ---------------------------------------------------------------------------

def test_order_of_events():
    feats = [
        {"c2_min": 100.0, "c3_min": 150.0, "bk_min": 40.0, "tp_min": None},  # bk<c2,c3 → huérf ambas
        {"c2_min": 30.0, "c3_min": 200.0, "bk_min": None, "tp_min": 90.0},   # tp<c3 → huérf c3
        {"c2_min": None, "c3_min": None, "bk_min": 20.0, "tp_min": None},    # sin piernas
    ]
    o = order_of_events(feats)
    assert o["n"] == 3
    assert o["pct_c2_huerfana"] == pytest.approx(33.3, abs=0.1)   # 1/3
    assert o["pct_c3_huerfana"] == pytest.approx(66.7, abs=0.1)   # 2/3


# ---------------------------------------------------------------------------
# 5) Tabla de cortes — fill tardío cambia el neto entre 1h y duración
# ---------------------------------------------------------------------------

def _t(number, pnl, side="long", entry=5000.0, atr=10.0, mae_atr=4.0, mfe_atr=1.0):
    return SimpleNamespace(number=number, side=side, in_sample=True,
                           entry_price=entry, atr_entry=atr,
                           mae_pct=mae_atr * atr / entry * 100.0,
                           mfe_pct=mfe_atr * atr / entry * 100.0, pnl_usd=pnl)


def test_ladder_cuts_fill_tardio_marginal():
    c2 = 3.0
    # A: toca C2 temprano (30m); B: toca C2 tarde (150m). mae_atr(4.0) ≥ c2(3).
    trades = [_t(1, 100.0, mae_atr=4.0), _t(2, -60.0, mae_atr=4.0)]
    feats = [{"c2_min": 30.0, "c3_min": None}, {"c2_min": 150.0, "c3_min": None}]
    cuts = ladder_cuts(trades, feats, c2, 6.0, [1, 1, 0], b_pts := None,
                       {"long": 8.0}, 50.0)
    # a 1h solo A llena su C2; a duración ambos → net distinto (el tardío entra)
    assert cuts["1h"]["net_usd"] != cuts["duracion"]["net_usd"]
    assert set(cuts) == {"1h", "2h", "3h", "duracion"}


# ---------------------------------------------------------------------------
# 6) Recomendación — muestra floja → n/s + default conservador (jamás sin muestra)
# ---------------------------------------------------------------------------

def test_recomendar_ns_default():
    section = {
        "arrival_c3": {"n_touched": 3, "p90_min": 400.0},          # < N_MIN → n/s
        "graduada": {(60.0, 0.0): {"n_cond": 2, "p_toque_luego_pct": 5.0},
                     (60.0, 0.5): {"n_cond": 1, "p_toque_luego_pct": 0.0},
                     (60.0, 1.0): {"n_cond": 1, "p_toque_luego_pct": 0.0}},
        "atr_exp_c3": {"perdedores": {"n": 2, "atr_ratio_p90": 2.0}},
    }
    r = recomendar(section)
    assert r["MAX_CICLOS"] == 1 and "n/s" in r["MAX_CICLOS_evidencia"]
    assert r["K_SOBRE_C0"] == 1.0 and "n/s" in r["K_SOBRE_C0_evidencia"]
    assert r["UMBRAL_ATR_EXPANSION"] == 1.5 and "n/s" in r["UMBRAL_ATR_EXPANSION_evidencia"]


def test_recomendar_con_muestra():
    section = {
        "arrival_c3": {"n_touched": 30, "p90_min": 124.0},          # ⌈124/62⌉=2
        "graduada": {(60.0, 0.0): {"n_cond": 40, "p_toque_luego_pct": 30.0},
                     (60.0, 0.5): {"n_cond": 30, "p_toque_luego_pct": 12.0},
                     (60.0, 1.0): {"n_cond": 25, "p_toque_luego_pct": 6.0}},
        "atr_exp_c3": {"perdedores": {"n": 15, "atr_ratio_p90": 1.8}},
    }
    r = recomendar(section)
    assert r["MAX_CICLOS"] == 2
    assert r["K_SOBRE_C0"] == 0.5                # primera celda con P≤15% y muestra
    assert r["UMBRAL_ATR_EXPANSION"] == 1.8


# ---------------------------------------------------------------------------
# 7) Gated ES real — sección presente + determinista (si HOLC alineado disponible)
# ---------------------------------------------------------------------------

_ALIGNED = Path("_ntbridge_0714/ES_5m.csv")
_ES_MASTER = Path("ListaDeOperaciones/LO130726/ES5m_ConfNormal_TC_TSR_130726.csv")


@pytest.mark.skipif(not (_ALIGNED.exists() and _ES_MASTER.exists()),
                    reason="HOLC alineado 07-14 no disponible (descargar de /mnt/ntbridge)")
def test_es_real_seccion_coherente():
    from scripts.ra0_study import ACTIVOS, run_activo
    r = run_activo("ES", ACTIVOS["ES"])
    assert r["contencion_pct"] >= 80.0                 # ES alineado
    a = r["arrival_c2"]
    assert a["mediana_min"] is not None and 0 < a["mediana_min"] < 3000
    assert set(r["cortes"]) == {"1h", "2h", "3h", "duracion"}
    assert "MAX_CICLOS" in r["recomendacion"]
    # determinista
    r2 = run_activo("ES", ACTIVOS["ES"])
    assert r["arrival_c2"] == r2["arrival_c2"] and r["cortes"] == r2["cortes"]


# ---------------------------------------------------------------------------
# 8) Integración en el estudio Luxy — sección presente + HONESTA + digest intacto
#    Reusa el master sintético ALINEADO de LX-12 (integrar real → run_for_clave).
# ---------------------------------------------------------------------------

from tests.test_contencion_lx12 import _integrar   # noqa: E402  (fixture compartido)

_SECCION_KEYS = {"arrival_c2", "arrival_c3", "cortes", "graduada_flat",
                 "orden_eventos", "recomendacion", "ciega_c2", "ciega_c3",
                 "atr_exp_c2", "atr_exp_c3", "n", "n_min_celda"}


def test_piernas_en_estudio_alineado_y_honesta(tmp_path, monkeypatch):
    """Con el master ALINEADO la sección 'Piernas' está en dashboard['piernas'],
    completa, y su recomendación es HONESTA: muestra chica → n/s + default
    conservador (jamás una constante sin muestra)."""
    import scripts.mr_luxy as mrl
    _integrar(tmp_path, monkeypatch, shift=0.0)
    study = mrl.run_for_clave("ES_Test", tmp_path / "MotorRiesgo")
    assert study["degradado"] is False
    p = study["dashboard"]["piernas"]
    assert _SECCION_KEYS <= set(p)
    assert len(p["graduada_flat"]) == 9                    # 3 t × 3 k
    assert set(p["cortes"]) == {"1h", "2h", "3h", "duracion"}
    # honestidad: con 20 trades triviales la muestra por celda es floja → default
    r = p["recomendacion"]
    assert r["MAX_CICLOS"] == 1 and "n/s" in r["MAX_CICLOS_evidencia"]
    assert r["UMBRAL_ATR_EXPANSION"] == 1.5 and "n/s" in r["UMBRAL_ATR_EXPANSION_evidencia"]


def test_piernas_degrada_con_el_estudio(tmp_path, monkeypatch):
    """CRITERIO 3 — si el estudio degrada (contención/intrabar) NO hay dashboard,
    luego NO hay sección 'Piernas': degrada CON el estudio, no en paralelo."""
    import scripts.mr_luxy as mrl
    _integrar(tmp_path, monkeypatch, shift=5.0)            # +20 ticks → no confiable
    study = mrl.run_for_clave("ES_Test", tmp_path / "MotorRiesgo")
    assert study["degradado"] is True
    assert study["dashboard"] is None                      # sin dashboard → sin piernas


def test_piernas_no_engorda_el_digest(tmp_path, monkeypatch):
    """CRITERIO 4 — el detalle 'Piernas' NO entra al digest de la flota
    (resumen_flota.json): la lista de Estrategias no lo carga por fila."""
    import scripts.mr_luxy as mrl
    _integrar(tmp_path, monkeypatch, shift=0.0)
    study = mrl.run_for_clave("ES_Test", tmp_path / "MotorRiesgo")
    resumen = mrl.study_resumen(study)
    assert "piernas" in study["dashboard"]                 # sí en el estudio completo
    assert "piernas" not in resumen                        # NO en el digest
    # y el JSON del digest en disco tampoco lo trae
    import json
    dg = (tmp_path / "MotorRiesgo" / "ES_Test" / "runs" / "resumen_flota.json")
    assert "piernas" not in json.loads(dg.read_text(encoding="utf-8"))
