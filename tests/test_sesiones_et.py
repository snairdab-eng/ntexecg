"""L7a — partición ÚNICA de sesiones/zonas horarias ET (R-T7).

El módulo `scripts.sesiones_et` es la fuente CANÓNICA de las 6 zonas de Luxy;
`mr_luxy` la consume (no re-define). Determinista y con cobertura total 0..23.
(Riesgo v1 conserva su `sesion_et` grueso hasta L7b — aquí no se toca.)
"""
import scripts.mr_luxy as mrl
from scripts import sesiones_et as se


def test_zonas_cobertura_total_sin_solapes():
    covered = [h for _n, _e, hrs in se.LUXY_ZONES for h in hrs]
    assert sorted(covered) == list(range(24))
    assert len(covered) == 24                      # sin duplicados ni huecos


def test_zone_of_hour_casos():
    assert se.zone_of_hour(9) == "Apertura US"
    assert se.zone_of_hour(3) == "Europa/Londres"
    assert se.zone_of_hour(19) == "Asia"
    assert se.zone_of_hour(0) == "Asia"            # cruza medianoche
    assert se.zone_of_hour(16) == "Cierre US"
    assert se.zone_of_hour(None) is None


def test_determinista():
    # función pura: mismas entradas → mismas salidas, y todo hueco 0..23 mapea.
    assert [se.zone_of_hour(h) for h in range(24)] == \
           [se.zone_of_hour(h) for h in range(24)]
    assert all(se.zone_of_hour(h) is not None for h in range(24))


def test_mr_luxy_reexporta_la_fuente_unica():
    # Luxy CONSUME el módulo canónico: los MISMOS objetos, no una copia que
    # pueda divergir (R-T7 — una sola partición para motor, Lab y front).
    assert mrl.LUXY_ZONES is se.LUXY_ZONES
    assert mrl.zone_of_hour is se.zone_of_hour
    assert mrl._DAY_ES is se._DAY_ES


def test_et_labels_y_dias():
    assert all("ET" in et for _n, et, _h in se.LUXY_ZONES)   # rango ET viaja
    assert set(se._DAY_ES) == set(range(7))
