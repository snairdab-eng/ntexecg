"""LOTE LAB-1 — "datos conectados": frescura + fuente única del manifest.

Candados verificados:
  - upload del Lab ENCADENA el recalc de esa llave (2º plano, JOBS); opt-out.
  - upload de Riesgo ENCADENA el recalc del Lab para la estrategia (un solo
    upload deja las DOS pestañas frescas).
  - estrategia del manifest SIN caché → ficha de datos con botón "Generar
    caché ahora" (no el comando de consola).
  - caché stale → banner ámbar FUERTE con botón "Recalcular" (no solo texto).
  - manifest_store: el lock por estrategia serializa dos subidas concurrentes
    (asyncio.gather) — ninguna pisa a la otra.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

import app.web.routes_lab as routes_lab
import app.web.routes_riesgo as rr
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.web import manifest_store
from scripts.lab_analyze import feature_rows
from tests.test_lab_b6 import _CSV_OK, _seed_es_manifest, _write_manifest
from tests.test_lab_ui import _mk_trades

# recalc instantáneo (sin subproceso real de scripts.lab_analyze): el job se
# encola y termina "done" sin tocar el repo real.
_FAST_RECALC = lambda key, is_strategy: [sys.executable, "-c", "print('ok')"]


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lab_datos")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "REPORTES").mkdir()
    (tmp_path / "ListaDeOperaciones").mkdir()
    (tmp_path / "MotorRiesgo").mkdir()
    monkeypatch.setattr(routes_lab, "LAB_DIR", tmp_path / "REPORTES")
    monkeypatch.setattr(routes_lab, "TRADES_DIR", tmp_path / "ListaDeOperaciones")
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    monkeypatch.setattr(rr, "TRADES_DIR", tmp_path / "ListaDeOperaciones")
    routes_lab.JOBS.clear()
    rr.JOBS.clear()
    manifest_store._INTEGRAR_LOCKS.clear()
    return tmp_path


# ---------------------------------------------------------------------------
# Upload → recalc encadenado (opt-out)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lab_upload_encola_recalc(client: AsyncClient, dirs: Path,
                                        monkeypatch: pytest.MonkeyPatch):
    """Subir un CSV en el Lab deja la caché regenerándose sin un segundo clic:
    el recalc de esa llave queda ENCOLADO (JOBS) en 2º plano."""
    _seed_es_manifest(dirs)
    monkeypatch.setattr(routes_lab, "_recalc_cmd", _FAST_RECALC)

    r = await client.post("/ui/lab/upload",
                          data={"strategy": "ES5m_ConfStrong"},
                          files={"file": ("nuevo.csv", _CSV_OK.encode("utf-8"),
                                          "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["recalc"] == "running"
    assert "ES5m_ConfStrong" in routes_lab.JOBS          # job encolado
    await routes_lab.JOBS["ES5m_ConfStrong"]["task"]
    assert routes_lab.JOBS["ES5m_ConfStrong"]["status"] == "done"


@pytest.mark.asyncio
async def test_lab_upload_optout_no_encola(client: AsyncClient, dirs: Path,
                                           monkeypatch: pytest.MonkeyPatch):
    """recalc=false → el upload NO encola nada (opt-out explícito)."""
    _seed_es_manifest(dirs)
    monkeypatch.setattr(routes_lab, "_recalc_cmd", _FAST_RECALC)

    r = await client.post("/ui/lab/upload",
                          data={"strategy": "ES5m_ConfStrong", "recalc": "false"},
                          files={"file": ("nuevo.csv", _CSV_OK.encode("utf-8"),
                                          "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["recalc"] == "skipped"
    assert "ES5m_ConfStrong" not in routes_lab.JOBS


@pytest.mark.asyncio
async def test_riesgo_upload_encola_recalc_lab(client: AsyncClient, dirs: Path,
                                               monkeypatch: pytest.MonkeyPatch):
    """Un solo upload en Riesgo deja las DOS pestañas frescas: integra el
    master del Motor Y encola el recalc del Lab para esa estrategia."""
    _write_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})

    async def _fake_motor(cmd):
        return 0, "ok"
    monkeypatch.setattr(rr, "_run_motor", _fake_motor)
    monkeypatch.setattr(routes_lab, "_recalc_cmd", _FAST_RECALC)

    r = await client.post(
        "/ui/riesgo/upload", data={"strategy": "ES5m_Test"},
        files={"file": ("Lux_CME_MINI_ES1!_2026-07-04_ab.csv",
                        _CSV_OK.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["lab_recalc"] == "running"
    assert "ES5m_Test" in routes_lab.JOBS                # recalc del LAB encolado
    await routes_lab.JOBS["ES5m_Test"]["task"]
    assert routes_lab.JOBS["ES5m_Test"]["status"] == "done"


# ---------------------------------------------------------------------------
# Estrategia sin caché ≠ error críptico (ficha + botón) / identidad + stale
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_page_sin_cache_muestra_boton(client: AsyncClient, dirs: Path):
    """Llave del manifest sin caché → ficha de datos + botón 'Generar caché
    ahora' (dispara el mismo recalc), NO el comando de consola."""
    _seed_es_manifest(dirs)                    # ES5m_ConfNormal queda SIN caché
    r = await client.get("/ui/lab?strategy=ES5m_ConfNormal")
    assert r.status_code == 200
    assert "Generar caché ahora" in r.text
    assert "labGen(" in r.text                 # botón cableado al job
    assert "Lux_ES1!_x.csv" in r.text          # export (CSV) visible en la ficha
    assert routes_lab.REGEN_CMD not in r.text  # nada de banner de consola


@pytest.mark.asyncio
async def test_page_stale_banner_con_boton(client: AsyncClient, dirs: Path):
    """Caché stale (CSV más nuevo) → banner ámbar FUERTE con botón Recalcular
    (no solo texto) + identidad del dato (export) siempre visible."""
    _seed_es_manifest(dirs)                    # ES5m_ConfStrong CON caché
    cache = dirs / "REPORTES" / "lab_features_ES5m_ConfStrong.json"
    old = time.time() - 3600
    os.utime(cache, (old, old))
    # el CSV del manifest se vuelve MÁS NUEVO que la caché → stale
    csv = dirs / "ListaDeOperaciones" / "Lux_ES1!_x.csv"
    csv.write_text(_CSV_OK, encoding="utf-8")

    r = await client.get("/ui/lab?strategy=ES5m_ConfStrong")
    assert r.status_code == 200
    assert "ESTUDIO DESACTUALIZADO" in r.text
    assert "Recalcular" in r.text
    assert "labGen(" in r.text                 # el botón dispara el recalc
    assert "Export en uso (CSV)" in r.text     # identidad siempre visible
    assert "Lux_ES1!_x.csv" in r.text


# ---------------------------------------------------------------------------
# manifest_store — lock por estrategia (concurrencia real con asyncio.gather)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manifest_store_lock_serializa(dirs: Path):
    """Adversarial: dos escrituras CONCURRENTES de la misma clave bajo el lock
    compartido no se pisan. Sin lock, ambas leen el manifest vacío y cada una
    guarda SOLO su entrada (last-writer-wins) → se pierde una."""
    _write_manifest(dirs, {})

    async def añade(sid: str) -> None:
        async with manifest_store.lock_integrar("ES5m_Shared"):
            m = manifest_store.load_manifest()
            m[sid] = {"instrument": "ES", "csv": f"{sid}.csv", "confirmed": True}
            await asyncio.sleep(0.02)          # ventana para pisarse sin lock
            manifest_store.guardar_manifest(m)

    await asyncio.gather(añade("A"), añade("B"))
    final = manifest_store.load_manifest()
    assert set(final) == {"A", "B"}            # con lock: ninguna se pierde
