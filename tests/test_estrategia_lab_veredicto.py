"""EST-2 — plomería del veredicto del Lab (SOLO LECTURA).

NOTA (Parte C): la VISIBILIDAD del veredicto por filtro en la ficha se retiró
junto con la sección de Filtros de calidad del Config. Lo que sigue bajo prueba:
  - el mapeo filtro de producción ↔ sub-score del Lab sigue correcto (unitario);
  - abrir la ficha NO muestra ya la evidencia EST-2 (sección retirada);
  - CERO escrituras: abrir la ficha no toca la caché ni el manifest del Lab.
"""
import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_lab as routes_lab
import app.web.routes_strategies as routes_strategies
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile

SID = "ES5m_LabTest"


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_est2")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture(autouse=True)
def _clear_verdict_cache() -> None:
    routes_strategies._LAB_VERDICT_CACHE.clear()


@pytest.fixture()
def lab_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "REPORTES").mkdir()
    (tmp_path / "ListaDeOperaciones").mkdir()
    monkeypatch.setattr(routes_lab, "LAB_DIR", tmp_path / "REPORTES")
    monkeypatch.setattr(routes_lab, "TRADES_DIR", tmp_path / "ListaDeOperaciones")
    routes_lab.JOBS.clear()
    return tmp_path


def _write_manifest(lab_dirs: Path, entries: dict) -> None:
    (lab_dirs / "REPORTES" / "lab_manifest.json").write_text(
        json.dumps({"version": 1, "entries": entries}), encoding="utf-8")


def _cache_file(lab_dirs: Path, key: str = SID) -> Path:
    return lab_dirs / "REPORTES" / f"lab_features_{key}.json"


def _write_cache(lab_dirs: Path, rows: list[dict], key: str = SID) -> Path:
    p = _cache_file(lab_dirs, key)
    p.write_text(json.dumps({"meta": {"instrument": "ES", "strategy_id": key},
                             "rows": rows}), encoding="utf-8")
    return p


def _row(pnl_pct: float, in_sample: bool, sub_volume: float,
         sub_other: float = 0.9) -> dict:
    return {
        "pnl_pct": pnl_pct, "pnl_usd": pnl_pct * 100.0,
        "in_sample": in_sample, "atr_pct": 1.0,
        "mae_pct": 0.0, "mfe_pct": 0.0, "mae_atr": 0.0, "mfe_atr": 0.0,
        "sub_volume": sub_volume, "sub_atr": sub_other,
        "sub_vwap": sub_other, "sub_time": sub_other,
    }


def _rows() -> list[dict]:
    out: list[dict] = []
    for ins in (True, False):
        out += [_row(2.0, ins, 0.9) for _ in range(3)]
        out += [_row(-1.0, ins, 0.1) for _ in range(2)]
        out += [_row(-1.0, ins, 0.9)]
    return out


async def _seed_strategy(db: AsyncSession) -> None:
    db.add(Strategy(strategy_id=SID, name=SID, asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=SID, mode="paper"))
    await db.commit()


# ── mapeo (unitario, sin DB) — la plomería del veredicto sigue viva ──────────

def test_mapeo_filtro_a_sub_del_lab() -> None:
    from app.services import lab_metrics as lm
    from app.services import quality_scorer as qs

    assert tuple(routes_strategies.FILTER_TO_LAB_SUB) == qs._NAMES
    for sub in routes_strategies.FILTER_TO_LAB_SUB.values():
        assert sub in lm._SUB_ATTR
        assert sub in lm.SUB_NAMES


# ── Parte C — la evidencia EST-2 ya NO se muestra en la ficha ────────────────

@pytest.mark.asyncio
async def test_veredicto_ya_no_se_muestra_en_la_ficha(
    client: AsyncClient, db: AsyncSession, lab_dirs: Path
) -> None:
    """Con caché válida y candidato real, la ficha NO expone la evidencia del
    Lab: la sección de Filtros de calidad (y con ella EST-2) se retiró."""
    await _seed_strategy(db)
    _write_manifest(lab_dirs, {SID: {"instrument": "ES",
                                     "csv": "ListaDeOperaciones/x.csv"}})
    _write_cache(lab_dirs, _rows())

    html = (await client.get(f"/ui/strategies/{SID}")).text
    assert "Evidencia informativa del Lab" not in html
    assert "Filtros de calidad — Nivel 4" not in html
    assert "candidato — valida antes de activar" not in html
    assert "caché vieja vs CSV" not in html


# ── cero escrituras (invariante conservado) ──────────────────────────────────

@pytest.mark.asyncio
async def test_ficha_no_escribe_en_el_lab(
    client: AsyncClient, db: AsyncSession, lab_dirs: Path
) -> None:
    await _seed_strategy(db)
    manifest_p = lab_dirs / "REPORTES" / "lab_manifest.json"
    _write_manifest(lab_dirs, {SID: {"instrument": "ES",
                                     "csv": "ListaDeOperaciones/x.csv"}})
    cache_p = _write_cache(lab_dirs, _rows())

    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in (manifest_p, cache_p)}
    r = await client.get(f"/ui/strategies/{SID}")
    assert r.status_code == 200
    for p, (data, mtime) in before.items():
        assert p.read_bytes() == data, f"{p.name} cambió de contenido"
        assert p.stat().st_mtime_ns == mtime, f"{p.name} cambió de mtime"
    assert sorted(x.name for x in (lab_dirs / "REPORTES").iterdir()) == \
        ["lab_features_" + SID + ".json", "lab_manifest.json"]
