"""LX-8 — Puente de ventanas: compilador (zonas/días ON/OFF → ventanas L2) +
preview + aplicar supervisado. El compilador es puro y determinista; aplicar
escribe en el MISMO store que la pestaña Ventanas + AuditLog, sin tocar nada más.
"""
import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_riesgo as rr
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from scripts.sesiones_et import LUXY_ZONES, compilar_ventanas_l2

_Z = [z[0] for z in LUXY_ZONES]
_ALLON = {z: True for z in _Z}
# días presentes del estudio en %w: Dom(0), Lun..Vie (1..5)
_DIAS_W = {0: True, 1: True, 2: True, 3: True, 4: True, 5: True}


# ── compilador puro ─────────────────────────────────────────────────────────

def test_compilador_caso_asia_viernes_forma_exacta():
    z = dict(_ALLON); z["Asia"] = False          # Asia OFF
    d = dict(_DIAS_W); d[5] = False               # Viernes (%w 5) OFF
    assert compilar_ventanas_l2(z, d) == [
        {"days": [0, 1, 2, 3, 4], "start": "02:00", "end": "18:59"}]


def test_compilador_medianoche():
    z = {name: (name == "Asia") for name in _Z}   # solo Asia ON (19..01)
    out = compilar_ventanas_l2(z, {1: True, 2: True})
    assert out == [{"days": [1, 2], "start": "19:00", "end": "01:59",
                    "next_day_end": True}]


def test_compilador_huecos_multiples_ventanas():
    z = dict(_ALLON); z["Asia"] = False; z["Apertura US"] = False   # gap 08-09
    out = compilar_ventanas_l2(z, {1: True})
    assert out == [{"days": [1], "start": "02:00", "end": "07:59"},
                   {"days": [1], "start": "10:00", "end": "18:59"}]


def test_compilador_todo_on_es_7_por_7_completo():
    out = compilar_ventanas_l2(_ALLON, {d: True for d in range(7)})
    assert out == [{"days": [0, 1, 2, 3, 4, 5, 6],
                    "start": "00:00", "end": "23:59"}]


def test_compilador_todo_off_es_invalido():
    assert compilar_ventanas_l2({z: False for z in _Z}, _DIAS_W) is None   # sin zonas
    assert compilar_ventanas_l2(_ALLON, {d: False for d in range(7)}) is None  # sin días


def test_compilador_determinista():
    z = dict(_ALLON); z["Asia"] = False
    assert compilar_ventanas_l2(z, dict(_DIAS_W)) == compilar_ventanas_l2(z, dict(_DIAS_W))


# ── preview + aplicar (con estudio Luxy sintético) ──────────────────────────

@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lx8")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


def _seed_study(motor_dir: Path, clave: str) -> None:
    runs = motor_dir / clave / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    study = {"fecha": "2026-07-12", "degradado": False, "dashboard": {
        "reco": {"days": [{"dow": d} for d in (0, 1, 2, 3, 4, 6)]},   # weekday()
        "trades": [{"hr": 10, "dow": 0, "long": True},
                   {"hr": 3, "dow": 1, "long": True},
                   {"hr": 20, "dow": 4, "long": False},   # viernes 20h → fuera
                   {"hr": 22, "dow": 0, "long": False}],  # asia → fuera
        "ventana_operacion": {"muestras": [[1, 600], [5, 1200], [1, 180]]}}}
    (runs / "luxy_2026-07-12.json").write_text(json.dumps(study), encoding="utf-8")


@pytest.mark.asyncio
async def test_aplicar_escribe_ventanas_y_audita(
    client: AsyncClient, db: AsyncSession, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    db.add(Strategy(strategy_id="ES5m_Vent8", name="V8", asset_symbol="ES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="ES5m_Vent8",
                           pipeline_config_json={"backstop_points": 90.0}))
    await db.commit()
    _seed_study(rr.MOTOR_DIR, rr.clave_de("ES5m_Vent8", "ES"))

    body = {"zones_off": ["Asia"], "days_off": [4]}    # Asia + Viernes OFF
    # preview: costo en la cara + propuestas + avisos
    pv = await client.post("/ui/strategies/ES5m_Vent8/luxy/ventanas/preview",
                           json=body)
    assert pv.status_code == 200
    pj = pv.json()
    assert pj["propuestas"] == [{"days": [0, 1, 2, 3, 4],
                                 "start": "02:00", "end": "18:59"}]
    assert pj["pct_fuera_propuesta"] is not None
    assert pj["por_lado"]["short_fuera"] >= 1          # los 20h/22h caen fuera
    assert any("NO aporta edge" in a for a in pj["avisos"])
    assert any("LX-7" in a for a in pj["avisos"])

    # aplicar: escribe en pipeline_config_json.windows (mismo store) + audit
    ap = await client.post("/ui/strategies/ES5m_Vent8/luxy/ventanas/aplicar",
                           json=body)
    assert ap.status_code == 200 and ap.json()["ok"] is True
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "ES5m_Vent8"))).scalar_one()
    await db.refresh(prof)
    cfg = prof.pipeline_config_json
    assert cfg["windows"] == [{"days": [0, 1, 2, 3, 4],
                               "start": "02:00", "end": "18:59"}]
    # LX-8 #4 — NO toca nada más del pipeline_config
    assert cfg["backstop_points"] == 90.0
    assert set(cfg) == {"backstop_points", "windows"}
    # AuditLog APPLY_LUXY_VENTANAS con antes/después
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "APPLY_LUXY_VENTANAS"))).scalars().first()
    assert audit is not None
    assert audit.new_value_json["windows"] == cfg["windows"]
    assert audit.old_value_json["windows"] is None     # no había ventanas antes
    # LX-8 #5 — el editor canónico (pestaña Ventanas) usa el MISMO store y
    # formato: su propio endpoint round-trip-ea las ventanas compiladas idénticas.
    r2 = await client.post("/ui/strategies/ES5m_Vent8/windows",
                           data={"windows_json": json.dumps(cfg["windows"])})
    assert r2.status_code == 303
    prof2 = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "ES5m_Vent8"))).scalar_one()
    await db.refresh(prof2)
    assert prof2.pipeline_config_json["windows"] == cfg["windows"]


@pytest.mark.asyncio
async def test_aplicar_todo_off_rechaza(
    client: AsyncClient, db: AsyncSession, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    db.add(Strategy(strategy_id="ES5m_V8b", name="V8b", asset_symbol="ES",
                    status="paper", enabled=True))
    await db.commit()
    _seed_study(rr.MOTOR_DIR, rr.clave_de("ES5m_V8b", "ES"))
    off_all = {"zones_off": _Z, "days_off": [0, 1, 2, 3, 4, 5, 6]}
    ap = await client.post("/ui/strategies/ES5m_V8b/luxy/ventanas/aplicar",
                           json=off_all)
    assert ap.status_code == 400 and "vacía" in ap.json()["error"]
