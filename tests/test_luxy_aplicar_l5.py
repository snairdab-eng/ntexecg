"""LOTE L5 — Aplicar supervisado desde Luxy (reusa el Puente). Config aplicable
de la fila IN-SAMPLE (R-T10); BE informativo (no se escribe); mismas garantías
del Puente (kill-switch intacto, NX-11, AuditLog origen luxy_aplicar)."""
import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_riesgo as rr
import scripts.mr_luxy as mrl
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile


def _study(fecha="2026-07-11", be=None):
    """Estudio Luxy con IN-SAMPLE ≠ OOS (para el adversarial R-T10)."""
    return {
        "fecha": fecha, "degradado": False, "cancel_after_s": 3600,
        "levers_in_sample": {
            "backstop_usd": 4000.0, "b_pts": 80.0,
            "tp_por_lado_atr": {"long": 8.0, "short": 6.5},
            "ladder": {"alloc": [5, 3, 2], "levels": [0.0, 8.0, 16.0]},
            "breakeven": {"be_atr": be, "mejora_usd": 1000 if be else None},
        },
        "levers_oos": {                       # OOS distinto — NUNCA aplicable
            "backstop_usd": 10000.0, "b_pts": 200.0,
            "tp_por_lado_atr": {"long": 99.0, "short": 99.0},
            "ladder": {"alloc": [10, 0, 0], "levels": [0.0, 0.0, 0.0]},
            "breakeven": {"be_atr": None},
        },
    }


# ---------------------------------------------------------------------------
# Unidad — R-T10: SOLO in-sample; BE informativo (no se mapea)
# ---------------------------------------------------------------------------

def test_activacion_solo_in_sample_rt10():
    act = mrl.activacion_from_study(_study())
    assert act["backstop_points"] == 80.0            # in-sample
    assert act["tp_nominal_long"] == 8.0 and act["tp_nominal_short"] == 6.5
    assert act["scale_entry"]["quantities"] == [5, 3, 2]
    assert act["scale_entry"]["levels"] == [8.0, 16.0]
    assert act["entry_reserve_timeout_seconds"] == 3600
    # la ventana OOS JAMÁS entra (backstop 200 / tp 99 no aparecen)
    assert act["backstop_points"] != 200.0
    assert act["tp_nominal_long"] != 99.0


def test_activacion_be_no_se_mapea():
    st = _study(be=2.1)
    act = mrl.activacion_from_study(st)
    assert "be" not in json.dumps(act).lower() or "breakeven" not in act
    assert "be_atr" not in act                        # el BE NO se aplica
    assert mrl.breakeven_informativo(st) == {"be_atr": 2.1, "mejora_usd": 1000}
    assert mrl.breakeven_informativo(_study(be=None)) is None


# ---------------------------------------------------------------------------
# Web — mismas garantías del Puente
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_l5")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def motor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    return tmp_path


async def _seed(db: AsyncSession, motor: Path, be=None,
                mode="design_only") -> None:
    db.add(Strategy(strategy_id="ES5m_Ap", name="Ap", asset_symbol="ES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="ES5m_Ap", mode="paper",
                           dry_run=True, traderspost_enabled=False,
                           pipeline_config_json={
                               "scale_entry": {"mode": mode, "quantities": [1],
                                               "levels": []}}))
    await db.commit()
    runs = motor / "MotorRiesgo" / "ES_Ap" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "luxy_2026-07-11.json").write_text(
        json.dumps(_study(be=be)), encoding="utf-8")


@pytest.mark.asyncio
async def test_preview_muestra_diff_y_be_informativo(
    client: AsyncClient, motor: Path, db: AsyncSession
) -> None:
    await _seed(db, motor, be=2.1)
    r = await client.get("/ui/strategies/ES5m_Ap/luxy/aplicar/preview")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["fuente"] == "luxy" and j["filas"]
    # BE recomendado → aparece informativo (no en filas aplicables)
    assert j["informativos"] and "no aplicable" in j["informativos"][0]["nota"]
    assert any("R-T10" in a for a in j["avisos"])


@pytest.mark.asyncio
async def test_aplicar_luxy_garantias_puente(
    client: AsyncClient, motor: Path, db: AsyncSession
) -> None:
    await _seed(db, motor, be=2.1, mode="design_only")
    r = await client.post("/ui/strategies/ES5m_Ap/luxy/aplicar")
    assert r.status_code == 200, r.text

    db.expire_all()
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "ES5m_Ap"))).scalar_one()
    cfg = prof.pipeline_config_json
    # config IN-SAMPLE aplicada (R-T10: NO la OOS 200/99)
    assert cfg["backstop_points"] == 80.0
    assert cfg["tp_nominal_long"] == 8.0
    assert cfg["scale_entry"]["quantities"] == [5, 3, 2]
    # NX-11 — el mode del scale_entry se PRESERVA (design_only, no execute)
    assert cfg["scale_entry"]["mode"] == "design_only"
    # BE NO se escribió (palanca no aplicable)
    assert "be_atr" not in cfg and "breakeven" not in cfg
    # kill-switch intacto (el merge no toca dry_run/traderspost/status)
    assert prof.dry_run is True and prof.traderspost_enabled is False
    # AuditLog con origen luxy_aplicar
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "APPLY_LUXY_RECO"))).scalars().first()
    assert audit is not None and audit.actor == "luxy_aplicar"


@pytest.mark.asyncio
async def test_aplicar_sin_estudio_409(
    client: AsyncClient, motor: Path, db: AsyncSession
) -> None:
    db.add(Strategy(strategy_id="NoStudy", name="x", asset_symbol="ES",
                    status="paper", enabled=True))
    await db.commit()
    r = await client.get("/ui/strategies/NoStudy/luxy/aplicar/preview")
    assert r.status_code == 409
    assert "sin estudio" in r.json()["error"]
