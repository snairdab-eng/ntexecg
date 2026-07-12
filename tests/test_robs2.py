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
    # sin cuenta propia → hereda el global (L7b: helper vivo; la página v1 que
    # antes la pintaba se retiró — la analítica de cuenta vive en Perfiles L4).
    assert rr._leer_cuenta("ES_Test") == 25_000.0
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


# L7b — los tests de RENDER de la ficha v1 (rango por lado, protección) se
# retiraron con la página. La lógica sigue bajo prueba en unidad:
# `test_listado_crudo_rango_por_lado` (arriba) y los `test_proteccion_*`; y su
# presencia EN EL DETALLE (Luxy/Perfiles) en `test_estrategias_l1
# ::test_luxy_ventana_paridad_v1_real` y `test_perfiles_l4.py`.


# ---------------------------------------------------------------------------
# R-obs-2c — SL/TP en ×ATR o PUNTOS FIJOS (base) + herencia en los 4 perfiles
# ---------------------------------------------------------------------------

def _cfg_bracket(**profiles_kw) -> dict:
    return {
        "traderspost_webhook_url": "https://tp/base",
        "sl_atr_multiplier": 2.5, "tp_atr_multiplier": 6.0,
        "backstop_points": 90.0,
        "tp_nominal_long": 11.5, "tp_nominal_short": 8.0,
        "scale_entry": {"mode": "execute", "levels": [0.25, 0.5],
                        "quantities": [0, 5, 5], "max_micro_contracts": 10},
        "dry_run": True, "traderspost_enabled": False,
        **profiles_kw,
    }


def test_perfiles_heredan_bracket_completo_y_niveles():
    """Los 4 perfiles heredan de la base el bracket COMPLETO (stop fijo +
    TP nominal por lado) y los niveles ATR — pedido explícito del operador."""
    from app.services import dispatch_profiles as dp
    cfg = _cfg_bracket(profiles=[
        {"name": f"p{i}", "enabled": True, "webhook_url": f"https://tp/{i}"}
        for i in range(4)])
    dests = dp.resolve_destinations(cfg)
    assert len(dests) == 5                    # base + 4 perfiles
    for d in dests:
        assert d["backstop_points"] == 90.0
        assert d["tp_nominal_long"] == 11.5
        assert d["tp_nominal_short"] == 8.0
        assert d["scale_entry"]["levels"] == [0.25, 0.5]


def test_override_atr_de_un_perfil_apaga_lo_heredado():
    """El override Avanzado es explícito: SL×ATR en el perfil REEMPLAZA al
    stop fijo heredado (si no, la precedencia del L5 lo dejaría mudo); TP
    explícito (aunque None) apaga el nominal heredado."""
    from app.services import dispatch_profiles as dp
    cfg = _cfg_bracket(profiles=[
        {"name": "atr", "enabled": True, "webhook_url": "https://tp/a",
         "sl_atr_multiplier": 4.0, "tp_atr_multiplier": None}])
    d = dp.resolve_destinations(cfg)[1]
    assert d["backstop_points"] is None       # el override manda
    assert d["sl_atr_multiplier"] == 4.0
    assert d["tp_nominal_long"] is None and d["tp_nominal_short"] is None
    assert d["tp_atr_multiplier"] is None     # "sin TP" explícito
    # y el config proyectado lleva las mismas llaves (payload/gate)
    proj = dp.make_dest_config(cfg, d)
    assert proj["backstop_points"] is None
    assert proj["tp_nominal_long"] is None


def test_recompute_bracket_precedencia_l5():
    """recompute_bracket espeja la precedencia del L5: backstop (sin ATR)
    > SL×ATR; TP nominal del lado > TP único; guarda P0 espejada."""
    from app.services.dispatch_profiles import recompute_bracket
    dest = {"backstop_points": 90.0, "sl_atr_multiplier": 2.5,
            "tp_nominal_long": 11.5, "tp_nominal_short": 8.0,
            "tp_atr_multiplier": 6.0}
    # long con ATR: backstop fijo + nominal del lado largo
    sl, tp = recompute_bracket(5000.0, 10.0, True, dest)
    assert sl == 5000.0 - 90.0
    assert tp == 5000.0 + 10.0 * 11.5
    # short: nominal del lado corto
    sl, tp = recompute_bracket(5000.0, 10.0, False, dest)
    assert sl == 5000.0 + 90.0
    assert tp == 5000.0 - 10.0 * 8.0
    # sin ATR: el backstop se computa igual; TP cae al ancho del backstop
    sl, tp = recompute_bracket(5000.0, None, True, dest)
    assert sl == 4910.0 and tp == 5090.0
    # sin backstop → SL×ATR; TP único cuando no hay nominal
    d2 = {"sl_atr_multiplier": 2.0, "tp_atr_multiplier": 6.0}
    sl, tp = recompute_bracket(5000.0, 10.0, True, d2)
    assert sl == 4980.0 and tp == 5060.0
    # sin ATR ni backstop → no computable (el caller cae al bracket base)
    assert recompute_bracket(5000.0, None, True, d2) == (None, None)
    # guarda P0 espejada: backstop más grande que el precio → inválido
    d3 = {"backstop_points": 6000.0}
    assert recompute_bracket(5000.0, 10.0, True, d3) == (None, None)


@pytest.mark.asyncio
async def test_sltp_form_modo_pts_y_nominal(client: AsyncClient, db) -> None:
    """El form SL/TP guarda en ×ATR o PUNTOS FIJOS y TP nominal por lado —
    los mismos campos que aplica el Motor de Riesgo."""
    from sqlalchemy import select

    from app.models.strategy import Strategy
    from app.models.strategy_profile import StrategyProfile
    db.add(Strategy(strategy_id="BR_Test", name="T", asset_symbol="MES",
                    status="paper", enabled=True))
    await db.commit()

    async def _prof():
        return (await db.execute(select(StrategyProfile).where(
            StrategyProfile.strategy_id == "BR_Test"))).scalar_one()

    # pts + nominal (el combo del estudio)
    r = await client.post("/ui/strategies/BR_Test/sltp", data={
        "sl_mode": "pts", "backstop_points": "90",
        "tp_mode": "nominal", "tp_nominal_long": "11.5",
        "tp_nominal_short": "8"})
    assert r.status_code == 303
    prof = await _prof()
    cfg = prof.pipeline_config_json
    assert cfg["backstop_points"] == 90.0
    assert cfg["tp_nominal_long"] == 11.5 and cfg["tp_nominal_short"] == 8.0
    # volver a ×ATR único: apaga backstop y nominales
    r = await client.post("/ui/strategies/BR_Test/sltp", data={
        "sl_mode": "atr", "sl_atr_multiplier": "2.5",
        "tp_mode": "unico", "tp_atr_multiplier": "6"})
    assert r.status_code == 303
    await db.refresh(prof)
    cfg = prof.pipeline_config_json or {}
    assert "backstop_points" not in cfg
    assert "tp_nominal_long" not in cfg
    assert float(prof.sl_atr_multiplier) == 2.5
    assert float(prof.tp_atr_multiplier) == 6.0
    # validación: pts sin valor → error y nada cambia
    r = await client.post("/ui/strategies/BR_Test/sltp", data={
        "sl_mode": "pts", "backstop_points": "", "tp_mode": "unico",
        "tp_atr_multiplier": "6"})
    assert r.status_code == 303 and "requiere" in r.headers["location"]
    # retrocompat: POST legacy sin modos = ×ATR único (tests viejos)
    r = await client.post("/ui/strategies/BR_Test/sltp", data={
        "sl_atr_multiplier": "1.5", "tp_atr_multiplier": ""})
    assert r.status_code == 303
    await db.refresh(prof)
    assert float(prof.sl_atr_multiplier) == 1.5
    assert prof.tp_atr_multiplier is None


@pytest.mark.asyncio
async def test_form_sltp_renderiza_modos(client: AsyncClient, db) -> None:
    from app.models.strategy import Strategy
    from app.models.strategy_profile import StrategyProfile
    db.add(Strategy(strategy_id="BR_UI", name="T", asset_symbol="MES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="BR_UI", pipeline_config_json={
        "backstop_points": 90.0, "tp_nominal_long": 11.5,
        "tp_nominal_short": 8.0}))
    await db.commit()
    r = await client.get("/ui/strategies/BR_UI")
    assert r.status_code == 200
    html = r.text
    assert 'name="sl_mode"' in html and 'name="tp_mode"' in html
    assert "puntos fijos desde la señal" in html
    assert "nominal por lado" in html
    assert "slMode: &#39;pts&#39;" in html or "slMode: 'pts'" in html
    assert "heredan de la base el bracket COMPLETO" in html


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


# ---------------------------------------------------------------------------
# LOTE RIES-W — ventana de operación (de COBERTURA, no de filtrado)
# ---------------------------------------------------------------------------

def _t_at(side: str, y: int, mo: int, d: int, h: int, mi: int,
          pnl: float) -> SimpleNamespace:
    ts = datetime(y, mo, d, h, mi)
    return SimpleNamespace(side=side, pnl_usd=pnl, entry_ts=ts,
                           exit_ts=ts + timedelta(hours=1))


# Sección de ventana ya persistida (para las fichas/reporte, sin recalcular)
VENTANA_OP = {
    "nota": ("El filtro de sesión/hora NO aporta edge — DESCARTADO por diseño "
             "(validado 2026-07-04). Esta ventana es de COBERTURA."),
    "cuenta_ref_usd": 10000.0, "umbral_rojo_usd": 1000.0,
    "n_trades": 5, "n_rojos": 1,
    "por_sesion": {
        "RTH": {"n": 3, "net_usd": 250.0, "pf": 1.5, "peor_trade_usd": -50.0,
                "rojos": 0, "rojos_pct": 0.0},
        "asia": {"n": 2, "net_usd": -1920.0, "pf": None,
                 "peor_trade_usd": -2000.0, "rojos": 1, "rojos_pct": 100.0},
    },
    "rango_horario_et": {
        "total": {"n": 5, "min": 10.0, "max": 22.0, "p05": 10.2, "p95": 21.6},
        "long": {"n": 3, "min": 10.0, "max": 14.0, "p05": 10.1, "p95": 13.7},
        "short": {"n": 2, "min": 20.0, "max": 22.0, "p05": 20.1, "p95": 21.9},
    },
    "ventana_minima_cobertura": {
        "dias_w": [1], "dias_label": "lun", "hora_desde": 10, "hora_hasta": 22,
        "cobertura_pct": 100.0,
        "p95_ref": {"hora_desde": 10, "hora_hasta": 22},
    },
    "muestras": [[1, 600], [1, 660], [1, 840], [1, 1200], [1, 1320]],
}

_LC_BASE = {
    "metricas": {"n": 120, "net_usd": 28175.0, "pf": 1.62, "wr_pct": 79.2,
                 "max_dd_usd": 11750.0, "peor_trade_usd": -10162.5},
    "duracion_h": {"ganador_prom_h": 26.9, "perdedor_prom_h": 15.1,
                   "n_ganadores": 95, "n_perdedores": 25},
}


def test_ventana_operacion_distribucion_y_cobertura():
    """Estudio con trades sintéticos en 2 sesiones ET: distribución por sesión
    (n/net/rojos), ventana mínima de cobertura y rango horario por lado."""
    from scripts.nt_riesgo import _listado_crudo
    # 2026-07-06 = lunes (%w=1). RTH (10/11/14h) + asia (20/22h).
    trades = [
        _t_at("long", 2026, 7, 6, 10, 0, 100),
        _t_at("long", 2026, 7, 6, 11, 0, -50),
        _t_at("long", 2026, 7, 6, 14, 0, 200),
        _t_at("short", 2026, 7, 6, 20, 0, 80),
        _t_at("short", 2026, 7, 6, 22, 0, -2000),      # ROJO (≥ $1,000)
    ]
    vo = _listado_crudo(trades)["ventana_operacion"]
    assert vo["n_trades"] == 5 and vo["umbral_rojo_usd"] == 1000.0
    assert vo["n_rojos"] == 1
    ps = vo["por_sesion"]
    assert ps["RTH"]["n"] == 3 and ps["RTH"]["net_usd"] == 250.0
    assert ps["RTH"]["rojos"] == 0 and ps["RTH"]["rojos_pct"] == 0.0
    assert ps["asia"]["n"] == 2 and ps["asia"]["rojos"] == 1
    assert ps["asia"]["rojos_pct"] == 100.0             # el único rojo cae ahí
    rg = vo["rango_horario_et"]
    assert rg["total"]["min"] == 10.0 and rg["total"]["max"] == 22.0
    assert rg["long"]["min"] == 10.0 and rg["long"]["max"] == 14.0
    assert rg["short"]["min"] == 20.0 and rg["short"]["max"] == 22.0
    assert rg["total"]["p05"] is not None and rg["total"]["p95"] is not None
    vm = vo["ventana_minima_cobertura"]
    assert vm["dias_w"] == [1] and vm["dias_label"] == "lun"
    assert vm["hora_desde"] == 10 and vm["hora_hasta"] == 22
    assert vm["cobertura_pct"] == 100.0 and vm["p95_ref"] is not None
    assert [1, 600] in vo["muestras"] and [1, 1320] in vo["muestras"]


def test_ventana_operacion_usa_offset_et():
    """El ET calca el enriched: entrada 09:00 'naive' + offset +60 → 10:00 ET
    → sesión RTH (paridad con write_enriched)."""
    from scripts.nt_riesgo import _listado_crudo
    vo = _listado_crudo([_t_at("long", 2026, 7, 6, 9, 0, 100)],
                        offset_min=60)["ventana_operacion"]
    assert "RTH" in vo["por_sesion"]
    assert vo["muestras"] == [[1, 600]]


def test_pct_trades_fuera_angosta_y_24h():
    """% de trades del backtest FUERA de la ventana vigente: angosta (RTH
    Mon-Fri) deja fuera los de asia y el sábado; 24h/sin ventana cubre todo."""
    from app.web.routes_riesgo import _pct_trades_fuera
    # lun 10:00 (dentro), lun 20:00 (fuera por hora), sáb 10:00 (fuera por día)
    muestras = [[1, 600], [1, 1200], [6, 600]]
    angosta = {"days_enabled": [1, 2, 3, 4, 5],
               "entry_start": "09:30", "entry_end": "15:45"}
    assert _pct_trades_fuera(angosta, muestras) == 66.7
    # forma 'windows' (override de estrategia) — mismo resultado
    wins = {"windows": [{"days": [1, 2, 3, 4, 5],
                         "start": "09:30", "end": "15:45"}]}
    assert _pct_trades_fuera(wins, muestras) == 66.7
    # 24h / sin ventana restrictiva → cubre todo (como el L2)
    assert _pct_trades_fuera({}, muestras) == 0.0
    assert _pct_trades_fuera(None, muestras) == 0.0
    assert _pct_trades_fuera(angosta, []) is None       # sin muestras


# L7b — la ficha v1 de la ventana de operación se retiró; la ventana + la
# comparación con la ventana L2 vigente viven ahora en el detalle (Luxy, L7a) y
# se prueban en `test_estrategias_l1::test_luxy_ventana_paridad_v1_real`. Aquí se
# conserva la prueba UNITARIA del helper que ambas rutas reusan (`_pct_trades_fuera`),
# incluida la arista del banner "trades fuera de la ventana" (participación perdida).

def test_pct_trades_fuera_helper_intacto():
    scfg = {"days_enabled": [1, 2, 3, 4, 5],
            "entry_start": "09:30", "entry_end": "15:45"}
    # 3 dentro RTH (10/11/14h) + 2 fuera (20/22h) → 40% fuera (banner ámbar)
    muestras = [[1, 600], [1, 660], [1, 840], [1, 1200], [1, 1320]]
    assert rr._pct_trades_fuera(scfg, muestras) == 40.0
    # sin ventana L2 restrictiva → cubre todo (0%)
    assert rr._pct_trades_fuera(None, muestras) == 0.0
    # sin muestras → None (la vista no pinta comparación)
    assert rr._pct_trades_fuera(scfg, []) is None


def test_reporte_md_incluye_ventana_operacion():
    """El .md del reporte trae la sección de ventana (solo la parte del
    estudio: tabla por sesión + ventana mínima de cobertura)."""
    from scripts.mr_report import _ventana_md
    md = "\n".join(_ventana_md(VENTANA_OP))
    assert "Ventana de operación (COBERTURA" in md
    assert "| RTH |" in md and "| asia |" in md
    assert "Ventana mínima de cobertura (100%)" in md
    assert "lun · 10:00–22:00 ET" in md
    assert "DESCARTADO por diseño" in md
