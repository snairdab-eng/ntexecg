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


def test_zone_of_hour_es_la_fuente_unica_de_los_toggles():
    """El motor excluye por la MISMA partición que el front pinta (R-T7): las
    zonas de los switches son las de sesiones_et y cubren 0..23 sin huecos."""
    covered = [h for _n, _et, hrs in LUXY_ZONES for h in hrs]
    assert sorted(covered) == list(range(24))
    # cada zona canónica es un id válido de zones_off
    for name, _et, hours in LUXY_ZONES:
        for h in hours:
            assert zone_of_hour(h) == name
