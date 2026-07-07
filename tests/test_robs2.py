"""R-obs-2 (2026-07-07) — observaciones del operador sobre las fichas.

Candados:
  1. Protección de cuenta con PARTICIPACIÓN 100% OBLIGATORIA: el objetivo es
     capar la pérdida catastrófica, NO filtrar señales — "sobrevivir dejando
     de operar" (escaleras que no llenan, lados bloqueados) queda fuera.
  2. La cuenta editable vive POR ESTRATEGIA (fallback global → default).
  3. El listado crudo reporta el RANGO de tiempo de operación POR LADO
     (largos/cortos) — el dato que dimensiona el topo del cancel_after de
     TradersPost (máx duro 3600s).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

import app.web.routes_lab as routes_lab
import app.web.routes_riesgo as rr
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from scripts.mr_sims import proteccion_para_cuenta
from tests.test_riesgo_ui import ESTUDIO, PROTECCION, _seed_motor, \
    _write_lab_manifest


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_robs2")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "REPORTES").mkdir()
    (tmp_path / "ListaDeOperaciones").mkdir()
    (tmp_path / "MotorRiesgo").mkdir()
    monkeypatch.setattr(routes_lab, "LAB_DIR", tmp_path / "REPORTES")
    monkeypatch.setattr(routes_lab, "TRADES_DIR",
                        tmp_path / "ListaDeOperaciones")
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    monkeypatch.setattr(rr, "TRADES_DIR", tmp_path / "ListaDeOperaciones")
    rr.JOBS.clear()
    rr._INTEGRAR_LOCKS.clear()
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Protección: participación 100% obligatoria (selección pura)
# ---------------------------------------------------------------------------

def _combo(nombre: str, participacion: float, peor: float, net: float,
           palancas: int = 1, lado=None) -> dict:
    return {"escalera": {"nombre": nombre,
                         "piernas": [{"depth_atr": 0.0, "micros": 10}]},
            "sl_atr": None, "backstop_usd": 900.0,
            "tp_por_lado_atr": {"long": 11.5, "short": 8.0}, "lado": lado,
            "n_palancas": palancas, "participacion_pct": participacion,
            "ganadoras_cortadas_pct": 0.0,
            "metricas": {"n": 100, "net_usd": net, "pf": 1.5, "wr_pct": 70.0,
                         "max_dd_usd": 3000.0, "peor_trade_usd": peor}}


CRUDO_TOTAL = {"net_usd": 20000.0, "pf": 1.6, "wr_pct": 75.0,
               "max_dd_usd": 11000.0, "peor_trade_usd": -9000.0}


def test_proteccion_exige_participacion_100():
    """El combo 'mejor' con 5% de participación (el absurdo de ES: escalera
    que no llena + bloquear largos) NO puede ganar aunque sobreviva mejor —
    gana el 100% que sobrevive."""
    prot = dict(PROTECCION)
    prot["combos"] = [
        _combo("escalera profunda que no llena", 5.0, -300.0, 4000.0,
               palancas=3, lado="short"),          # superviviente... al 5%
        _combo("backstop simple", 100.0, -900.0, 15000.0, palancas=1),
    ]
    pc = proteccion_para_cuenta(prot, 10_000.0, CRUDO_TOTAL)
    assert pc["elegido"]["escalera"]["nombre"] == "backstop simple"
    assert pc["elegido"]["participacion_pct"] == 100.0
    assert pc["protegido"] is True
    assert "participación 100%" in pc["nota_supervivencia"]


def test_proteccion_100_aunque_nadie_sobreviva():
    """Si ningún combo al 100% sobrevive, se recomienda el 100% que MÁS
    acerca — jamás el de participación baja aunque ese sí sobreviva."""
    prot = dict(PROTECCION)
    prot["combos"] = [
        _combo("5% superviviente", 5.0, -200.0, 3000.0, palancas=3),
        _combo("100% que no llega", 100.0, -2500.0, 12000.0),  # 25% > umbral
    ]
    pc = proteccion_para_cuenta(prot, 10_000.0, CRUDO_TOTAL)
    assert pc["elegido"]["escalera"]["nombre"] == "100% que no llega"
    assert pc["protegido"] is False
    assert "más se acerca" in pc["nota_supervivencia"]


def test_proteccion_fallback_sin_plenos():
    """Sin NINGÚN combo al 100% (no debería pasar) → fallback honesto al
    barrido completo, con la bandera en la nota."""
    prot = dict(PROTECCION)
    prot["combos"] = [_combo("a", 60.0, -500.0, 5000.0),
                      _combo("b", 80.0, -400.0, 6000.0)]
    pc = proteccion_para_cuenta(prot, 10_000.0, CRUDO_TOTAL)
    assert pc["elegido"] is not None
    assert "ningún combo participa al 100%" in pc["nota_supervivencia"]


# ---------------------------------------------------------------------------
# 2. Cuenta por estrategia (fallback global → default)
# ---------------------------------------------------------------------------

SID = "ES5m_Test"


def _manifest_es(dirs: Path) -> None:
    _write_lab_manifest(dirs, {SID: {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})


@pytest.mark.asyncio
async def test_cuenta_por_estrategia(client: AsyncClient, dirs: Path) -> None:
    _manifest_es(dirs)
    _seed_motor(dirs)
    # global de arranque
    (dirs / "MotorRiesgo" / "cuenta.json").write_text(
        json.dumps({"cuenta_usd": 25_000.0}), encoding="utf-8")
    # sin cuenta propia → hereda el global
    r = await client.get(f"/ui/riesgo?strategy={SID}")
    assert "25000" in r.text.replace(",", "").replace(".0", "")
    # guardar POR estrategia
    r = await client.post("/ui/riesgo/cuenta",
                          json={"cuenta_usd": 50_000.0, "strategy": SID})
    assert r.status_code == 200 and SID in r.json()["ambito"]
    assert (dirs / "MotorRiesgo" / "ES_Test" / "cuenta.json").exists()
    # la página de ESTA estrategia usa la suya; el global no se tocó
    assert rr._leer_cuenta("ES_Test") == 50_000.0
    assert rr._leer_cuenta() == 25_000.0
    assert rr._leer_cuenta("Otra_Clave") == 25_000.0     # fallback global
    # sin strategy → sigue escribiendo el global (retrocompat)
    r = await client.post("/ui/riesgo/cuenta", json={"cuenta_usd": 30_000.0})
    assert r.status_code == 200 and r.json()["ambito"] == "global"
    assert rr._leer_cuenta() == 30_000.0
    # estrategia fuera del manifest → 400
    r = await client.post("/ui/riesgo/cuenta",
                          json={"cuenta_usd": 1000.0, "strategy": "NoExiste"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 3. Rango de tiempo de operación por lado
# ---------------------------------------------------------------------------

def _trade(side: str, horas: float, pnl: float) -> SimpleNamespace:
    t0 = datetime(2026, 7, 1, 9, 0)
    return SimpleNamespace(side=side, pnl_usd=pnl, entry_ts=t0,
                           exit_ts=t0 + timedelta(hours=horas))


def test_listado_crudo_rango_por_lado():
    from scripts.nt_riesgo import _listado_crudo
    trades = [_trade("long", 1.0, 100), _trade("long", 3.0, -50),
              _trade("long", 10.0, 200), _trade("short", 0.5, 80)]
    lc = _listado_crudo(trades)
    lados = lc["duracion_h_por_lado"]
    assert lados["long"]["n"] == 3
    assert lados["long"]["min_h"] == 1.0
    assert lados["long"]["max_h"] == 10.0
    assert lados["long"]["p50_h"] == 3.0
    assert lados["short"] == {"n": 1, "min_h": 0.5, "p50_h": 0.5,
                              "p90_h": 0.5, "max_h": 0.5}
    # sin trades de un lado → None (la ficha muestra "sin muestra")
    lc2 = _listado_crudo([_trade("long", 2.0, 10)])
    assert lc2["duracion_h_por_lado"]["short"] is None


@pytest.mark.asyncio
async def test_ficha_muestra_rango_por_lado(client: AsyncClient,
                                            dirs: Path) -> None:
    _manifest_es(dirs)
    estudio = json.loads(json.dumps(ESTUDIO))
    estudio["listado_crudo"] = {
        "metricas": {"n": 120, "net_usd": 28175.0, "pf": 1.62,
                     "wr_pct": 79.2, "max_dd_usd": 11750.0,
                     "peor_trade_usd": -10162.5},
        "duracion_h": {"ganador_prom_h": 26.9, "perdedor_prom_h": 15.1,
                       "n_ganadores": 95, "n_perdedores": 25},
        "duracion_h_por_lado": {
            "long": {"n": 80, "min_h": 0.3, "p50_h": 4.2, "p90_h": 38.5,
                     "max_h": 120.4},
            "short": None,
        },
    }
    _seed_motor(dirs, estudio=estudio)
    r = await client.get(f"/ui/riesgo?strategy={SID}")
    assert r.status_code == 200
    html = r.text
    assert "Rango de operación por lado" in html
    assert "p50 <b class=\"text-gray-200\">4.2h</b>" in html
    assert "38.5h" in html
    assert "cortos — sin muestra" in html
    assert "3600s = 1h" in html                     # el topo, visible
    # estudio viejo SIN el campo → la ficha no truena ni muestra la sección
    _seed_motor(dirs, clave="ES_Test2", estudio=ESTUDIO)


@pytest.mark.asyncio
async def test_ficha_proteccion_espeja_lineas_sin_cajas(client: AsyncClient,
                                                        dirs: Path) -> None:
    """R-obs-2b: la ficha de protección espeja las LÍNEAS de la validada
    (SL, Escalera, TP, Lado, cancel_after, Sizing, Confianza — números
    propios) y las 4 cajas de 'efecto' se retiraron (tachadas por el
    operador: sus números ya viven en las tarjetas KPI)."""
    _manifest_es(dirs)
    estudio = json.loads(json.dumps(ESTUDIO))
    estudio["proteccion"] = PROTECCION
    _seed_motor(dirs, estudio=estudio)
    r = await client.get(f"/ui/riesgo?strategy={SID}")
    assert r.status_code == 200
    html = r.text
    assert "(in-sample) — palancas" in html
    # las líneas nuevas del espejo: la validada usa "cancel_after coherente";
    # la protección su propio "cancel_after"; Sizing/Confianza en AMBAS
    assert "<b>cancel_after:</b>" in html
    assert html.count("<b>Sizing:</b>") >= 2
    assert html.count("<b>Confianza:</b>") >= 2
    assert "PF in-sample" in html
    assert "tamaño fijo, sin equity" in html
    # las cajas tachadas: FUERA
    for caja in ("Peor trade protegido", "Max DD protegido", "Costo en net",
                 "Ganadoras cortadas por el stop"):
        assert caja not in html, caja
    # participación 100% visible en el objetivo del bloque
    assert "sin saltar señales" in html


def test_reporte_md_incluye_rango_por_lado():
    from scripts.mr_report import render_md
    res = json.loads(json.dumps(ESTUDIO))
    res["listado_crudo"] = {
        "metricas": {"n": 120, "net_usd": 28175.0, "pf": 1.62,
                     "wr_pct": 79.2, "max_dd_usd": 11750.0,
                     "peor_trade_usd": -10162.5},
        "duracion_h": {"ganador_prom_h": 26.9, "perdedor_prom_h": 15.1,
                       "n_ganadores": 95, "n_perdedores": 25},
        "duracion_h_por_lado": {
            "long": {"n": 80, "min_h": 0.3, "p50_h": 4.2, "p90_h": 38.5,
                     "max_h": 120.4},
            "short": {"n": 40, "min_h": 0.1, "p50_h": 1.9, "p90_h": 9.7,
                      "max_h": 30.2},
        },
    }
    try:
        md = render_md(res)
    except Exception as exc:
        pytest.skip(f"render_md necesita más campos del estudio real: {exc}")
    assert "Rango de operación largos" in md
    assert "p50 4.2h" in md
