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
