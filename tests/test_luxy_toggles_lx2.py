"""LX-2 — toggles por sesión/día (parte no gated).

Candados sin datos reales:
  - `activacion_from_study` (lo que se aplica desde Luxy) IGNORA cualquier
    toggle: aplicar-desde-Luxy con sesiones/días apagados NO escribe nada de
    sesiones/días en la config (criterio 5 — no persiste, no entra en Aplicar);
  - `evaluate_overrides` con toggles apagados excluye por zona canónica / día,
    reusando el MISMO `zone_of_hour` de `sesiones_et` (R-T7, una sola fuente).
"""
from datetime import datetime
from types import SimpleNamespace

import scripts.mr_luxy as mrl
from scripts.sesiones_et import LUXY_ZONES, zone_of_hour


def test_entry_hour_et_usa_offset_no_hora_cruda():
    """Los toggles clasifican por hora ET (fuente única). Un trade sin enriquecer
    (hour None) NO debe caer en la hora cruda del CSV: el fallback recompone la
    ET con el MISMO offset del enriched. Caso donde la zona CAMBIA con offset."""
    # entrada cruda 01:30; offset +120min → ET 03:30 → Europa/Londres, no Asia.
    tr = SimpleNamespace(hour=None, entry_ts=datetime(2026, 3, 16, 1, 30))
    assert mrl._entry_hour_et(tr, 120) == 3
    assert zone_of_hour(mrl._entry_hour_et(tr, 120)) == "Europa/Londres"
    assert zone_of_hour(tr.entry_ts.hour) == "Asia"       # la cruda clasificaría mal
    # si el enriched ya dejó la hora ET, se respeta tal cual (no se re-suma)
    tr2 = SimpleNamespace(hour=1, entry_ts=datetime(2026, 3, 16, 1, 30))
    assert mrl._entry_hour_et(tr2, 120) == 1
    assert zone_of_hour(mrl._entry_hour_et(tr2, 120)) == "Asia"


def test_activacion_from_study_ignora_toggles():
    """Aplicar (activacion_from_study) sale SOLO de levers_in_sample: aunque el
    dict traiga zones_off/days_off, jamás terminan en la config aplicable."""
    study = {
        "levers_in_sample": {
            "b_pts": 90.0, "backstop_usd": 4500.0,
            "tp_por_lado_atr": {"long": 6.0, "short": 5.0},
            "ladder": {"alloc": [10, 0, 0], "levels": [0.0]},
        },
        "cancel_after_s": 3600,
        # ruido de toggles que NO debe filtrarse a la config:
        "zones_off": ["Asia", "Cierre US"], "days_off": [4],
    }
    act = mrl.activacion_from_study(study)
    for k in ("zones_off", "days_off", "windows", "session_config_json",
              "days_enabled", "entry_start", "entry_end", "sesion", "dow"):
        assert k not in act, k
    # sí trae las palancas de riesgo (el aplicar real)
    assert act["backstop_points"] == 90.0
    assert act["tp_nominal_long"] == 6.0


# ── LX-3b — semáforo de robustez · $/trade / retención · banner de muestra ──

def test_robustez_semaforo_tres_estados():
    R = mrl.robustez_semaforo
    assert R({"net_usd": 1000, "pf": 1.5, "n": 20})["verdict"] == "verde"     # 🟢
    assert R({"net_usd": 1000, "pf": 1.30, "n": 20})["verdict"] == "verde"    # umbral incl.
    assert R({"net_usd": 500, "pf": 1.15, "n": 15})["verdict"] == "amarillo"  # 🟡
    assert R({"net_usd": 500, "pf": 1.00, "n": 15})["verdict"] == "amarillo"  # borde inf.
    assert R({"net_usd": -1, "pf": 2.0, "n": 15})["verdict"] == "rojo"        # 🔴 neto≤0 (n≥10)
    assert R({"net_usd": 100, "pf": 0.9, "n": 15})["verdict"] == "rojo"       # 🔴 PF<1.0 (n≥10)
    # LX-14 — muestra OOS chica (n<RETENCION_N_MIN): ni verde ni rojo (⚪)
    assert R({"net_usd": -1, "pf": 2.0, "n": 9})["verdict"] == "sin_veredicto"
    assert R({"net_usd": 100, "pf": 2.87, "n": 2})["verdict"] == "sin_veredicto"  # el caso GC
    assert R({"net_usd": None, "pf": None, "n": 0})["verdict"] == "sin_veredicto"
    # umbrales como constantes nombradas
    assert mrl.ROBUSTEZ_PF_VERDE == 1.3 and mrl.ROBUSTEZ_PF_MIN == 1.0


def test_expectativa_y_retencion_con_guardas():
    # $/trade = neto ÷ n
    assert mrl._expectativa({"net_usd": 340, "n": 17}) == 20.0
    assert mrl._expectativa({"net_usd": 100, "n": 0}) is None      # guarda división
    assert mrl._expectativa({"net_usd": None, "n": 5}) is None
    # retención = $/trade OOS ÷ $/trade Crudo+
    r = mrl.retencion_oos({"net_usd": 340, "n": 17}, {"net_usd": 2040, "n": 102})
    assert r["pct"] == 100.0 and r["muestra_chica"] is False       # n_oos 17 ≥ 10
    # muestra chica: n_oos < 10
    assert mrl.retencion_oos({"net_usd": 50, "n": 5},
                             {"net_usd": 2040, "n": 102})["muestra_chica"] is True
    # división por cero / sin muestra → pct None (no revienta)
    assert mrl.retencion_oos({"net_usd": 340, "n": 17},
                             {"net_usd": 0, "n": 102})["pct"] is None
    assert mrl.retencion_oos({"net_usd": 340, "n": 0},
                             {"net_usd": 2040, "n": 102})["pct"] is None


def test_muestra_banner_on_off_y_texto():
    # OFF: toda la muestra simulable → sin banner
    assert mrl.muestra_banner(120, 120) is None
    assert mrl.muestra_banner(120, 130) is None                    # nunca negativo
    # ON: texto corregido (el HOLC vive en NTEXECG, no viaja en la lista)
    b = mrl.muestra_banner(121, 102)
    assert b is not None
    assert "19 de 121" in b
    assert "cobertura HOLC almacenada en NTEXECG" in b
    assert "Crudo+ los excluye de la simulación" in b


def test_zone_of_hour_es_la_fuente_unica_de_los_toggles():
    """El motor excluye por la MISMA partición que el front pinta (R-T7): las
    zonas de los switches son las de sesiones_et y cubren 0..23 sin huecos."""
    covered = [h for _n, _et, hrs in LUXY_ZONES for h in hrs]
    assert sorted(covered) == list(range(24))
    # cada zona canónica es un id válido de zones_off
    for name, _et, hours in LUXY_ZONES:
        for h in hours:
            assert zone_of_hour(h) == name
