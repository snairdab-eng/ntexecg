"""LOTE DISPLAY-FX + SL-RESPIRO (2026-07-18) — tres partes, cada una con tests.

Parte 1 — etiqueta de UNIVERSO: la línea "CRUDO (señal, sin gestión)" del
  stdout/.md salía del universo CONTENIDO pero se imprimía junto al "Listado
  completo" sin declarar que son universos distintos (GC: −$14,930/41 vs lista
  −$8,880/44). Ahora declara "universo contenido, N de M trades" (U-1/U-4).
Parte 2 — display FX en TICKS (linaje FIX-FX-BACKSTOP): leyenda del Recorrido,
  línea "Backstop óptimo" de nt_riesgo y rincones Jinja usan fmt_pts / su
  espejo JS luxyFmtPts — 6E/6J en ticks del catálogo, jamás "0 pts".
Parte 3 — SL-RESPIRO: el slider del SL ya no está capado al MAE más profundo
  (forzaba "siempre toca"); respira ≥1.25× el pullback más profundo y nunca
  menos que el backstop del estudio ni la config viva, con muesca visual.
  El motor NO capa (evidencia: mr_luxy._luxy_exit_atr:179 solo dispara con
  mae_atr ≥ sl_atr; _overrides_to_levers:1304 convierte sin recorte).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_riesgo as rr
import scripts.mr_luxy as mrl
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from scripts.fx_levers import fmt_pts
from scripts.mr_report import _universo_lbl
from scripts.mr_sims import SimTrade

_TPL = Path("app/templates/strategy_detail.html")
_SRC = _TPL.read_text(encoding="utf-8")
_MR_SRC = Path("scripts/mr_report.py").read_text(encoding="utf-8")
_NR_SRC = Path("scripts/nt_riesgo.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# PARTE 1 — etiqueta de universo
# ---------------------------------------------------------------------------

def test_universo_lbl_n_de_m():
    res = {"universo": {"n": 41},
           "listado_crudo": {"metricas": {"n": 44}}}
    assert _universo_lbl(res) == "universo contenido, 41 de 44 trades"


def test_universo_lbl_fallback_meta():
    res = {"universo": {"n": 41}, "meta": {"n_trades_listado": 44}}
    assert _universo_lbl(res) == "universo contenido, 41 de 44 trades"


def test_universo_lbl_sin_listado():
    assert _universo_lbl({"universo": {"n": 41}}) \
        == "universo contenido, 41 trades"


def _res_6j():
    """res mínimo (6J, la config del hallazgo) para _print_resumen_estudios."""
    lado = {"n": 20, "net_usd": -4000.0, "pf": 0.8, "wr_pct": 50.0,
            "peor_trade_usd": -965.0, "giveback_perdedores_3atr": 0}
    return {
        "meta": {"activo": "6J", "n_trades_listado": 44},
        "linea_base": {"total": {"net_usd": -8000.0, "pf": 0.9,
                                 "max_dd_usd": 5000.0,
                                 "peor_trade_usd": -965.0}},
        "universo": {"n": 41, "n_atr_estimado": 0},
        "listado_crudo": {
            "metricas": {"n": 44},
            "duracion_h": {"ganador_prom_h": 5.0, "perdedor_prom_h": 3.0,
                           "n_ganadores": 20, "n_perdedores": 24}},
        "mae_floor": {"ganadoras_mae_atr": {"mediana": 1.0, "p90": 2.0,
                                            "p95": 2.5, "max": 3.0},
                      "veredicto": "ok"},
        # 6J: ppt 12.5M, $2,000 → 1.6e-4 pts = 320 ticks (tick 5e-7). El bug:
        # "$2,000.00 = 0 pts".
        "backstop": {"optimo": {"backstop_usd": 2000.0, "backstop_pts": 0.00016,
                                "x_atr_mediana": 160.0, "tocados": 0,
                                "delta_net_usd": 0.0, "delta_dd_pct": 0.0,
                                "peor_con_gap_usd": {}}},
        "tp": {"por_lado": {}, "tp_meta_mejor": None},
        "ls": {"lectura": "sin asimetría", "long": lado, "short": lado},
        "configs": [],
    }


def test_stdout_crudo_declara_universo_y_backstop_fx_en_ticks(capsys):
    import scripts.nt_riesgo as nr
    nr._print_resumen_estudios("6J_Test", _res_6j())
    out = capsys.readouterr().out
    # Parte 1 — doble universo declarado, "N de M"
    assert "universo contenido, 41 de 44 trades" in out
    assert "CRUDO (señal, sin gestión — universo contenido, 41 de 44 trades)" in out
    assert "Listado completo: 44 trades" in out
    assert "toca 0 de 41" in out
    # Parte 2 — backstop FX en TICKS del catálogo, jamás "0 pts"
    assert "320 ticks" in out
    assert "0 pts" not in out


def test_stdout_backstop_indice_sigue_en_pts(capsys):
    import scripts.nt_riesgo as nr
    res = _res_6j()
    res["meta"]["activo"] = "ES"
    res["backstop"]["optimo"].update(backstop_usd=4500.0, backstop_pts=90.0)
    nr._print_resumen_estudios("ES_Test", res)
    out = capsys.readouterr().out
    assert "90 pts" in out and "ticks" not in out


def test_md_fuente_usa_etiqueta_y_fmt_pts():
    """Guarda de FUENTE del .md (mismo criterio que test_bug_render_semaforo):
    la fila CRUDO y la nota de LÍNEA BASE llevan _universo_lbl; el backstop
    (óptimo y grid) pasa por fmt_pts (FX en ticks)."""
    assert "CRUDO (señal, sin gestión — {_universo_lbl(res)})" in _MR_SRC
    assert _MR_SRC.count("_universo_lbl(res)") >= 2      # nota §1 + fila §3
    assert "fmt_pts(meta['activo'], b['backstop_pts'])" in _MR_SRC
    assert "fmt_pts(meta['activo'], r['backstop_pts'])" in _MR_SRC
    # el formato viejo que colapsaba FX no vuelve
    assert "{b['backstop_pts']:.0f} pts" not in _MR_SRC


def test_riesgo_ui_declara_universo():
    """La ficha Riesgo (riesgo.html) declara el universo del CRUDO (misma
    fuente _universo_lbl vía _estudio_ctx)."""
    html = Path("app/templates/riesgo.html").read_text(encoding="utf-8")
    assert "estudio.universo_lbl" in html
    assert "_universo_lbl(res)" in Path("app/web/routes_riesgo.py").read_text(
        encoding="utf-8")


# ---------------------------------------------------------------------------
# PARTE 2 — espejo JS de fmt_pts (leyenda del Recorrido / readouts)
# ---------------------------------------------------------------------------

def _extract_js_pure() -> str:
    m = re.search(
        r"(window\.luxyFmtPts = function.*?window\.luxySlRange = function"
        r".*?\};)", _SRC, re.S)
    assert m, "no encuentro window.luxyFmtPts/luxySlRange en el template"
    return m.group(1)


def test_leyenda_y_readout_usan_fmt_pts_js():
    # la leyenda del Recorrido y el readout del SL usan el espejo de fmt_pts
    assert "const ptf=v=> window.luxyFmtPts(v, D.units, PV);" in _SRC
    assert "money(-S.slV)+window.luxyFmtPts(S.slV, D.units, PV)" in _SRC
    # el formato viejo (colapsaba/omitía FX) no vuelve
    assert "' ('+Math.round(v/PV)+' pts)'" not in _SRC


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_luxy_fmt_pts_node():
    harness = "var window={};\n" + _extract_js_pure() + "\n" + r"""
      var f = window.luxyFmtPts;
      function A(c,m){ if(!c){ console.error('FAIL: '+m); process.exit(1); } }
      var FX6J = {es_fx:true, tick:5e-7}, PV6J = 12500000;
      // 6J: SL $2,000 → 320 ticks — la guarda del hallazgo (jamás '0 pts')
      var s = f(2000, FX6J, PV6J);
      A(s.indexOf('ticks') >= 0, '6J debe salir en ticks: ' + s);
      A(s.indexOf('~320') >= 0, '6J $2,000 = ~320 ticks: ' + s);
      A(s.indexOf('0 pts') < 0, "6J jamás '0 pts': " + s);
      // valor negativo (leyenda pinta SL como −$X) → mismos ticks
      A(f(-2000, FX6J, PV6J).indexOf('~320') >= 0, 'signo no cambia los ticks');
      // índice: pts como siempre
      A(f(2000, {show_pts:true}, 50) === ' (40 pts)', 'ES $2,000 = 40 pts');
      // FX sin tick en catálogo → '' (fail-open, jamás fabricar rejilla ni '0 pts')
      A(f(2000, {es_fx:true}, 12500000) === '', 'FX sin tick → vacío');
      // sin show_pts (pv grande no-FX) → '' (como antes)
      A(f(2000, {show_pts:false}, 12500000) === '', 'sin show_pts → vacío');
      console.log('OK');
    """
    r = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout


def test_jinja_rincones_migrados_a_fmt_pts():
    # puente P1 (backstop_points) y perfiles (sl_pts) usan fmt_pts
    assert "fmt_pts(l1_instrument, mrcfg.get('backstop_points'))" in _SRC
    assert "fmt_pts(l1_instrument, P.sl_pts)" in _SRC
    # el global existe en el env compartido
    from app.web.common import templates
    assert templates.env.globals["fmt_pts"] is fmt_pts


# ---------------------------------------------------------------------------
# PARTE 3 — SL-RESPIRO
# ---------------------------------------------------------------------------

def test_slider_sl_respira_fuente():
    # el rango del SL sale de luxySlRange (estudio + config viva), no de maeMax
    assert "window.luxySlRange(maeMax, RECO0.slV, D.config_sl_usd)" in _SRC
    assert "setupRng('lx-r-sl',slMax" in _SRC
    assert "setupRng('lx-r-sl',maeMax" not in _SRC          # el cap viejo no vuelve
    # muesca del pullback más profundo (elemento + label + posicionamiento)
    assert 'id="lx-sl-muesca"' in _SRC and 'id="lx-sl-muesca-lbl"' in _SRC
    assert "pullback más profundo" in _SRC
    assert "maeMax/slMax*100" in _SRC
    # la muesca y el readout usan el espejo de fmt_pts (ticks en FX)
    assert "window.luxyFmtPts(maeMax, D.units, PV)" in _SRC


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_luxy_sl_range_node():
    harness = "var window={};\n" + _extract_js_pure() + "\n" + r"""
      var R = window.luxySlRange;
      function A(c,m){ if(!c){ console.error('FAIL: '+m); process.exit(1); } }
      // 6J del hallazgo: MAE más profundo ~$965 → fondo ≥ 1.25× = $1,207
      A(R(965, 0, 0) === 1207, '1.25x del MAE: ' + R(965,0,0));
      // ...pero nunca menos profundo que el backstop del estudio ($2,000)
      A(R(965, 2000, 0) === 2000, 'estudio manda: ' + R(965,2000,0));
      // ...ni que el valor vivo de la config
      A(R(800, 0, 3000) === 3000, 'config viva manda: ' + R(800,0,3000));
      // 1.25x gana cuando estudio/config son más someros
      A(R(1000, 1100, 900) === 1250, '1.25x domina: ' + R(1000,1100,900));
      // sin datos → 1 (rango mínimo del slider, como antes)
      A(R(0, 0, 0) === 1, 'vacío → 1');
      console.log('OK');
    """
    r = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout


# --- (c) el MOTOR no recorta un backstop más profundo que el max MAE --------

_PPT = 50.0


def _st(number, pnl_usd, mae_atr, mfe_atr=1.0, atr=10.0):
    return SimTrade(number=number, side="long", in_sample=True,
                    entry_price=5000.0, atr_pts=atr, mae_pts=mae_atr * atr,
                    mfe_pts=mfe_atr * atr, native_pnl_usd=pnl_usd)


def _lev(b_pts):
    return {"ladder": {"legs": ((0.0, 1.0),), "alloc": [10, 0, 0],
                       "levels": [0.0]},
            "b_pts": b_pts, "tp_por_lado_atr": None, "lado": None,
            "breakeven": None}


def test_motor_sl_mas_profundo_que_max_mae_no_recorta():
    """SL más profundo que TODO MAE observado ⇒ toca 0: cada desenlace es el
    NATIVO y el peor == peor nativo (la filosofía del backstop: seguro de
    catástrofe MÁS ALLÁ de lo observado)."""
    sts = [_st(1, -965.0, mae_atr=1.93), _st(2, 200.0, mae_atr=0.5),
           _st(3, -300.0, mae_atr=1.0)]
    # max MAE = 1.93×ATR = 19.3 pts; backstop a 50 pts (≫ observado)
    m = mrl.eval_levers(sts, _lev(50.0), _PPT, cancel_after_s=None,
                        touches=None)
    assert m["net_usd"] == pytest.approx(-965.0 + 200.0 - 300.0)
    assert m["peor_trade_usd"] == pytest.approx(-965.0)      # peor NATIVO
    assert m["participacion_pct"] == 100.0


def test_motor_sl_somero_si_recorta_contraste():
    """Contraste: un SL DENTRO de lo observado sí topa el peor trade — prueba
    que el camino del stop está vivo y el test anterior no pasa por vacío."""
    sts = [_st(1, -965.0, mae_atr=1.93), _st(2, 200.0, mae_atr=0.5)]
    m = mrl.eval_levers(sts, _lev(10.0), _PPT, cancel_after_s=None,
                        touches=None)                        # SL a 1.0×ATR
    assert m["peor_trade_usd"] == pytest.approx(-10.0 * _PPT)  # topado −$500


def test_overrides_no_recortan_sl_profundo():
    """_overrides_to_levers pasa el sl_usd del operador SIN recorte (no lo capa
    al MAE): b_pts = usd/ppt tal cual."""
    base = {"suelo_mae_p95_ganadoras": None, "backstop_usd": 500.0,
            "b_pts": 10.0, "tp_por_lado_atr": {}, "ladder": {},
            "lado": None, "breakeven": {}}
    lev = mrl._overrides_to_levers(base, {"sl_usd": 99000.0}, atr_med=8.0,
                                   ppt=_PPT)
    assert lev["backstop_usd"] == 99000.0
    assert lev["b_pts"] == pytest.approx(99000.0 / _PPT)


# --- inyección al render: units del catálogo + backstop vivo de la config ---

_STUDY_6J = {
    "version": 3, "fecha": "2026-07-18", "degradado": False,
    "degradado_motivo": None, "usd_por_punto": 12500000.0,
    "cancel_after_s": 3600, "avisos": [],
    "contencion": {"confiable": True, "pct": 100.0, "umbral_pct": 80.0},
    "dashboard": {
        "pv": 12500000.0, "n": 5, "recon_ok": 5, "fragile": False,
        "notes": [], "ref_price": 0.0067, "mfe_max": 500.0, "mae_min": -965.0,
        "trades": [], "base": {}, "config": {},
        "reco": {"alloc": [10, 0, 0], "sl_usd": 2000.0, "zones": [],
                 "days": []},
        "timestop": {}, "units": {"pv": 12500000.0, "show_pts": False,
                                  "atr_med_pts": 1e-06},
        "zones_partition": [],
    },
    "split": {"n_total": 5, "n_in_sample": 4, "n_oos": 1, "cutoff_ts": None,
              "n_trades_in": 4, "n_trades_oos": 1, "nota": ""},
    "tabla_a": [],
    "tabla_b": {"in_sample_optimo": {}, "oos_optimo": {}, "convergencia": {},
                "nota_oos": ""},
    "levers_in_sample": {},
    "levers_oos": {},
}


@pytest.fixture()
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_slr")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


async def _seed_6j(db: AsyncSession, tmp_path: Path,
                   monkeypatch: pytest.MonkeyPatch, sid: str = "6J5m_SLR"):
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol="6J",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=sid, mode="paper", dry_run=True,
                           traderspost_enabled=False,
                           pipeline_config_json={"backstop_points": 0.00016}))
    await db.commit()
    clave = rr.clave_de(sid, "6J")
    base = tmp_path / "MotorRiesgo" / clave
    (base / "runs").mkdir(parents=True)
    (base / "manifest.json").write_text(json.dumps({
        "version": 1, "activo": "6J", "codigo": clave.split("_", 1)[-1],
        "integrado": "2026-07-18", "trades": {"n": 5},
        "usd_por_punto": {"usado": 12500000.0}, "cuadre": {"ok": True},
    }), encoding="utf-8")
    (base / "runs" / "luxy_2026-07-18.json").write_text(
        json.dumps(_STUDY_6J), encoding="utf-8")
    return sid


@pytest.mark.asyncio
async def test_detalle_inyecta_units_catalogo_y_config_sl_usd(
        client: AsyncClient, db: AsyncSession, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch, _auth):
    """El render enriquece window.LUXY con el catálogo (es_fx/tick — el espejo
    JS de fmt_pts) y con el backstop VIVO en USD (fondo del slider): 6J con
    backstop_points 1.6e-4 y ppt 12.5M ⇒ config_sl_usd $2,000."""
    sid = await _seed_6j(db, tmp_path, monkeypatch)
    r = await client.get(f"/ui/strategies/{sid}")
    assert r.status_code == 200, r.text[:500]
    html = r.text
    assert '"es_fx": true' in html
    assert "5e-07" in html                        # tick del catálogo (6J)
    assert '"config_sl_usd": 2000.0' in html
    # el puente P1 (Jinja) también sale en ticks — jamás "0 pts" para 6J
    assert "320 ticks" in html


@pytest.mark.asyncio
async def test_detalle_indice_no_marca_fx(
        client: AsyncClient, db: AsyncSession, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch, _auth):
    """ES (índice): es_fx false — la leyenda sigue en pts (regresión GC/ES)."""
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    sid = "ES5m_SLR"
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol="ES",
                    status="paper", enabled=True))
    await db.commit()
    clave = rr.clave_de(sid, "ES")
    base = tmp_path / "MotorRiesgo" / clave
    (base / "runs").mkdir(parents=True)
    (base / "manifest.json").write_text(json.dumps({
        "version": 1, "activo": "ES", "codigo": clave.split("_", 1)[-1],
        "integrado": "2026-07-18", "trades": {"n": 5},
        "usd_por_punto": {"usado": 50.0}, "cuadre": {"ok": True},
    }), encoding="utf-8")
    study = json.loads(json.dumps(_STUDY_6J))
    study["usd_por_punto"] = 50.0
    study["dashboard"]["pv"] = 50.0
    study["dashboard"]["units"] = {"pv": 50.0, "show_pts": True,
                                   "atr_med_pts": 8.0}
    (base / "runs" / "luxy_2026-07-18.json").write_text(
        json.dumps(study), encoding="utf-8")
    r = await client.get(f"/ui/strategies/{sid}")
    assert r.status_code == 200
    assert '"es_fx": false' in r.text
