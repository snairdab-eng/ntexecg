"""LX-15-JS — Aplicar-lo-que-ves + C1 móvil: la ruta de escritura que activa el cable.

Testea el WIRING de la ruta `/luxy/aplicar_palancas` (evaluate monkeypatcheado para
aislar la ruta del master real) + el E2E del cable (config aplicada → build_scaled →
C1 LÍMITE al precio absoluto y al tick) + gate ámbar en el AuditLog + R-T10.
"""
import json
import uuid
from datetime import datetime, timezone
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
from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.filter_pipeline import PipelineResult
from app.services.payload_builder import PayloadBuilder

_SID = "ES5m_JS"
_CLAVE = "ES_JS"
_OVERRIDES = {"sl_usd": 4500.0, "tp_usd": 3200.0, "dir": "both",
              "l1_usd": 400.0, "l2_usd": 656.0, "l3_usd": 1312.0}  # l1>0 → C1 móvil


def _study():
    return {"fecha": "2026-07-16", "degradado": False, "cancel_after_s": 3600,
            "dashboard": {}, "contencion": {"confiable": True, "pct": 100.0}}


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lx15js")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def motor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    base = tmp_path / "MotorRiesgo" / _CLAVE
    (base / "runs").mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text("{}", encoding="utf-8")
    (base / "runs" / "luxy_2026-07-16.json").write_text(
        json.dumps(_study()), encoding="utf-8")
    return tmp_path


def _fake_eval(overrides):
    """Resultado controlado de evaluate_overrides: aplicable REAL desde las
    palancas (config_from_overrides), señales verdes (aísla el C1-amber del gate)."""
    aplicable = mrl.config_from_overrides(overrides, atr_med=8.0, ppt=50.0,
                                          alloc=[5, 3, 2], cancel_after_s=3600)
    return {
        "validado": True, "aplicable": aplicable,
        "señales": {"robustez": "verde", "implausible": False, "flip_signo": False,
                    "mejora_3x": False, "participacion_pct": 88.0},
        "base": {"net": 1000.0, "part": 100.0},
        "config": {"net": 1400.0, "part": 88.0},
        "oos": {"net": 500.0, "part": 90.0, "pf": 1.4},
        "robustez": {"verdict": "verde", "pf": 1.4, "n": 20},
        "retencion": {},
    }


async def _seed(db: AsyncSession):
    db.add(Strategy(strategy_id=_SID, name="JS", asset_symbol="ES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=_SID, mode="paper", dry_run=True,
                           traderspost_enabled=False,
                           pipeline_config_json={"scale_entry": {
                               "mode": "design_only", "quantities": [1],
                               "levels": []}}))
    await db.commit()


def _patch_eval(monkeypatch):
    monkeypatch.setattr(mrl, "evaluate_overrides",
                        lambda clave, motor_dir, overrides, **kw: _fake_eval(overrides))


# ---------------------------------------------------------------------------
# 1) Preview: evidencia OOS de ESTAS palancas + gate ÁMBAR forzado por C1>0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preview_palancas_gate_ambar_por_c1(
        client, motor, db, monkeypatch):
    await _seed(db)
    _patch_eval(monkeypatch)
    r = await client.post(f"/ui/strategies/{_SID}/luxy/aplicar_palancas/preview",
                          json=_OVERRIDES)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["aplicable"]["scale_entry"]["c1_depth_atr"] == 1.0   # 400/50/8
    assert j["gate"]["nivel"] == "amber"                          # C1>0 fuerza ámbar
    assert any("C1 móvil" in t for t in j["gate"]["triggers"])
    assert j["evidencia"]["robustez"]["verdict"] == "verde"       # OOS espejo (R-T10)


# ---------------------------------------------------------------------------
# 2) Apply: gate ámbar BLOQUEA sin confirmar; con confirm escribe + audita
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_bloqueado_sin_confirmar_ambar(client, motor, db, monkeypatch):
    await _seed(db)
    _patch_eval(monkeypatch)
    r = await client.post(f"/ui/strategies/{_SID}/luxy/aplicar_palancas",
                          json={"overrides": _OVERRIDES})       # sin confirm_riesgo
    assert r.status_code == 400 and "ÁMBAR" in r.json()["error"]


@pytest.mark.asyncio
async def test_apply_palancas_escribe_c1_depth_y_audita(client, motor, db, monkeypatch):
    await _seed(db)
    _patch_eval(monkeypatch)
    r = await client.post(f"/ui/strategies/{_SID}/luxy/aplicar_palancas",
                          json={"overrides": _OVERRIDES, "confirm_riesgo": True})
    assert r.status_code == 200, r.text

    db.expire_all()
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == _SID))).scalar_one()
    cfg = prof.pipeline_config_json
    # LA RUTA DE ESCRITURA: config viva gana c1_depth_atr (activa el cable)
    assert cfg["scale_entry"]["c1_depth_atr"] == 1.0
    assert cfg["backstop_points"] == 90.0                        # 4500/50
    # kill-switch intacto
    assert prof.dry_run is True and prof.traderspost_enabled is False
    # AuditLog origen "luxy_aplicar_palancas" con gate ámbar + palancas + evidencia
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "APPLY_LUXY_PALANCAS"))).scalars().first()
    assert audit is not None and audit.actor == "luxy_aplicar_palancas"
    nv = audit.new_value_json
    assert nv["_gate_lx11"]["nivel"] == "amber"
    assert nv["_palancas"]["l1_usd"] == 400.0
    assert nv["_evidencia_oos"]["robustez"]["verdict"] == "verde"


# ---------------------------------------------------------------------------
# 3) E2E DEL CABLE — config aplicada → señal → payload C1 LÍMITE absoluto y al tick
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_cable_config_aplicada_despacha_c1_limite(
        client, motor, db, monkeypatch):
    await _seed(db)
    _patch_eval(monkeypatch)
    await client.post(f"/ui/strategies/{_SID}/luxy/aplicar_palancas",
                      json={"overrides": _OVERRIDES, "confirm_riesgo": True})
    db.expire_all()
    cfg = dict((await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == _SID))).scalar_one().pipeline_config_json)
    cfg["scale_entry"]["mode"] = "execute"     # armar despacho (NX-11 preserva mode)
    cfg["tick_size"] = 0.25

    sig = NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id=_SID, ticker_received="ES",
        mapped_symbol="ESU2025", action="buy", sentiment="long",
        signal_role="entry_long", price=5000.0, quantity=10,
        signal_ts=datetime.now(timezone.utc), dedupe_key=uuid.uuid4().hex)
    sig.id = uuid.uuid4()
    pr = PipelineResult(outcome="APPROVE", score=100, sl_price=4990.0,
                        tp_price=5010.0, atr_value=8.0, market_data_provider="M")
    legs = PayloadBuilder().build_scaled(sig, None, cfg, pr)
    # C1 (leg 1) despacha como LÍMITE a P0 − c1_depth×ATR = 5000 − 1.0×8 = 4992, al tick
    assert legs[0]["orderType"] == "limit"
    assert legs[0]["limitPrice"] == 4992.0


# ---------------------------------------------------------------------------
# 4) R-T10 adversarial — lo aplicado viene de las PALANCAS, jamás de la fila OOS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rt10_aplica_palancas_no_oos(client, motor, db, monkeypatch):
    await _seed(db)
    # eval con OOS venenoso (backstop 999) — jamás debe entrar a la config
    def _venenoso(clave, motor_dir, overrides, **kw):
        ev = _fake_eval(overrides)
        ev["oos"] = {"net": -1.0, "part": 10.0, "backstop_points": 999.0}
        return ev
    monkeypatch.setattr(mrl, "evaluate_overrides", _venenoso)
    r = await client.post(f"/ui/strategies/{_SID}/luxy/aplicar_palancas",
                          json={"overrides": _OVERRIDES, "confirm_riesgo": True})
    assert r.status_code == 200, r.text
    db.expire_all()
    cfg = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == _SID))).scalar_one().pipeline_config_json
    assert cfg["backstop_points"] == 90.0        # de las palancas (4500/50), NO 999
    assert "999" not in json.dumps(cfg)
