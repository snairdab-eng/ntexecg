"""FASE B6 — Laboratorio llaveado por ESTRATEGIA + gestión de datos.

B6.1: manifest CSV ↔ strategy_id (propuesta desde las estrategias existentes,
el operador confirma; siembra = estrategia primaria del símbolo, símbolo sin
estrategia → el instrumento como id, retrocompat). Cachés por estrategia,
selector agrupado por símbolo, paridad por estrategia.

B6.2: sección de datos (CSV actual + fecha), subida etiquetada con su
strategy_id (validada con el parser ANTES de aceptar) y recálculo en
SEGUNDO PLANO (job async, sin recomputo pesado en el hilo de la petición).
Único punto de escritura del visor; sin tocar dispatch/config/TradersPost.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

import app.web.routes_lab as routes_lab
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.services.lab_metrics import baseline_from_rows, lift_from_rows
from scripts.lab_analyze import feature_rows
from tests.test_lab_ui import _mk_trades

# CSV mínimo con el formato REAL de LuxAlgo (BOM, Salida antes que Entrada)
_CSV_OK = "﻿" + """Trade number,Tipo,Fecha y hora,Señal,Precio USD,Tamaño (cant.),Tamaño de la posición (valor),PyG netas USD,PyG netas %,Desviación favorable USD,Desviación favorable %,Desviación adversa USD,Desviación adversa %,PyG acumuladas USD,PyG acumuladas %
1,Salida en largo,2026-03-16 14:30,Scripted Exit All,6711.5,1,335150,425,0.13,575,0.17,-487.5,-0.15,425,4.25
1,Entrada en largo,2026-03-16 13:10,Scripted Long,6703,1,335150,425,0.13,575,0.17,-487.5,-0.15,425,4.25
"""


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lab_b6")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def lab_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lab = tmp_path / "REPORTES"
    trades = tmp_path / "ListaDeOperaciones"
    lab.mkdir()
    trades.mkdir()
    monkeypatch.setattr(routes_lab, "LAB_DIR", lab)
    monkeypatch.setattr(routes_lab, "TRADES_DIR", trades)
    routes_lab.JOBS.clear()
    return tmp_path


def _write_manifest(lab_dirs: Path, entries: dict) -> Path:
    p = lab_dirs / "REPORTES" / "lab_manifest.json"
    p.write_text(json.dumps({"version": 1, "entries": entries}),
                 encoding="utf-8")
    return p


def _write_cache(lab_dirs: Path, rows: list[dict], key: str) -> Path:
    p = lab_dirs / "REPORTES" / f"lab_features_{key}.json"
    p.write_text(json.dumps({
        "meta": {"instrument": "ES", "strategy_id": key, "n_trades": len(rows)},
        "rows": rows}), encoding="utf-8")
    return p


def _seed_es_manifest(lab_dirs: Path) -> list[dict]:
    csv = lab_dirs / "ListaDeOperaciones" / "Lux_ES1!_x.csv"
    csv.write_text(_CSV_OK, encoding="utf-8")
    _write_manifest(lab_dirs, {
        "ES5m_ConfStrong": {"instrument": "ES",
                            "csv": "ListaDeOperaciones/Lux_ES1!_x.csv",
                            "confirmed": True},
        "ES5m_ConfNormal": {"instrument": "ES",
                            "csv": "ListaDeOperaciones/Lux_ES1!_x.csv",
                            "confirmed": True},
        "GC5m_Contra": {"instrument": "GC",
                        "csv": "ListaDeOperaciones/Lux_GC1!_x.csv",
                        "confirmed": False},
    })
    rows = feature_rows(_mk_trades(20))
    _write_cache(lab_dirs, rows, "ES5m_ConfStrong")
    return rows


# ---------------------------------------------------------------------------
# B6.1 — propuesta del manifest (pura, sin DB)
# ---------------------------------------------------------------------------

def test_propose_entries_primary_and_fallback():
    """Cada CSV → la estrategia PRIMARIA de su símbolo (primera alfabética;
    el operador puede cambiarla antes de confirmar); símbolo sin estrategia
    → el instrumento como id (retrocompat CL/YM)."""
    from scripts.lab_manifest import propose_entries

    csvs = ["ListaDeOperaciones/Lux_ES1!_a.csv",
            "ListaDeOperaciones/Lux_CL1!_a.csv"]
    strategies = [("ES5m_ConfStrong_TSR", "MES"),
                  ("ES5m_ConfNormal_TC", "MES"),
                  ("GC5m_Contra", "MGC")]      # GC sin CSV → no aparece
    entries = propose_entries(csvs, strategies)
    assert set(entries) == {"ES5m_ConfNormal_TC", "CL"}
    es = entries["ES5m_ConfNormal_TC"]
    assert es["instrument"] == "ES" and es["confirmed"] is False
    assert es["csv"].endswith("Lux_ES1!_a.csv")
    assert es["candidates"] == ["ES5m_ConfNormal_TC", "ES5m_ConfStrong_TSR"]
    assert entries["CL"]["instrument"] == "CL"


def test_merge_proposal_respects_confirmed():
    """La propuesta NO pisa entradas confirmadas por el operador (salvo
    --force)."""
    from scripts.lab_manifest import merge_proposal

    existing = {"ES5m_A": {"instrument": "ES", "csv": "viejo.csv",
                           "confirmed": True}}
    proposed = {"ES5m_A": {"instrument": "ES", "csv": "nuevo.csv",
                           "confirmed": False},
                "CL": {"instrument": "CL", "csv": "cl.csv",
                       "confirmed": False}}
    m = merge_proposal(existing, proposed)
    assert m["ES5m_A"]["csv"] == "viejo.csv"         # confirmada: intacta
    assert m["CL"]["csv"] == "cl.csv"                # nueva: entra
    m2 = merge_proposal(existing, proposed, force=True)
    assert m2["ES5m_A"]["csv"] == "nuevo.csv"


def test_csv_instrument_parse():
    from scripts.lab_manifest import csv_instrument

    assert csv_instrument("Lux_..._CME_MINI_NQ1!_2026-06-27_b65.csv") == "NQ"
    assert csv_instrument("Lux_6J1!_x.csv") == "6J"
    assert csv_instrument("cualquiera.csv") is None


# ---------------------------------------------------------------------------
# B6.1 — visor por estrategia (selector agrupado, paridad, retrocompat)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_page_selector_grouped_by_symbol(client: AsyncClient, lab_dirs: Path):
    _seed_es_manifest(lab_dirs)
    r = await client.get("/ui/lab?strategy=ES5m_ConfStrong")
    assert r.status_code == 200
    assert "ES5m_ConfStrong" in r.text and "ES5m_ConfNormal" in r.text
    assert "GC5m_Contra" in r.text                 # agrupada bajo GC
    assert "strategy=ES5m_ConfNormal" in r.text    # links por estrategia


@pytest.mark.asyncio
async def test_aggregate_parity_per_strategy(client: AsyncClient, lab_dirs: Path):
    """Paridad POR ESTRATEGIA: el endpoint con strategy= devuelve lo del
    núcleo sobre el cache de ESA estrategia."""
    rows = _seed_es_manifest(lab_dirs)
    r = await client.post("/ui/lab/aggregate", json={
        "strategy": "ES5m_ConfStrong", "subs": {"volume_relative": 60}})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["base"] == json.loads(json.dumps(baseline_from_rows(rows)))
    off = lift_from_rows(rows, {"subs": {"volume_relative": 60}})
    assert j["result"] == json.loads(json.dumps(off))


@pytest.mark.asyncio
async def test_instrument_retrocompat_and_invalid_keys(
    client: AsyncClient, lab_dirs: Path
):
    rows = feature_rows(_mk_trades(20))
    p = lab_dirs / "REPORTES" / "lab_features_ES.json"
    p.write_text(json.dumps({"meta": {"instrument": "ES"}, "rows": rows}),
                 encoding="utf-8")
    r = await client.post("/ui/lab/aggregate", json={"instrument": "ES"})
    assert r.status_code == 200                    # retrocompat sin manifest
    # llaves inválidas / traversal → 400
    for bad in ("../../etc", "ES5m_no_existe", "a/b"):
        r = await client.post("/ui/lab/aggregate", json={"strategy": bad})
        assert r.status_code == 400, bad


# ---------------------------------------------------------------------------
# B6.2 — datos: subida validada + recálculo en segundo plano
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_csv_valid_and_garbage(client: AsyncClient, lab_dirs: Path):
    _seed_es_manifest(lab_dirs)
    r = await client.post("/ui/lab/upload",
                          data={"strategy": "ES5m_ConfStrong"},
                          files={"file": ("nuevo.csv",
                                          _CSV_OK.encode("utf-8"),
                                          "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["n_trades"] == 1
    saved = Path(lab_dirs) / j["csv"]
    assert saved.exists()
    manifest = json.loads((lab_dirs / "REPORTES" / "lab_manifest.json")
                          .read_text(encoding="utf-8"))
    assert manifest["entries"]["ES5m_ConfStrong"]["csv"] == j["csv"]

    # basura: se valida con el PARSER antes de aceptar — ni archivo ni manifest
    r = await client.post("/ui/lab/upload",
                          data={"strategy": "ES5m_ConfStrong"},
                          files={"file": ("malo.csv", b"no,es,luxalgo",
                                          "text/csv")})
    assert r.status_code == 400
    manifest2 = json.loads((lab_dirs / "REPORTES" / "lab_manifest.json")
                           .read_text(encoding="utf-8"))
    assert manifest2["entries"]["ES5m_ConfStrong"]["csv"] == j["csv"]
    # estrategia fuera del manifest → 400
    r = await client.post("/ui/lab/upload", data={"strategy": "nope"},
                          files={"file": ("x.csv", _CSV_OK.encode(), "text/csv")})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_recalc_background_job(client: AsyncClient, lab_dirs: Path,
                                     monkeypatch: pytest.MonkeyPatch):
    """El recálculo corre en SEGUNDO PLANO (subproceso), publica progreso y
    rechaza un segundo job concurrente de la misma estrategia."""
    _seed_es_manifest(lab_dirs)
    import sys
    monkeypatch.setattr(
        routes_lab, "_recalc_cmd",
        lambda key, is_strategy: [sys.executable, "-c",
                                  "import time; time.sleep(0.4); print('ok')"])

    r = await client.post("/ui/lab/recalc", json={"strategy": "ES5m_ConfStrong"})
    assert r.status_code == 202, r.text
    # concurrente → 409
    r2 = await client.post("/ui/lab/recalc", json={"strategy": "ES5m_ConfStrong"})
    assert r2.status_code == 409
    st = await client.get("/ui/lab/recalc/status?strategy=ES5m_ConfStrong")
    assert st.json()["status"] == "running"
    await routes_lab.JOBS["ES5m_ConfStrong"]["task"]
    st = await client.get("/ui/lab/recalc/status?strategy=ES5m_ConfStrong")
    j = st.json()
    assert j["status"] == "done" and "ok" in j["tail"]
    # y un comando que falla queda como error (silencio ≠ éxito)
    monkeypatch.setattr(
        routes_lab, "_recalc_cmd",
        lambda key, is_strategy: [sys.executable, "-c",
                                  "raise SystemExit('boom')"])
    await client.post("/ui/lab/recalc", json={"strategy": "ES5m_ConfStrong"})
    await routes_lab.JOBS["ES5m_ConfStrong"]["task"]
    st = await client.get("/ui/lab/recalc/status?strategy=ES5m_ConfStrong")
    assert st.json()["status"] == "error"


@pytest.mark.asyncio
async def test_page_datos_section(client: AsyncClient, lab_dirs: Path):
    _seed_es_manifest(lab_dirs)
    r = await client.get("/ui/lab?strategy=ES5m_ConfStrong")
    assert "Datos por estrategia" in r.text
    assert "recalcular" in r.text.lower()
    assert "/ui/lab/upload" in r.text
    assert "Lux_ES1!_x.csv" in r.text              # CSV actual visible
