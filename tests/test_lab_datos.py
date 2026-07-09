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
import json
import os
import sys
import time
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_lab as routes_lab
import app.web.routes_riesgo as rr
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.web import manifest_store
from scripts.lab_analyze import feature_rows
from tests.test_lab_b6 import (
    _CSV_OK,
    _seed_es_manifest,
    _write_cache,
    _write_manifest,
)
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


# ===========================================================================
# LOTE LAB-2 — compartir/descargar + eliminar (espejo v2-D)
# ===========================================================================

def _rows() -> list[dict]:
    return feature_rows(_mk_trades(20))


@pytest.mark.asyncio
async def test_csv_download_y_traversal(client: AsyncClient, dirs: Path):
    """Descarga el CSV vigente; un strategy con traversal → 400 (nunca sirve
    fuera de ListaDeOperaciones)."""
    _seed_es_manifest(dirs)                       # Lux_ES1!_x.csv en el manifest
    r = await client.get("/ui/lab/csv", params={"strategy": "ES5m_ConfStrong"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "ES5m_ConfStrong" in r.headers.get("content-disposition", "")
    assert "Trade number" in r.text               # el contenido real del CSV

    bad = await client.get("/ui/lab/csv",
                           params={"strategy": "../../etc/passwd"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_enriched_download(client: AsyncClient, dirs: Path):
    _seed_es_manifest(dirs)
    # sin enriched → 404
    r0 = await client.get("/ui/lab/csv",
                          params={"strategy": "ES5m_ConfStrong", "kind": "enriched"})
    assert r0.status_code == 404
    # el motor generó enriched.csv en su carpeta <clave>
    clave = rr.clave_de("ES5m_ConfStrong", "ES")
    mdir = dirs / "MotorRiesgo" / clave
    mdir.mkdir(parents=True)
    (mdir / "enriched.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    r = await client.get("/ui/lab/csv",
                         params={"strategy": "ES5m_ConfStrong", "kind": "enriched"})
    assert r.status_code == 200
    assert "enriched" in r.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_delete_datos_upload_conserva_manifest(client: AsyncClient, dirs: Path):
    """Elimina caché + CSV upload_*, PERO conserva la entrada del manifest."""
    csv = dirs / "ListaDeOperaciones" / "upload_ES5m_X_1.csv"
    csv.write_text(_CSV_OK, encoding="utf-8")
    _write_manifest(dirs, {"ES5m_X": {
        "instrument": "ES", "csv": "ListaDeOperaciones/upload_ES5m_X_1.csv",
        "confirmed": True}})
    _write_cache(dirs, _rows(), "ES5m_X")
    r = await client.delete("/ui/lab/datos", params={"strategy": "ES5m_X"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["cache_borrada"] is True and j["csv_borrado"] is True
    assert not (dirs / "REPORTES" / "lab_features_ES5m_X.json").exists()
    assert not csv.exists()
    entries = json.loads((dirs / "REPORTES" / "lab_manifest.json")
                         .read_text(encoding="utf-8"))["entries"]
    assert "ES5m_X" in entries                     # la estrategia se conserva


@pytest.mark.asyncio
async def test_delete_datos_no_toca_export_original(client: AsyncClient, dirs: Path):
    """Un export ORIGINAL del operador (no upload_*) jamás se borra; la caché sí."""
    csv = dirs / "ListaDeOperaciones" / "Lux_ES1!_orig.csv"
    csv.write_text(_CSV_OK, encoding="utf-8")
    _write_manifest(dirs, {"ES5m_O": {
        "instrument": "ES", "csv": "ListaDeOperaciones/Lux_ES1!_orig.csv",
        "confirmed": True}})
    _write_cache(dirs, _rows(), "ES5m_O")
    r = await client.delete("/ui/lab/datos", params={"strategy": "ES5m_O"})
    j = r.json()
    assert j["cache_borrada"] is True and j["csv_borrado"] is False
    assert csv.exists()                            # export original intacto
    assert not (dirs / "REPORTES" / "lab_features_ES5m_O.json").exists()


@pytest.mark.asyncio
async def test_riesgo_delete_limpia_cache_lab(client: AsyncClient, dirs: Path):
    """Eliminar la estrategia desde Riesgo (v2-D) también limpia la caché del
    Lab (ya no queda huérfana)."""
    _write_manifest(dirs, {"ES5m_D": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv", "confirmed": True}})
    _write_cache(dirs, _rows(), "ES5m_D")
    r = await client.delete("/ui/riesgo/estrategia", params={"strategy": "ES5m_D"})
    assert r.status_code == 200, r.text
    assert r.json()["lab_cache_borrada"] is True
    assert not (dirs / "REPORTES" / "lab_features_ES5m_D.json").exists()
    entries = json.loads((dirs / "REPORTES" / "lab_manifest.json")
                         .read_text(encoding="utf-8"))["entries"]
    assert "ES5m_D" not in entries


@pytest.mark.asyncio
async def test_riesgo_rename_mueve_cache_lab(client: AsyncClient, dirs: Path):
    """Renombrar en Riesgo mueve también la caché del Lab."""
    _write_manifest(dirs, {"ES5m_A": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv", "confirmed": True}})
    _write_cache(dirs, _rows(), "ES5m_A")
    r = await client.post("/ui/riesgo/estrategia/renombrar",
                          json={"strategy": "ES5m_A", "nuevo_id": "ES5m_B"})
    assert r.status_code == 200, r.text
    assert r.json()["lab_cache_movida"] is True
    assert not (dirs / "REPORTES" / "lab_features_ES5m_A.json").exists()
    assert (dirs / "REPORTES" / "lab_features_ES5m_B.json").exists()


@pytest.mark.asyncio
async def test_cta_nueva_estrategia_render(client: AsyncClient, dirs: Path):
    _seed_es_manifest(dirs)
    html = (await client.get("/ui/lab?strategy=ES5m_ConfStrong")).text
    assert "nueva estrategia" in html and "/ui/riesgo" in html


@pytest.mark.asyncio
async def test_live_config_read_only_render(client: AsyncClient, dirs: Path,
                                            db: AsyncSession):
    """§4-bis — la config VIVA (filtros/régimen) + link, rotulada informativo."""
    _seed_es_manifest(dirs)
    db.add(Strategy(strategy_id="ES5m_ConfStrong", name="x", asset_symbol="ES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="ES5m_ConfStrong", pipeline_config_json={
        "filters": {"volume_relative": {"enabled": True, "weight": 30}},
        "regime": {"enabled": True, "timeframe": "1h",
                   "allowed_regimes": ["trending_bull"]}}))
    await db.commit()
    html = (await client.get("/ui/lab?strategy=ES5m_ConfStrong")).text
    assert "config VIVA" in html
    assert "volume_relative" in html
    assert "informativo" in html
    assert "/ui/strategies/ES5m_ConfStrong" in html   # link ⚙ config viva →
