"""LAB-3 — reconciliación Lab ↔ Motor (auditoría de números).

Candados:
  - reconcile_one (pura): coincide cuando n/cobertura/net alinean; difiere y
    lista el detalle cuando no; el filtro de universo ATR (atr_pct=None) se
    reporta aparte y NO rompe la coincidencia (diferencia esperada);
  - build_report clasifica coincide / difiere / sin_master / sin_cache;
  - la línea en la ficha del Lab aparece en ambos estados.
"""
import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_lab as routes_lab
import app.web.routes_riesgo as rr
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from scripts.lab_motor_reconcile import build_report, reconcile_one


def _motor(n: int, net: float, desde: str, hasta: str) -> dict:
    return {"trades": {"n": n, "desde": desde, "hasta": hasta},
            "linea_base_usd": {"net_usd": net}}


def _lab_row(entry_ts: str, pnl_usd: float, atr_pct: float | None = 1.0,
             in_sample: bool = True) -> dict:
    return {"entry_ts": entry_ts, "pnl_usd": pnl_usd,
            "pnl_pct": pnl_usd / 100.0, "mae_pct": 0.0, "mae_atr": 0.0,
            "in_sample": in_sample, "atr_pct": atr_pct}


def _rows_ok() -> list[dict]:
    return [_lab_row("2026-03-16T12:45:00", 100.0),
            _lab_row("2026-05-01T10:00:00", 100.0, in_sample=False),
            _lab_row("2026-06-26T03:35:00", 100.0, in_sample=False)]


# ── reconcile_one (pura) ─────────────────────────────────────────────────

def test_reconcile_coincide() -> None:
    m = _motor(3, 300.0, "2026-03-16T00:00:00", "2026-06-26T23:59:00")
    r = reconcile_one(_rows_ok(), m)
    assert r["coincide"] is True
    assert r["detail"] == []
    assert r["lab_n"] == 3 and r["lab_net"] == 300.0
    assert r["atr_filtered"] == 0


def test_reconcile_atr_filtrado_es_esperado_no_rompe() -> None:
    """Un trade sin cobertura ATR (atr_pct=None) se cuenta aparte pero, si n
    crudo y net siguen alineados con el master, coincide."""
    rows = _rows_ok() + [_lab_row("2026-04-01T09:00:00", 0.0, atr_pct=None)]
    m = _motor(4, 300.0, "2026-03-16T00:00:00", "2026-06-26T23:59:00")
    r = reconcile_one(rows, m)
    assert r["coincide"] is True
    assert r["atr_filtered"] == 1


def test_reconcile_difiere_lista_detalle() -> None:
    rows = _rows_ok()                       # n=3, net=300, hasta 06-26
    m = _motor(120, 28175.0, "2026-03-24T00:00:00", "2026-07-03T00:00:00")
    r = reconcile_one(rows, m)
    assert r["coincide"] is False
    joined = "; ".join(r["detail"])
    assert "n 3 vs master 120" in joined
    assert "net" in joined and "cobertura" in joined


# ── build_report ─────────────────────────────────────────────────────────

def test_build_report_clasifica() -> None:
    manifest = {
        "ES5m_A": {"instrument": "ES", "csv": "x.csv"},   # coincide
        "NQ5m_B": {"instrument": "NQ", "csv": "y.csv"},   # difiere
        "GC5m_C": {"instrument": "GC", "csv": "z.csv"},   # sin master
        "CL5m_D": {"instrument": "CL", "csv": "w.csv"},   # sin caché
    }
    caches = {
        "ES5m_A": (_rows_ok(), {}),
        "NQ5m_B": ([_lab_row("2026-01-01T00:00:00", 999.0)], {}),
        "GC5m_C": (_rows_ok(), {}),
        # CL5m_D: sin caché
    }
    motors = {
        "ES_A": _motor(3, 300.0, "2026-03-16T00:00:00", "2026-06-26T23:59:00"),
        "NQ_B": _motor(3, 300.0, "2026-03-16T00:00:00", "2026-06-26T23:59:00"),
        "CL_D": _motor(3, 300.0, "2026-03-16T00:00:00", "2026-06-26T23:59:00"),
        # GC_C: sin master
    }
    rows = build_report(
        manifest,
        load_cache=lambda k: caches.get(k),
        motor_manifest_fn=lambda c: motors.get(c),
        clave_fn=lambda k, inst: f"{inst}_{k.split('5m_')[1]}",
    )
    by_key = {r["key"]: r["status"] for r in rows}
    assert by_key == {"ES5m_A": "coincide", "NQ5m_B": "difiere",
                      "GC5m_C": "sin_master", "CL5m_D": "sin_cache"}


# ── línea en la ficha del Lab ────────────────────────────────────────────

KEY = "ES5m_Recon"


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_recon")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def lab_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "REPORTES").mkdir()
    (tmp_path / "MotorRiesgo").mkdir()
    monkeypatch.setattr(routes_lab, "LAB_DIR", tmp_path / "REPORTES")
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    routes_lab.JOBS.clear()
    return tmp_path


def _seed_lab_and_motor(lab_dirs: Path, rows: list[dict],
                        motor: dict) -> None:
    (lab_dirs / "REPORTES" / "lab_manifest.json").write_text(
        json.dumps({"version": 1, "entries": {
            KEY: {"instrument": "ES", "csv": "ListaDeOperaciones/x.csv"}}}),
        encoding="utf-8")
    (lab_dirs / "REPORTES" / f"lab_features_{KEY}.json").write_text(
        json.dumps({"meta": {"instrument": "ES"}, "rows": rows}),
        encoding="utf-8")
    clave = rr.clave_de(KEY, "ES")
    (lab_dirs / "MotorRiesgo" / clave).mkdir()
    (lab_dirs / "MotorRiesgo" / clave / "manifest.json").write_text(
        json.dumps(motor), encoding="utf-8")


@pytest.mark.asyncio
async def test_ficha_reconciliacion_coincide(
    client: AsyncClient, db: AsyncSession, lab_dirs: Path
) -> None:
    _seed_lab_and_motor(
        lab_dirs, _rows_ok(),
        _motor(3, 300.0, "2026-03-16T00:00:00", "2026-06-26T23:59:00"))
    html = (await client.get(f"/ui/lab?strategy={KEY}")).text
    assert "coincide con el master del Motor ✓" in html


@pytest.mark.asyncio
async def test_ficha_reconciliacion_difiere(
    client: AsyncClient, db: AsyncSession, lab_dirs: Path
) -> None:
    _seed_lab_and_motor(
        lab_dirs, _rows_ok(),
        _motor(120, 28175.0, "2026-03-24T00:00:00", "2026-07-03T00:00:00"))
    html = (await client.get(f"/ui/lab?strategy={KEY}")).text
    assert "difiere del master del Motor" in html
    assert "n 3 vs master 120" in html
