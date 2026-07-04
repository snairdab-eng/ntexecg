"""Laboratorio camino B — Fase B1: visor read-only + PARIDAD UI ↔ reporte.

Candados verificados:
  - paridad exacta: el endpoint agrega con las MISMAS funciones (lab_metrics)
    que el reporte offline — base y lift idénticos para los mismos datos;
  - caché ausente → 409 / banner con el comando de regeneración (no recompute);
  - caché vieja (CSV más nuevo) → flag stale;
  - guarda anti-espejismo: out-of-sample n < 15 marcado;
  - formato legado del cache (lista pelada) sigue leyéndose.
"""
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient

import app.web.routes_lab as routes_lab
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.services.lab_metrics import baseline_from_rows, lift_from_rows
from scripts.lab_analyze import Trade, baseline, feature_rows

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lab_ui")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def lab_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lab = tmp_path / "REPORTES"
    trades = tmp_path / "ListaDeOperaciones"
    lab.mkdir()
    trades.mkdir()
    monkeypatch.setattr(routes_lab, "LAB_DIR", lab)
    monkeypatch.setattr(routes_lab, "TRADES_DIR", trades)
    return tmp_path


def _mk_trades(n: int = 20) -> list[Trade]:
    """Trades sintéticos con features completas (mitad ganan; 30% out)."""
    out: list[Trade] = []
    t0 = datetime(2026, 3, 16, 9, 0)
    for i in range(n):
        t = Trade(
            number=i + 1, side="long" if i % 2 == 0 else "short",
            entry_ts=t0 + timedelta(hours=6 * i), exit_ts=None,
            entry_price=100.0 + i, exit_price=None,
            pnl_usd=(50.0 if i % 2 == 0 else -30.0),
            pnl_pct=(0.5 if i % 2 == 0 else -0.3),
            mfe_pct=0.8, mae_pct=0.2 + (i % 5) * 0.1,
        )
        t.atr_entry = 1.0
        t.atr_pct = 0.5
        t.bar_close = 100.0 + i
        t.hour = (9 + i) % 24
        t.in_sample = i < int(n * 0.7)
        t.sub_volume = 0.9 if i % 2 == 0 else 0.3   # el umbral 60 deja ganadores
        t.sub_atr = 0.7
        t.sub_vwap = 0.5
        t.sub_time = 0.5
        t.regime_1h = "ranging"
        t.regime_4h = "trending_bull"
        t.ema_with = {"1h20": i % 2 == 0, "1h50": True,
                      "4h20": False, "4h50": None}
        out.append(t)
    return out


def _write_cache(lab_dirs: Path, rows: list[dict], instrument="ES",
                 legacy=False) -> Path:
    p = lab_dirs / "REPORTES" / f"lab_features_{instrument}.json"
    payload = rows if legacy else {
        "meta": {"instrument": instrument, "generated_at": "2026-07-03T10:00:00",
                 "n_trades": len(rows), "uncovered": 0,
                 "tz": {"offset_minutes": 0, "sanity": 0.93}},
        "rows": rows,
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# PARIDAD UI ↔ reporte (el criterio de aceptación de la Fase B1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aggregate_parity_with_offline_report(
    client: AsyncClient, lab_dirs: Path
):
    """El endpoint devuelve EXACTAMENTE lo que computa el camino offline para
    los mismos datos: base == scripts.lab_analyze.baseline(trades) y el lift
    de un filtro == lift_from_rows (la función que llena la tabla §5 del .md)."""
    trades = _mk_trades()
    rows = feature_rows(trades)
    _write_cache(lab_dirs, rows)

    r = await client.post("/ui/lab/aggregate", json={
        "instrument": "ES", "subs": {"volume_relative": 60}})
    assert r.status_code == 200, r.text
    j = r.json()

    offline_base = baseline(trades)                       # camino A
    offline_lift = lift_from_rows(rows, {"subs": {"volume_relative": 60}})
    assert j["base"] == json.loads(json.dumps(offline_base))
    assert j["result"] == json.loads(json.dumps(offline_lift))
    # el filtro deja solo los pares ganadores → PF/exp del kept conocidos
    assert j["result"]["in"]["wr"] == 100.0
    assert j["deltas"]["in"]["pf"] is None or j["deltas"]["in"]["pf"] >= 0


@pytest.mark.asyncio
async def test_lab_page_base_card_parity(client: AsyncClient, lab_dirs: Path):
    """La tarjeta de línea base del HTML muestra los valores del núcleo."""
    trades = _mk_trades()
    rows = feature_rows(trades)
    _write_cache(lab_dirs, rows)
    base = baseline_from_rows(rows)

    r = await client.get("/ui/lab?instrument=ES")
    assert r.status_code == 200
    for key in ("wr", "pf", "expectancy_pct", "net_usd"):
        v = base["total"][key]
        assert v is not None and str(v) in r.text, f"{key}={v} no está en la página"


# ---------------------------------------------------------------------------
# Candados: caché ausente / vieja / read-only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_cache_banner_and_409(client: AsyncClient, lab_dirs: Path):
    r = await client.get("/ui/lab?instrument=ES")
    assert r.status_code == 200
    assert "lab_analyze --all-summary" in r.text      # banner con el comando

    r = await client.get("/ui/lab/data?instrument=ES")
    assert r.status_code == 409
    assert "lab_analyze" in r.json()["regen_cmd"]

    r = await client.post("/ui/lab/aggregate", json={"instrument": "ES"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_stale_cache_flag(client: AsyncClient, lab_dirs: Path):
    rows = feature_rows(_mk_trades())
    cache = _write_cache(lab_dirs, rows)
    # CSV de trades MÁS NUEVO que la caché → stale
    csv = lab_dirs / "ListaDeOperaciones" / "Lux_X_ES1!_2026.csv"
    csv.write_text("x", encoding="utf-8")
    old = time.time() - 3600
    os.utime(cache, (old, old))

    r = await client.get("/ui/lab/data?instrument=ES")
    assert r.status_code == 200
    assert r.json()["meta"]["stale"] is True

    page = await client.get("/ui/lab?instrument=ES")
    assert "desactualizada" in page.text


@pytest.mark.asyncio
async def test_low_n_out_guard(client: AsyncClient, lab_dirs: Path):
    """Una selección con out-of-sample chico se marca como no confiable."""
    rows = feature_rows(_mk_trades(20))       # out = 6 trades < 15
    _write_cache(lab_dirs, rows)
    r = await client.post("/ui/lab/aggregate", json={"instrument": "ES"})
    assert r.status_code == 200
    assert r.json()["low_n_out"] is True


@pytest.mark.asyncio
async def test_legacy_list_cache_still_readable(client: AsyncClient, lab_dirs: Path):
    rows = feature_rows(_mk_trades())
    _write_cache(lab_dirs, rows, legacy=True)
    r = await client.get("/ui/lab/data?instrument=ES")
    assert r.status_code == 200
    assert r.json()["meta"]["n_trades"] == len(rows)


@pytest.mark.asyncio
async def test_invalid_instrument_rejected(client: AsyncClient, lab_dirs: Path):
    r = await client.get("/ui/lab/data?instrument=../../etc")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Fase B2 — selecciones de régimen/EMA, edge por hora y controles en la página
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aggregate_regime_selection_fail_open(client: AsyncClient, lab_dirs: Path):
    """Gate de régimen con semántica viva: los permitidos Y unknown pasan."""
    rows = feature_rows(_mk_trades(20))
    for i, r in enumerate(rows):            # 5 bull, 5 unknown, 10 ranging
        r["regime_1h"] = ("trending_bull" if i < 5
                          else "unknown" if i < 10 else "ranging")
    _write_cache(lab_dirs, rows)

    r = await client.post("/ui/lab/aggregate", json={
        "instrument": "ES",
        "regime": {"tf": "1h", "allowed": ["trending_bull"]}})
    assert r.status_code == 200
    j = r.json()
    kept = j["result"]["in"]["n"] + j["result"]["out"]["n"]
    assert kept == 10                        # 5 bull + 5 unknown (fail-open)


@pytest.mark.asyncio
async def test_aggregate_ema_selection_strict(client: AsyncClient, lab_dirs: Path):
    """EMA con-tendencia estricta: True pasa; False y None se excluyen."""
    rows = feature_rows(_mk_trades(20))     # 1h20 True en pares, 4h50 None
    _write_cache(lab_dirs, rows)
    r = await client.post("/ui/lab/aggregate",
                          json={"instrument": "ES", "ema": ["1h20"]})
    kept = r.json()["result"]
    assert kept["in"]["n"] + kept["out"]["n"] == 10       # solo los pares
    r = await client.post("/ui/lab/aggregate",
                          json={"instrument": "ES", "ema": ["4h50"]})
    kept = r.json()["result"]
    assert kept["in"]["n"] + kept["out"]["n"] == 0        # None nunca pasa


@pytest.mark.asyncio
async def test_hourly_parity_with_offline(client: AsyncClient, lab_dirs: Path):
    """El panel horario usa la MISMA función que el §3 del reporte."""
    from app.services.lab_metrics import hourly_from_rows
    from scripts.lab_analyze import hourly_edge

    trades = _mk_trades(20)
    rows = feature_rows(trades)
    assert hourly_from_rows(rows) == hourly_edge(trades)


# ---------------------------------------------------------------------------
# Fase B3 — cambia-desenlace (re-sim) + pullback
# ---------------------------------------------------------------------------

def _with_touch(rows: list[dict]) -> list[dict]:
    """Toques cacheados: SL toca a los 10 min con k≤2.5; TP a los 5 min con
    tp=3 (TP primero); grillas completas con None en lo no tocado."""
    for r in rows:
        r["t_sl_touch"] = {str(k): (10.0 if k <= 2.5 else None)
                           for k in (1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0)}
        r["t_tp_touch"] = {str(t): (5.0 if t == 3.0 else None)
                           for t in (3.0, 4.0, 6.0)}
        r["mae_pct"] = 2.0     # alcanza SL hasta k=4 (atr_pct=0.5)
        r["mfe_pct"] = 1.6     # alcanza TP 3.0 (1.5) pero no 4.0 (2.0)
    return rows


@pytest.mark.asyncio
async def test_resim_endpoint_parity_and_curves(client: AsyncClient, lab_dirs: Path):
    """El endpoint devuelve EXACTAMENTE resim_rows (núcleo del §2/§8/§9) y las
    curvas cuadran con los desenlaces."""
    from app.services.lab_metrics import equity_curve, resim_rows

    rows = _with_touch(feature_rows(_mk_trades(20)))
    _write_cache(lab_dirs, rows)

    r = await client.post("/ui/lab/resim",
                          json={"instrument": "ES", "sl_k": 2.5, "tp": 3.0})
    assert r.status_code == 200, r.text
    j = r.json()

    offline = resim_rows(rows, sl_k=2.5, tp=3.0)
    outcomes = offline.pop("outcomes")
    assert j["result"]["in"] == json.loads(json.dumps(offline["in"]))
    assert j["result"]["out"] == json.loads(json.dumps(offline["out"]))
    assert j["curves"]["resim"] == json.loads(json.dumps(equity_curve(outcomes)))
    assert len(j["curves"]["base"]) == len(j["curves"]["resim"]) == 20
    assert j["curves"]["split_idx"] == 14
    # ambos umbrales alcanzados y TP tocó primero (5m < 10m) → todo TP +1.5
    assert j["result"]["in"]["tp_pct"] == 100.0
    assert j["result"]["in"]["expectancy_pct"] == 1.5
    assert j["legacy_cache"] is False


@pytest.mark.asyncio
async def test_resim_ambiguous_same_bar_goes_sl(client: AsyncClient, lab_dirs: Path):
    rows = _with_touch(feature_rows(_mk_trades(20)))
    for r in rows:                      # mismo minuto = misma barra → SL
        r["t_tp_touch"]["3.0"] = 10.0
    _write_cache(lab_dirs, rows)
    r = await client.post("/ui/lab/resim",
                          json={"instrument": "ES", "sl_k": 2.5, "tp": 3.0})
    j = r.json()
    assert j["result"]["in"]["sl_pct"] == 100.0
    assert j["result"]["in"]["ambiguous"] == 14
    assert j["result"]["in"]["expectancy_pct"] == -1.25      # −k·atr% = −2.5·0.5


@pytest.mark.asyncio
async def test_resim_legacy_cache_flag_and_conservative(client: AsyncClient, lab_dirs: Path):
    """Cache sin toques (previo a B3): ambos alcanzados → SL (conservador) y
    se avisa legacy_cache para regenerar."""
    rows = feature_rows(_mk_trades(20))
    for r in rows:
        r["mae_pct"], r["mfe_pct"] = 2.0, 1.6
        r["t_sl_touch"] = r["t_tp_touch"] = None
    _write_cache(lab_dirs, rows)
    r = await client.post("/ui/lab/resim",
                          json={"instrument": "ES", "sl_k": 2.5, "tp": 3.0})
    j = r.json()
    assert j["legacy_cache"] is True
    assert j["result"]["in"]["sl_pct"] == 100.0


@pytest.mark.asyncio
async def test_resim_grid_validation(client: AsyncClient, lab_dirs: Path):
    rows = feature_rows(_mk_trades(20))
    _write_cache(lab_dirs, rows)
    assert (await client.post("/ui/lab/resim", json={
        "instrument": "ES", "sl_k": 2.7})).status_code == 400
    assert (await client.post("/ui/lab/resim", json={
        "instrument": "ES", "tp": 5.0})).status_code == 400
    assert (await client.post("/ui/lab/resim", json={
        "instrument": "ES"})).status_code == 400


@pytest.mark.asyncio
async def test_page_has_resim_and_pullback_panels(client: AsyncClient, lab_dirs: Path):
    rows = feature_rows(_mk_trades(20))
    p = lab_dirs / "REPORTES" / "lab_features_ES.json"
    payload = {
        "meta": {"instrument": "ES", "n_trades": len(rows),
                 "pullback_window_min": 180,
                 "pullback": {"0.75": {
                     "n_filled": 15, "fill_rate": 75.0, "t_med": 10.0,
                     "t_p90": 25.0, "cancel_after": 1560,
                     "filled_outcome": {"n": 15, "wr": 80.0, "pf": 1.7,
                                        "expectancy_pct": 0.07},
                     "unfilled_outcome": {"n": 5, "expectancy_pct": 0.1},
                     "filled_out_exp": -0.04}}},
        "rows": rows,
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    r = await client.get("/ui/lab?instrument=ES")
    assert r.status_code == 200
    assert "labResim(" in r.text and "/ui/lab/resim" in r.text
    assert "orden de toques intrabar" in r.text
    assert "una sola caducidad" in r.text                 # nota NX-17/NX-28
    assert "entry_reserve_timeout_seconds" in r.text
    assert "Cancel entry after" in r.text
    assert "1560" in r.text                               # cancel_after @0.75


@pytest.mark.asyncio
async def test_page_pullback_missing_hint(client: AsyncClient, lab_dirs: Path):
    rows = feature_rows(_mk_trades(20))
    _write_cache(lab_dirs, rows)      # cache sin agregado de pullback
    r = await client.get("/ui/lab?instrument=ES")
    assert "no trae el agregado de pullback" in r.text


# ---------------------------------------------------------------------------
# Fase B4 — veredicto (heat 1–10), rótulo ET y "mejor configuración" (OOS)
# ---------------------------------------------------------------------------

def test_heat_score_known_answers():
    """Escalones documentados del heat 1–10 (5 = neutro; ±0.05/0.25/0.5/1/2)."""
    from app.services.lab_metrics import heat_score

    assert heat_score(None) is None
    assert heat_score(0.0) == 5
    assert heat_score(0.05) == 6
    assert heat_score(0.3) == 7
    assert heat_score(0.6) == 8
    assert heat_score(1.2) == 9
    assert heat_score(2.5) == 10
    assert heat_score(-0.06) == 4
    assert heat_score(-0.3) == 4
    assert heat_score(-0.55) == 3
    assert heat_score(-1.5) == 2
    assert heat_score(-3.0) == 1


@pytest.mark.asyncio
async def test_aggregate_and_resim_include_verdict(client: AsyncClient, lab_dirs: Path):
    """B4.2 — el veredicto viene del SERVIDOR (paridad con lab_metrics.verdict;
    nada de métricas en JS) y marca si el PF out cruza 1.0."""
    from app.services.lab_metrics import deltas_vs_base, verdict

    rows = feature_rows(_mk_trades(20))
    _write_cache(lab_dirs, rows)

    r = await client.post("/ui/lab/aggregate", json={
        "instrument": "ES", "subs": {"atr_normalized": 50}})
    j = r.json()
    base = baseline_from_rows(rows)
    result = lift_from_rows(rows, {"subs": {"atr_normalized": 50},
                                   "regime": {}, "ema": []})
    off = verdict(result, deltas_vs_base(result, base))
    assert j["verdict"] == json.loads(json.dumps(off))
    # atr≥50 mantiene todo → Δ0 → neutro 5; PF out 1.67 ≥ 1 → sobrevive
    assert j["verdict"]["in"]["score"] == 5
    assert j["verdict"]["out"]["survives"] is True

    r2 = await client.post("/ui/lab/resim",
                           json={"instrument": "ES", "sl_k": 2.5})
    j2 = r2.json()
    assert set(j2["verdict"]) == {"in", "out"}
    assert "survives" in j2["verdict"]["out"]


@pytest.mark.asyncio
async def test_best_picks_oos_survivor_not_in_sample_mirage(
    client: AsyncClient, lab_dirs: Path
):
    """B4.3 (regla dura): el ganador se elige por OUT-of-sample. El espejismo
    (mejora ENORME in-sample pero empeora out) NO gana ni aparece."""
    trades = _mk_trades(20)
    for i, t in enumerate(trades):
        # volume ≥60: sobrevive DENTRO y FUERA (ganadores + 1 perdedor por bloque)
        t.sub_volume = 0.9 if (i % 2 == 0 or i in (1, 15)) else 0.3
        # atr ≥80: espejismo — dentro solo ganadores(+1), fuera solo perdedores
        t.sub_atr = (0.9 if ((i % 2 == 0 and i < 14) or i == 1
                             or (i % 2 == 1 and i >= 15)) else 0.3)
    rows = feature_rows(trades)
    _write_cache(lab_dirs, rows)

    r = await client.get("/ui/lab/best?instrument=ES")
    assert r.status_code == 200
    j = r.json()
    assert j["none_robust"] is False
    assert j["winner"]["label"].startswith("volume_relative")
    assert j["winner"]["selection"]["subs"]        # auto-aplicable por el visor
    labels = [s["label"] for s in j["survivors"]]
    assert all(not lbl.startswith("atr_normalized") for lbl in labels)


@pytest.mark.asyncio
async def test_best_none_robust_native_dominates(client: AsyncClient, lab_dirs: Path):
    """Sin superviviente robusto → explícito "nativo domina" (caso 6E/6J)."""
    rows = feature_rows(_mk_trades(20))
    _write_cache(lab_dirs, rows)
    r = await client.get("/ui/lab/best?instrument=ES")
    j = r.json()
    assert j["none_robust"] is True and j["winner"] is None
    assert j["survivors"] == []


def test_survivors_single_source_parity():
    """El reporte (phase2) y el visor (rows) comparten criterio Y grilla:
    mismos labels y deltas por ambas rutas."""
    from app.services.lab_metrics import (
        EMA_KEYS, REGIME_GATE_DEFS, SUB_NAMES, SUB_THRESHOLDS,
        oos_survivors_from_rows,
    )
    from scripts.lab_analyze import oos_survivors

    trades = _mk_trades(20)
    for i, t in enumerate(trades):
        t.sub_volume = 0.9 if (i % 2 == 0 or i in (1, 15)) else 0.3
    rows = feature_rows(trades)
    base = baseline_from_rows(rows)
    phase2 = {
        "subs": {name: {thr: lift_from_rows(rows, {"subs": {name: thr}})
                        for thr in SUB_THRESHOLDS} for name in SUB_NAMES},
        "regime_gates": {k: lift_from_rows(rows, {"regime": g})
                         for k, g in REGIME_GATE_DEFS},
        "ema_gates": {k: lift_from_rows(rows, {"ema": [k]}) for k in EMA_KEYS},
    }
    a = oos_survivors(base, phase2)
    b = oos_survivors_from_rows(rows, base)
    assert [(s["label"], s["d_in"], s["d_out"]) for s in a] == \
           [(s["label"], s["d_in"], s["d_out"]) for s in b]
    assert len(a) > 0


# ---------------------------------------------------------------------------
# B4.2 (revisado) — panel de DECISIÓN: vector completo + frase de tradeoff
# ---------------------------------------------------------------------------

def test_deltas_include_risk_vector():
    """El Δ contra base cubre el vector completo: PF, WR, exp, maxDD y net."""
    from app.services.lab_metrics import deltas_vs_base

    sel = {"in": {"pf": 1.5, "wr": 60.0, "expectancy_pct": 0.1,
                  "max_dd_pct": -2.0, "net_pct": 4.0},
           "out": {"pf": 1.2, "wr": 55.0, "expectancy_pct": 0.05,
                   "max_dd_pct": -1.0, "net_pct": 1.0}}
    base = {"in": {"pf": 1.3, "wr": 55.0, "expectancy_pct": 0.08,
                   "max_dd_pct": -3.5, "net_pct": 5.0},
            "out": {"pf": 1.0, "wr": 50.0, "expectancy_pct": 0.01,
                    "max_dd_pct": -2.0, "net_pct": 0.5}}
    d = deltas_vs_base(sel, base)
    assert d["in"]["max_dd_pct"] == 1.5          # −2.0 − (−3.5): DD mejora
    assert d["in"]["net_pct"] == -1.0
    assert d["out"]["max_dd_pct"] == 1.0


def test_tradeoff_read_deterministic_patterns():
    """B4.2 — la capa de interpretación: reglas DETERMINISTAS sobre el patrón
    de signos del Δ (en lab_metrics, no en JS). Los 4 mapeos del operador."""
    from app.services.lab_metrics import tradeoff_read

    # PF↓ + WR↑ + DD↓ (dd Δ>0 = drawdown menos profundo) → riesgo, no calidad
    t = tradeoff_read({"pf": -0.3, "wr": 5.0, "max_dd_pct": 1.2})
    assert t["verdict"] == "tradeoff_riesgo"
    assert "riesgo, no de calidad" in t["phrase"]
    # PF↑ + WR↓ → más volátil
    t = tradeoff_read({"pf": 0.4, "wr": -4.0, "max_dd_pct": -0.5})
    assert t["verdict"] == "volatil"
    assert "volátil" in t["phrase"]
    # PF↑ + WR↑ → mejor en todo
    t = tradeoff_read({"pf": 0.4, "wr": 3.0, "max_dd_pct": 0.5})
    assert t["verdict"] == "mejor"
    # PF↓ + WR↓ → peor en todo — descartar
    t = tradeoff_read({"pf": -0.4, "wr": -3.0, "max_dd_pct": -0.5})
    assert t["verdict"] == "peor"
    assert "descartar" in t["phrase"]
    # neutro (dentro de la tolerancia) y sin datos
    assert tradeoff_read({"pf": 0.01, "wr": 0.1,
                          "max_dd_pct": 0.0})["verdict"] == "neutro"
    assert tradeoff_read({"pf": None, "wr": 1.0,
                          "max_dd_pct": 0.0})["verdict"] == "sin_datos"


# ---------------------------------------------------------------------------
# B4.3 (revisado) — config default por RIESGO (sizing 1%), elegida por OUT
# ---------------------------------------------------------------------------

def _risk_row(pnl, mae_atr, pb: dict | None, in_sample=True) -> dict:
    return {"pnl_pct": pnl, "mae_pct": abs(pnl), "mfe_pct": abs(pnl) + 0.1,
            "atr_pct": 1.0, "mae_atr": mae_atr, "mfe_atr": 1.0,
            "in_sample": in_sample, "t_pb_touch": pb}


def test_risk_sized_outcomes_known_answer():
    """Modelo de sizing a riesgo fijo: tamaño tal que (todo llena y pega el
    stop) = −risk%. SL ANCLADO a la señal; pierna llena a mejor precio →
    pierde menos contra el stop y gana más en el ganador."""
    from app.services.lab_metrics import risk_sized_outcomes

    legs = ((0.0, 0.5), (0.5, 0.5))
    # ganador con pullback (la pierna 0.5 llena): worst = 7.75 → m = 1/7.75
    win = _risk_row(2.0, 0.6, {"0.5": 10.0})
    r = risk_sized_outcomes([win], 8.0, legs)
    assert r["in"]["expectancy_pct"] == round((0.5*2.0 + 0.5*2.5) / 7.75, 4)
    assert r["approx_fills"] is False
    # stopped con todo lleno → EXACTAMENTE −1% (el tope duro, por construcción)
    stop = _risk_row(-5.0, 9.0, {"0.5": 5.0})
    r = risk_sized_outcomes([stop], 8.0, legs)
    assert r["in"]["expectancy_pct"] == -1.0
    # ganador SIN pullback: la pierna no llena → solo media posición gana
    nofill = _risk_row(2.0, 0.1, {})
    r = risk_sized_outcomes([nofill], 8.0, legs)
    assert r["in"]["expectancy_pct"] == round(0.5 * 2.0 / 7.75, 4)
    # cache sin t_pb_touch (legado) → fallback mae_atr con flag approx
    legacy = _risk_row(2.0, 0.6, None)
    r = risk_sized_outcomes([legacy], 8.0, legs)
    assert r["approx_fills"] is True
    assert r["in"]["expectancy_pct"] == round((0.5*2.0 + 0.5*2.5) / 7.75, 4)


def test_default_config_picks_by_out_never_in_sample():
    """Regla dura B4.3: la config default se elige por OUT-of-sample. El
    escalonado que domina IN-SAMPLE (0.655%/trade @4×+0.75) NO gana si
    fuera de muestra las piernas no llenan en los ganadores."""
    from app.services.lab_metrics import default_config_study

    rows = []
    # in: 14 ganadores CON pullback somero (el escalonado luce espectacular)
    for _ in range(14):
        rows.append(_risk_row(2.0, 0.6, {"0.25": 5.0, "0.5": 10.0}))
    # out: 5 ganadores SIN pullback (pierna no llena → cede ganancia) + 1 stop
    for _ in range(5):
        rows.append(_risk_row(2.0, 0.1, {}, in_sample=False))
    rows.append(_risk_row(-5.0, 9.0,
                          {"0.25": 5.0, "0.5": 5.0, "0.75": 5.0},
                          in_sample=False))

    st = default_config_study(rows)
    assert st["none_viable"] is False
    rec = st["recommended"]
    # por OUT gana la entrada única @4× (0.25%/trade out) …
    assert rec["sl_k"] == 4.0
    assert len(rec["legs"]) == 1 and rec["legs"][0]["depth"] == 0.0
    assert rec["out"]["expectancy_pct"] == 0.25
    # … aunque por IN-SAMPLE ganaría el somero 0.75 (espejismo)
    best_in = max(st["candidates"],
                  key=lambda c: c["in"]["expectancy_pct"] or -9e9)
    assert len(best_in["legs"]) > 1 and best_in is not rec
    # costo visible: riesgo duro, peor pérdida topada a −1%, cesión vs nativo
    assert rec["cost"]["risk_pct"] == 1.0
    assert rec["cost"]["worst_account_pct"] >= -1.0
    assert "ceded_out_pct" in rec["cost"]
    # guarda n<15 visible
    assert rec["low_n_out"] is True and rec["out"]["n"] == 6


def test_default_config_none_viable():
    """Guarda innegociable: sin expectancy OOS positiva no se recomienda —
    reducir riesgo hasta volverla no-rentable derrota el propósito."""
    from app.services.lab_metrics import default_config_study

    rows = ([_risk_row(-1.0, 0.5, {}) for _ in range(14)]
            + [_risk_row(-1.0, 0.5, {}, in_sample=False) for _ in range(6)])
    st = default_config_study(rows)
    assert st["none_viable"] is True and st["recommended"] is None


@pytest.mark.asyncio
async def test_default_endpoint_and_tradeoff_in_responses(
    client: AsyncClient, lab_dirs: Path
):
    """GET /ui/lab/default sirve el estudio del núcleo; aggregate/resim
    incluyen la frase de tradeoff (paridad con lab_metrics)."""
    from app.services.lab_metrics import (default_config_study, deltas_vs_base,
                                          tradeoff_read)

    rows = feature_rows(_mk_trades(20))
    for r in rows:
        r["t_pb_touch"] = {"0.5": 10.0}
    _write_cache(lab_dirs, rows)

    r = await client.get("/ui/lab/default?instrument=ES")
    assert r.status_code == 200
    j = r.json()
    off = default_config_study(rows)
    assert j["none_viable"] == off["none_viable"]
    if not j["none_viable"]:
        assert j["recommended"]["label"] == off["recommended"]["label"]
        assert j["recommended"]["cost"]["risk_pct"] == 1.0

    r2 = await client.post("/ui/lab/aggregate", json={
        "instrument": "ES", "subs": {"volume_relative": 60}})
    j2 = r2.json()
    off_t = tradeoff_read(j2["deltas"]["out"])
    assert j2["tradeoff"]["out"] == json.loads(json.dumps(off_t))
    assert "phrase" in j2["tradeoff"]["in"]
    r3 = await client.post("/ui/lab/resim",
                           json={"instrument": "ES", "sl_k": 2.5})
    assert "tradeoff" in r3.json()


@pytest.mark.asyncio
async def test_page_decision_panel_and_default_card(client: AsyncClient, lab_dirs: Path):
    """B4.2/B4.3 (revisados): panel de decisión (frase del servidor, tarjeta
    de riesgo resaltada, KPI estilo Analytics) + tarjeta de config default."""
    rows = feature_rows(_mk_trades(20))
    _write_cache(lab_dirs, rows)
    r = await client.get("/ui/lab?instrument=ES")
    assert "Decisión — base → selección" in r.text
    assert "res.tradeoff.out.phrase" in r.text          # frase: viene del server
    assert "riesgo / cola" in r.text                    # LA tarjeta, resaltada
    assert "veredicto honesto" in r.text
    assert "Config DEFAULT recomendada" in r.text       # B4.3
    assert "/ui/lab/default" in r.text
    assert "tope duro 1%/trade" in r.text
    assert "lab-apply-sl" in r.text                     # aplicar SL al re-sim
    assert "piernas profundas prohibidas" in r.text


@pytest.mark.asyncio
async def test_page_b4_verdict_et_and_best_button(client: AsyncClient, lab_dirs: Path):
    rows = feature_rows(_mk_trades(20))
    _write_cache(lab_dirs, rows)
    r = await client.get("/ui/lab?instrument=ES")
    assert "todos los tiempos en ET (America/New_York)" in r.text     # B4.1
    assert "Mejor configuración (out-of-sample)" in r.text            # B4.3
    assert "/ui/lab/best" in r.text
    assert "nativo domina" in r.text                                  # caso vacío
    assert "veredicto honesto" in r.text                              # B4.2 out ★
    assert "heatColor" in r.text                                      # barra 1–10
    assert "<details>" in r.text and "detalle completo" in r.text     # base plegada


@pytest.mark.asyncio
async def test_page_has_interactive_panels_and_hourly(client: AsyncClient, lab_dirs: Path):
    rows = feature_rows(_mk_trades(20))
    _write_cache(lab_dirs, rows)
    r = await client.get("/ui/lab?instrument=ES")
    assert r.status_code == 200
    assert "labSel(" in r.text                       # componente Alpine
    assert "/ui/lab/aggregate" in r.text             # sin métricas en JS: fetch
    assert "volume_relative" in r.text and "4h50" in r.text
    assert "Edge por hora" in r.text
    assert "⚠" in r.text                             # buckets n bajo marcados
    assert "no confiable" in r.text                  # guarda n<15 visible
